/**
 * LearnGraph browser-sandbox runtime bridge — host side.
 *
 * The browser sandbox (MagicCard / HTML preview) renders inside an
 * opaque-origin iframe with no direct network access. Its injected `__lg`
 * shim (see `sandbox-runtime-shim.ts` / preview gateway `runtime-shim.js`)
 * relays two kinds of request over postMessage:
 *
 * - `vfs.read`   → host reads a bundle file through the authenticated main
 *                  API (`GET /subapps/bundles/{id}/files/{path}`) and returns
 *                  the bytes — the multi-file virtual filesystem channel.
 * - `net.fetch`  → host relays the request to the approval-free proxy gateway
 *                  (`POST /sandbox-net/proxy`) which hard-guards SSRF,
 *                  credentials, size and timeout, and audits every call.
 *
 * Security posture (mirrors `subapp-bridge.ts`):
 * - Only messages whose `event.source === iframe.contentWindow` are handled;
 *   the opaque origin makes this the only reliable provenance check.
 * - Only the bounded `lg:1` protocol messages are accepted; unknown kinds and
 *   oversized payloads are dropped.
 * - The host never forwards cookies or host session credentials to the proxy
 *   gateway (it sends its own bearer token to LearnGraph endpoints only).
 * - Relays never throw into the host UI; failures are answered to the iframe
 *   as `{ ok: false }` and logged.
 */

import { apiClient } from '@/api/client'

export interface SandboxRuntimeBridgeOptions {
  /** Bundle whose files back `vfs.read`; null disables VFS (network still works). */
  bundleId?: string | null
  /** Preview gateway URL for the bundle; used to derive the entry path for relative resolution. */
  bundlePreviewUrl?: string | null
  /** Absolute cap for a single VFS read (bytes). */
  maxVfsBytes?: number
  /** Absolute cap for a single relayed network response (bytes). */
  maxRelayBytes?: number
}

export interface SandboxRuntimeBridge {
  /** Stop listening to the sandbox iframe's message channel. Idempotent. */
  destroy(): void
}

interface RelayEnvelope {
  lg?: unknown
  kind?: unknown
  id?: unknown
  [key: string]: unknown
}

export const SANDBOX_RUNTIME_MAX_VFS_BYTES = 8 * 1024 * 1024
export const SANDBOX_RUNTIME_MAX_RELAY_BYTES = 1 * 1024 * 1024
const MAX_INBOUND_MESSAGE_BYTES = 256 * 1024

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function envelopeValid(data: RelayEnvelope): data is RelayEnvelope & { id: number } {
  return (
    data.lg === 1 &&
    typeof data.id === 'number' &&
    Number.isInteger(data.id) &&
    data.id > 0 &&
    data.id < 1_000_000_000 &&
    (data.kind === 'vfs.read' || data.kind === 'net.fetch')
  )
}

function boundedSize(value: unknown): boolean {
  if (value === undefined || value === null) return true
  if (typeof value !== 'string') return false
  return new TextEncoder().encode(value).byteLength <= MAX_INBOUND_MESSAGE_BYTES
}

/** Derive the bundle entry path from its preview URL. */
export function entryPathFromPreviewUrl(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const parsed = new URL(url)
    const segments = parsed.pathname.split('/').filter(Boolean)
    // /api/v1/subapps/preview/{token}/{bundle_id}/{entry...}
    const previewIndex = segments.indexOf('preview')
    if (previewIndex === -1 || segments.length <= previewIndex + 3) return null
    const entry = segments.slice(previewIndex + 3).join('/')
    return entry || null
  } catch {
    return null
  }
}

/** Resolve a sandbox-relative path against the bundle entry document. */
export function resolveBundlePath(relative: string, entryPath: string): string {
  const base = `http://bundle.local/${entryPath}`
  try {
    const resolved = new URL(relative, base)
    return resolved.pathname.replace(/^\/+/, '')
  } catch {
    return relative.replace(/^\.?\//, '')
  }
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk))
  }
  return btoa(binary)
}

function responseToResult(response: {
  status: number
  status_text?: string | null
  content_type?: string | null
  body_base64?: string | null
  size_bytes?: number | null
}): Record<string, unknown> {
  return {
    status: response.status,
    statusText: response.status_text ?? '',
    headers: response.content_type ? { 'content-type': response.content_type } : {},
    bodyBase64: response.body_base64 ?? null,
  }
}

