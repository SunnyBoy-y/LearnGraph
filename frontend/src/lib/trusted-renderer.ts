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
 * the protocol `renderer.unlock` / `renderer.state` handshakes to the inert
 * iframe — it never relaxes CSP, never grants same-origin, never reads iframe
 * DOM, and never treats an iframe message as a host instruction.
 */

export interface TrustedRendererEnvelope {
  channel: string
  protocol_version: string
  sealed: boolean
  token?: string
  token_id?: string
  unlock_message?: Record<string, unknown>
  /** Server-provided verbatim `renderer.state` message (mirrors unlock_message). */
  state_message?: Record<string, unknown>
  /** Server-approved raw state payload; rendererStateMessage wraps it when state_message is absent. */
  state?: Record<string, unknown>
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
 * The protocol `renderer.state` message to post into the subapp iframe.
 *
 * Mirrors the `unlock_message` envelope pattern: the server's sealed envelope
 * may carry a verbatim `state_message` (preferred, host never fabricates a
 * token-bearing payload), or a server-approved `state` payload that this seam
 * wraps into a `renderer.state` handshake. Returns null when there is no sealed
 * envelope or no state to deliver.
 *
 * `renderer.state` is host→iframe only. The server rejects it inbound (same
 * branch as unlock), so this is a construction/read seam — T2.6 wires the actual
 * dispatch into the subapp runtime.
 */
export function rendererStateMessage(
  data: Record<string, unknown>,
): Record<string, unknown> | null {
  const envelope = trustedRendererEnvelope(data)
  if (!envelope) return null
  const verbatim = asRecord(envelope.state_message)
  if (verbatim) return verbatim
  const state = asRecord(envelope.state)
  if (!state) return null
  return {
    version:
      typeof envelope.protocol_version === 'string' && envelope.protocol_version !== ''
        ? envelope.protocol_version
        : '1',
    event_type: 'renderer.state',
    payload: state,
  }
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

/**
 * Post a `renderer.state` message into the subapp iframe. Pairs with
 * `postRendererUnlock` and uses the identical `targetOrigin '*'` — the only
 * option for an opaque-origin iframe. Never throws, so a forward seam cannot
 * break the artifact render. T2.6 wires actual dispatch; this is the seam.
 */
export function postRendererState(
  iframe: HTMLIFrameElement | null,
  message: Record<string, unknown> | null,
): void {
  if (!iframe || !message) return
  try {
    iframe.contentWindow?.postMessage(message, '*')
  } catch {
    // Inert opaque iframe / jsdom: there is no receiver, and the state push is
    // a forward seam — never let a post failure break the artifact render.
  }
}

/**
 * Post a `component.event.ack` message into the subapp iframe so the client SDK
 * can resolve its pending `emit()` promise. `persisted` proves the event reached
 * the backend; `rejected`/`retrying` keep the page honest (no fake "submitted").
 * Same `targetOrigin '*'` and never-throw semantics as the other seams.
 */
export function postRendererAck(
  iframe: HTMLIFrameElement | null,
  message: Record<string, unknown> | null,
): void {
  if (!iframe || !message) return
  try {
    iframe.contentWindow?.postMessage(message, '*')
  } catch {
    // Inert opaque iframe / jsdom: no receiver; never break the artifact render.
  }
}
