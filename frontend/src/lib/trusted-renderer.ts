/**
 * P2-A trusted renderer channel — frontend consumer helpers.
 *
 * The server seals a short-lived, audience-bound capability token inside the
 * component artifact when (and only when) the component is fully eligible for
 * the trusted renderer: registered active issuer + verified signature + matching
 * package hash + passed renderer health + workspace authorization. The host
 * consumes that server-side decision here and never self-asserts trust.
 *
 * The deliverable stays `sandbox_artifact` and renders through the existing
 * opaque-origin iframe (`sandbox="allow-scripts"`, NO `allow-same-origin`,
 * `connect-src 'none'`). This module only surfaces the trust decision and posts
 * the protocol `renderer.unlock` handshake to the inert iframe — it never relaxes
 * CSP, never grants same-origin, never reads iframe DOM, and never treats an
 * iframe message as a host instruction.
 */

export interface TrustedRendererEnvelope {
  channel: string
  protocol_version: string
  sealed: boolean
  token?: string
  token_id?: string
  unlock_message?: Record<string, unknown>
  iframe_boundary?: Record<string, unknown>
  [key: string]: unknown
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

/** The sealed envelope, or null when the server did not mark this artifact trusted. */
export function trustedRendererEnvelope(
  data: Record<string, unknown>,
): TrustedRendererEnvelope | null {
  const envelope = asRecord(data.trusted_renderer)
  return envelope ? (envelope as TrustedRendererEnvelope) : null
}

/**
 * True only when BOTH the server flagged `trusted_renderer_eligible` AND a
 * sealed envelope is present. An eligibility flag without its token envelope is
 * never treated as trusted.
 */
export function trustedRendererEligible(data: Record<string, unknown>): boolean {
  return data.trusted_renderer_eligible === true && trustedRendererEnvelope(data) !== null
}

/** Server-provided downgrade/eligibility reason for audit-visible UI text. */
export function trustedRendererReason(data: Record<string, unknown>): string {
  return typeof data.trusted_renderer_reason === 'string'
    ? data.trusted_renderer_reason
    : ''
}

/**
 * The protocol `renderer.unlock` message to post into the inert iframe.
 * Built server-side and carried verbatim on the envelope; the host never
 * constructs a token-bearing message itself. Returns null when not eligible.
 */
export function rendererUnlockMessage(
  data: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!trustedRendererEligible(data)) return null
  const message = asRecord(trustedRendererEnvelope(data)?.unlock_message)
  return message
}

/**
 * Post the unlock handshake into the sandboxed iframe (forward-compatible seam).
 * The iframe content is server-owned and inert (`script-src 'none'`), so the
 * message has no receiver today; a future trusted renderer runtime reads it.
 * `targetOrigin '*'` is the only option for an opaque-origin iframe.
 */
export function postRendererUnlock(
  iframe: HTMLIFrameElement | null,
  message: Record<string, unknown> | null,
): void {
  if (!iframe || !message) return
  try {
    iframe.contentWindow?.postMessage(message, '*')
  } catch {
    // Inert opaque iframe / jsdom: there is no receiver, and the handshake is
    // a forward seam — never let a post failure break the artifact render.
  }
}