/**
 * Create a host-relay bridge bound to an opaque-origin sandbox iframe.
 *
 * Returns `{ destroy() }` so hosts can mount/unmount it with the artifact's
 * lifecycle. A null iframe yields a no-op bridge (messages are ignored), so
 * the mount may happen before the iframe ref resolves.
 */
export function createSandboxRuntimeBridge(
  iframe: HTMLIFrameElement | null,
  options: SandboxRuntimeBridgeOptions = {},
): SandboxRuntimeBridge {
  const bundleId = options.bundleId ?? null
  const entryPath = entryPathFromPreviewUrl(options.bundlePreviewUrl)
  const maxVfsBytes = options.maxVfsBytes ?? SANDBOX_RUNTIME_MAX_VFS_BYTES
  const maxRelayBytes = options.maxRelayBytes ?? SANDBOX_RUNTIME_MAX_RELAY_BYTES
  let destroyed = false

  function postResult(id: number, result: Record<string, unknown>): void {
    if (destroyed || !iframe) return
    try {
      iframe.contentWindow?.postMessage({ lg: 1, kind: 'lg.result', id, ...result }, '*')
    } catch {
      // Opaque iframe may be gone; never let a post failure break the host.
    }
  }

  async function handleVfsRead(message: RelayEnvelope & { id: number }): Promise<void> {
    if (!bundleId) {
      postResult(message.id, { ok: false, error: 'vfs unavailable: no bundle context' })
      return
    }
    const rawPath = typeof message.path === 'string' ? message.path : ''
    if (!rawPath || !boundedSize(rawPath)) {
      postResult(message.id, { ok: false, error: 'invalid vfs path' })
      return
    }
    const normalized = resolveBundlePath(rawPath, entryPath ?? 'index.html')
    try {
      const blob = await apiClient.getBlob(
        `/subapps/bundles/${encodeURIComponent(bundleId)}/files/${normalized
          .split('/')
          .map(encodeURIComponent)
          .join('/')}`,
      )
      if (blob.size > maxVfsBytes) {
        postResult(message.id, { ok: false, error: 'vfs file exceeds size limit' })
        return
      }
      const buffer = await blob.arrayBuffer()
      const type = blob.type || 'application/octet-stream'
      postResult(message.id, {
        ok: true,
        status: 200,
        statusText: 'OK',
        headers: { 'content-type': type },
        bodyBase64: arrayBufferToBase64(buffer),
      })
    } catch (error) {
      postResult(message.id, {
        ok: false,
        error: error instanceof Error ? `vfs read failed: ${error.message}` : 'vfs read failed',
      })
    }
  }

  async function handleNetFetch(message: RelayEnvelope & { id: number }): Promise<void> {
    const url = typeof message.url === 'string' ? message.url : ''
    const method = typeof message.method === 'string' ? message.method.toUpperCase() : 'GET'
    const headers =
      asRecord(message.headers) ?? {}
    const body = typeof message.body === 'string' ? message.body : null
    if (!url || !boundedSize(url)) {
      postResult(message.id, { ok: false, error: 'invalid network url' })
      return
    }
    try {
      const result = await apiClient.post<
        {
          status: number
          status_text?: string | null
          content_type?: string | null
          body_base64?: string | null
          size_bytes?: number | null
        },
        {
          url: string
          method: string
          headers: Record<string, unknown>
          body: string | null
        }
      >('/sandbox-net/proxy', { url, method, headers, body })
      const size = typeof result?.size_bytes === 'number' ? result.size_bytes : 0
      if (size > maxRelayBytes) {
        postResult(message.id, { ok: false, error: 'network response exceeds size limit' })
        return
      }
      postResult(message.id, { ok: true, ...responseToResult(result ?? { status: 502 }) })
    } catch (error) {
      postResult(message.id, {
        ok: false,
        error: error instanceof Error ? `network relay failed: ${error.message}` : 'network relay failed',
      })
    }
  }

  async function onMessage(event: MessageEvent): Promise<void> {
    if (destroyed || !iframe || event.source !== iframe.contentWindow) return
    const data = asRecord(event.data) as RelayEnvelope | null
    if (!data || !envelopeValid(data)) return
    if (!boundedSize(data.path) || !boundedSize(data.url) || !boundedSize(data.body)) return
    if (data.kind === 'vfs.read') {
      await handleVfsRead(data)
    } else if (data.kind === 'net.fetch') {
      await handleNetFetch(data)
    }
  }

  window.addEventListener('message', onMessage)

  return {
    destroy(): void {
      if (destroyed) return
      destroyed = true
      window.removeEventListener('message', onMessage)
    },
  }
}
