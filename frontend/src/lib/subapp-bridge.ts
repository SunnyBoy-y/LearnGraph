/**
 * T1.3 / T2.6 host-relay bridge + session channel for opaque-origin subapp iframes.
 *
 * The subapp iframe renders inside an opaque-origin sandbox (`sandbox="allow-scripts"`,
 * NO `allow-same-origin`, `connect-src 'none'`), so it cannot reach the host API
 * directly — postMessage is the only channel that requires no network permission.
 * This module relays trusted-renderer protocol `component.event` messages to the
 * backend:
 *
 * - P1 (T1.3): workspace-scoped ingest endpoint `POST /api/v1/subapps/events`
 *   with the host's session identity attached.
 * - P2 (T2.6): session-scoped endpoint `POST /api/v1/subapps/sessions/{id}/events`
 *   with the rotating session capability token. `createSubappChannel` drives the
 *   full bidirectional loop: instantiate a session, unlock the iframe, push
 *   `renderer.state` updates, poll for newer state versions, and tear down.
 *
 * Security posture (aligned with the design's §9.9 invariants):
 * - Only messages whose `event.source === iframe.contentWindow` are considered;
 *   the opaque origin makes this the only reliable provenance check available.
 * - Only the `component.event` protocol message is relayed. Host→iframe messages
 *   (`renderer.unlock` / `renderer.state`) are never accepted inbound.
 * - The business `event_type` must match the backend's bounded pattern, and the
 *   payload must be a plain, finite-JSON, size-bounded object before forwarding.
 * - The capability token is NEVER placed inside host→iframe `renderer.state`
 *   messages (only the one-time `renderer.unlock` carries it). It travels
 *   iframe→host inside `component.event` and is presented by the host relay from
 *   its own rotating store — the host never trusts the iframe's claimed token
 *   and never fabricates one (§9.9 #3).
 * - Relays never throw. Invalid/oversized events are dropped with a console
 *   hint; network failures are logged and swallowed. An iframe can never crash
 *   the host UI or force an unvalidated payload into an endpoint.
 */

import { apiClient } from '@/api/client'
import { downloadFile } from '@/api/files'
import { postRendererAck, postRendererState, postRendererUnlock } from '@/lib/trusted-renderer'
import {
  parseSubappMediaRefs,
  postRendererMedia,
  resolveSubappMedia,
  subappMediaMessage,
} from '@/lib/subapp-media'
import type { SubappMediaAsset, SubappMediaRef } from '@/lib/subapp-media'

/** Mirrors backend `MAX_SUBAPP_EVENT_PAYLOAD_BYTES` (16 KiB). */
export const SUBAPP_EVENT_MAX_PAYLOAD_BYTES = 16 * 1024

/** Mirrors backend `EVENT_TYPE_PATTERN` in domain/schemas/subapps.py. */
const EVENT_TYPE_PATTERN = /^[a-z][a-z0-9_.-]{0,119}$/

/** The trusted-renderer protocol discriminator for iframe→host events. */
const COMPONENT_EVENT_TYPE = 'component.event'

/** Bounded transient-failure retries for session event relays (host outbox). */
const SUBAPP_RELAY_MAX_ATTEMPTS = 3
const SUBAPP_RELAY_RETRY_DELAY_MS = 1500

// --------------------------------------------------------------------------- //
// T2.6 session channel tuning
// --------------------------------------------------------------------------- //

/** Polling cadence for new `renderer.state` versions (ms). */
export const SUBAPP_POLL_INTERVAL_MS = 2000

/**
 * Bounded polling budget. After this many consecutive polls without a state
 * advance the channel stops polling; a successful event relay resets the budget,
 * so an active session keeps watching while an idle one winds down. Bounded
 * polling + unmount teardown keeps the loop from running forever.
 */
export const SUBAPP_POLL_MAX_ATTEMPTS = 90

// --------------------------------------------------------------------------- //
// P3 media injection
// --------------------------------------------------------------------------- //

/**
 * Host-side media resolver signature (P3). `state` is the state snapshot payload
 * (`renderer.state` payload `state`), `refs` its shape-validated `media` array.
 * Returns validated byte assets to push as `renderer.media`, or [] when nothing
 * is injectable.
 */
export type SubappMediaResolver = (
  state: Record<string, unknown> | null | undefined,
  refs: SubappMediaRef[],
) => Promise<SubappMediaAsset[]>

/**
 * Media source locator scheme for the default resolver. `file:<fileId>` maps to
 * the existing authenticated files endpoint `GET /files/{id}/content` — the same
 * source AI image-generation results carry as `file_id`.
 */
const MEDIA_SOURCE_FILE_PREFIX = 'file:'

/** Strict file-id charset matches the backend `new_id()` (uuid4 hex + dashes). */
const FILE_ID_PATTERN = /^[A-Za-z0-9_-]+$/

async function fetchFileMediaSource(source: string): Promise<Blob | null> {
  if (!source.startsWith(MEDIA_SOURCE_FILE_PREFIX)) return null
  const fileId = source.slice(MEDIA_SOURCE_FILE_PREFIX.length)
  if (!FILE_ID_PATTERN.test(fileId)) return null
  return downloadFile(fileId)
}

/**
 * Default P3 resolver: resolve a state snapshot's `media` refs via the files
 * endpoint. Unknown source schemes, missing files, oversized or disallowed-MIME
 * blobs are dropped by `resolveSubappMedia`. A state with no media refs yields
 * [] (the channel then clears the previous injection set).
 */
export async function defaultSubappMediaResolver(
  _state: Record<string, unknown> | null | undefined,
  refs: SubappMediaRef[],
): Promise<SubappMediaAsset[]> {
  return resolveSubappMedia(refs, fetchFileMediaSource)
}

// --------------------------------------------------------------------------- //
// Bridge
// --------------------------------------------------------------------------- //

export interface SubAppBridgeIdentity {
  sessionId?: string | null
  chatSessionId?: string | null
  artifactVersionId?: string | null
  /** T2.6: when present, relay `component.event` to the session-scoped endpoint. */
  session?: SubAppSessionRelay
}

/** T2.6 session-scoped relay config handed to the bridge. */
export interface SubAppSessionRelay {
  sessionId: string
  /** Current rotating session capability token, read at relay time. */
  getToken: () => string | null
  /** Fired after a 202 carries a freshly rotated token. */
  onAccepted?: (accepted: { sessionId: string; nextToken: string }) => void
  /** Fired when the server asks for a one-time Agent consent decision. */
  onConsentRequired?: (info: {
    pendingConsentId: string
    triggers: string[]
  }) => void
  /** Fired when the server accepted the event and queued an Agent turn. */
  onEventQueued?: (info: { eventId?: string | null; runId?: string | null }) => void
  /** Fired on a deterministic (4xx) rejection such as an invalid/stale token. */
  onRejected?: (error: unknown) => void
  /** Fired for every relay outcome so the host can ACK the iframe (no fake success). */
  onAck?: (ack: {
    clientEventId: string
    status: 'persisted' | 'rejected' | 'retrying'
    errorCode?: string | null
  }) => void
}

export interface SubAppBridge {
  /** Stop listening to the subapp iframe's message channel. Idempotent. */
  destroy(): void
}

interface ComponentEventMessage {
  version?: unknown
  event_type?: unknown
  payload?: unknown
  token?: unknown
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

/**
 * Validate a `component.event` message and extract the business event to relay.
 * Returns `{ eventType, payload }` or null when the message is not a valid
 * subapp event. The business `event_type` lives on `payload.type` per the
 * SDK protocol; `type` is routing metadata and is STRIPPED from the payload the
 * backend validates against `event_schema`, so app-authored schemas never need
 * to declare `type`. Client reliability fields (`client_event_id`,
 * `schema_version`, `occurred_at`) stay in the forwarded payload.
 */
function resolveComponentEvent(
  message: ComponentEventMessage,
): { eventType: string; payload: Record<string, unknown> } | null {
  if (message.event_type !== COMPONENT_EVENT_TYPE) return null
  const payload = asRecord(message.payload)
  if (!payload) return null
  const eventType = typeof payload.type === 'string' ? payload.type : ''
  if (!EVENT_TYPE_PATTERN.test(eventType)) return null
  const business = { ...payload }
  delete business.type
  return { eventType, payload: business }
}

/** True when the payload is not finite, size-bounded JSON suitable for the API. */
function payloadInvalid(payload: Record<string, unknown>): boolean {
  let json: string
  try {
    json = JSON.stringify(payload)
  } catch {
    return true // circular reference / non-serializable value
  }
  if (json === undefined) return true
  return new TextEncoder().encode(json).byteLength > SUBAPP_EVENT_MAX_PAYLOAD_BYTES
}

/** Duck-typed HTTP status from an apiClient ApiError, or null. */
function apiErrorStatus(error: unknown): number | null {
  if (error !== null && typeof error === 'object' && 'status' in error) {
    const status = (error as { status?: unknown }).status
    if (typeof status === 'number') return status
  }
  return null
}

/** P1 workspace-scoped ingest path (T1.3). */
async function relayIngestEvent(
  eventType: string,
  payload: Record<string, unknown>,
  identity: SubAppBridgeIdentity,
): Promise<void> {
  try {
    await apiClient.post<unknown, Record<string, unknown>>('/subapps/events', {
      session_id: identity.sessionId ?? null,
      chat_session_id: identity.chatSessionId ?? null,
      artifact_version_id: identity.artifactVersionId ?? null,
      event_type: eventType,
      payload,
    })
  } catch (error) {
    // P1 is best-effort host relay. Never surface an iframe-originated failure
    // in the host UI; log a hint and drop.
    console.warn('[subapp-bridge] failed to relay component.event', error)
  }
}

/**
 * T2.6 session-scoped relay: present the host's current rotating token and POST
 * the business event to `POST /subapps/sessions/{id}/events`. A 202 rotates the
 * token; a deterministic 4xx (stale token, session not active, budget) surfaces
 * via `onRejected`, while transient failures are retried with bounded backoff
 * (host outbox) and each outcome is ACKed back to the iframe via `onAck`.
 */
async function relaySessionEvent(
  eventType: string,
  payload: Record<string, unknown>,
  session: SubAppSessionRelay,
): Promise<void> {
  const clientEventId =
    typeof payload.client_event_id === 'string' ? payload.client_event_id : ''

  async function attempt(round: number): Promise<void> {
    const token = session.getToken()
    if (!token) return
    try {
      const accepted = await apiClient.post<
        SubappEventAcceptedResponse,
        { token: string; event_type: string; payload: Record<string, unknown> }
      >(`/subapps/sessions/${session.sessionId}/events`, {
        token,
        event_type: eventType,
        payload,
      })
      if (accepted?.next_token) {
        session.onAccepted?.({ sessionId: session.sessionId, nextToken: accepted.next_token })
      }
      const agent = accepted?.agent
      if (agent?.consent_required) {
        session.onConsentRequired?.({
          pendingConsentId: agent.pending_consent_id ?? '',
          triggers: [],
        })
      } else if (agent?.triggered) {
        session.onEventQueued?.({})
      }
      session.onAck?.({ clientEventId, status: 'persisted' })
    } catch (error) {
      const status = apiErrorStatus(error)
      if (typeof status === 'number' && status >= 400 && status < 500) {
        session.onAck?.({ clientEventId, status: 'rejected', errorCode: String(status) })
        session.onRejected?.(error)
        return
      }
      if (round < SUBAPP_RELAY_MAX_ATTEMPTS) {
        session.onAck?.({ clientEventId, status: 'retrying' })
        await new Promise((resolve) =>
          setTimeout(resolve, SUBAPP_RELAY_RETRY_DELAY_MS * 2 ** (round - 1)),
        )
        await attempt(round + 1)
      } else {
        session.onAck?.({
          clientEventId,
          status: 'rejected',
          errorCode: 'relay_timeout',
        })
        console.warn('[subapp-bridge] component.event relay failed after retries', error)
      }
    }
  }

  await attempt(1)
}

async function relayComponentEvent(
  data: unknown,
  identity: SubAppBridgeIdentity,
): Promise<void> {
  const message = asRecord(data) as ComponentEventMessage | null
  const resolved = message && resolveComponentEvent(message)
  if (!resolved) return

  if (payloadInvalid(resolved.payload)) {
    console.warn(
      '[subapp-bridge] dropped component.event: payload is not finite, size-bounded JSON',
      data,
    )
    return
  }

  if (identity.session) {
    await relaySessionEvent(resolved.eventType, resolved.payload, identity.session)
  } else {
    await relayIngestEvent(resolved.eventType, resolved.payload, identity)
  }
}

/**
 * Create a host-relay bridge bound to an opaque-origin subapp iframe.
 *
 * Returns `{ destroy() }` so T2.6 can mount/unmount it with the artifact's
 * lifecycle. A null iframe yields a no-op bridge (messages are ignored) so the
 * mount can happen before the iframe ref is resolved.
 *
 * Session relays are serialized through a promise chain: concurrent
 * `component.event`s cannot race the rotating token (a stale presentation would
 * otherwise be rejected server-side).
 */
export function createSubappBridge(
  iframe: HTMLIFrameElement | null,
  identity: SubAppBridgeIdentity = {},
): SubAppBridge {
  let destroyed = false
  let relayQueue: Promise<void> = Promise.resolve()

  function onMessage(event: MessageEvent): void {
    if (destroyed) return
    // Provenance: only the opaque-origin iframe we were bound to may drive the
    // relay. `event.source` is the only reliable check available for this.
    if (!iframe || event.source !== iframe.contentWindow) return
    relayQueue = relayQueue
      .then(() => relayComponentEvent(event.data, identity))
      .catch(() => {})
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

// --------------------------------------------------------------------------- //
// T2.6 session channel
// --------------------------------------------------------------------------- //

/** Response envelope from `POST /api/v1/subapps/sessions`. */
export interface SubappSessionCreated {
  session_id: string
  status: string
  state_version: number
  state_sha256: string | null
  token: string
  token_prefix: string
  component_id: string
  render_ref: string
  artifact_version_id: string | null
  chat_session_id: string | null
  event_schema: Record<string, unknown>
  state_schema: Record<string, unknown>
  unlock_message: Record<string, unknown>
}

/** Immutable, versioned state snapshot from `GET /sessions/{id}/states`. */
export interface SubappStateView {
  id: string
  session_id: string
  version: number
  sha256: string
  state: Record<string, unknown>
  created_at: string
}

export interface SubappStateList {
  items: SubappStateView[]
  offset: number
  limit: number
  total: number
}

/** 202 ack from `POST /sessions/{id}/events` carrying the rotated token. */
export interface SubappEventAcceptedResponse {
  accepted: true
  session_id: string
  event: Record<string, unknown>
  next_token: string
  next_token_prefix: string
  agent?: {
    consent_required?: boolean
    triggered?: boolean
    pending_consent_id?: string | null
    disabled?: boolean
    event_id?: string | null
    run_id?: string | null
    triggers?: string[]
  }
}

/**
 * How an artifact opts into subapp mode. `provisioned` is a server-side session
 * whose envelope (token included) already rides inside the artifact data;
 * `instantiate` asks the host to create a fresh session from a published version.
 */
export type SubappSessionTrigger =
  | { kind: 'instantiate'; artifactVersionId: string }
  | {
      kind: 'provisioned'
      sessionId: string
      token: string
      unlockMessage: Record<string, unknown>
    }

/**
 * Detect the subapp-mode marker in an artifact payload (§9.8). Returns null for
 * the plain static preview path so existing rendering is never disturbed.
 *
 * Two markers are recognized:
 * 1. `subapp_session_id` + an `unlock_message` envelope that embeds the raw
 *    session token (returned by the server exactly once). Without a usable token
 *    the iframe cannot be unlocked, so the marker alone is treated as broken.
 * 2. An interactive published version: a non-empty `artifact_version_id` combined
 *    with `subapp_mode === true` or an `interaction_contract` manifest, which
 *    makes the host instantiate a fresh session via `POST /subapps/sessions`.
 */
export function subappSessionTrigger(
  data: Record<string, unknown>,
): SubappSessionTrigger | null {
  const sessionId =
    typeof data.subapp_session_id === 'string' ? data.subapp_session_id.trim() : ''
  if (sessionId) {
    const unlock = asRecord(data.unlock_message)
    const unlockPayload = unlock ? asRecord(unlock.payload) : null
    const token =
      unlockPayload && typeof unlockPayload.token === 'string' ? unlockPayload.token : ''
    if (unlock && token) {
      return { kind: 'provisioned', sessionId, token, unlockMessage: unlock }
    }
    // The server never re-exposes the raw token, so a session id without its
    // envelope is a broken marker — fall through to the static preview.
    return null
  }
  const artifactVersionId =
    typeof data.artifact_version_id === 'string' ? data.artifact_version_id.trim() : ''
  const interactive = data.subapp_mode === true || asRecord(data.interaction_contract) !== null
  if (artifactVersionId && interactive) {
    return { kind: 'instantiate', artifactVersionId }
  }
  return null
}

/**
 * Build the protocol `renderer.state` message (§9.3) for one state snapshot.
 * The payload carries `{ state_version, state_sha256, state }` so the subapp
 * template can enforce monotonic `state_version` before rendering; the host
 * never places the capability token in this host→iframe message.
 */
export function subappStateMessage(
  state: SubappStateView | null | undefined,
): Record<string, unknown> | null {
  if (!state || typeof state.version !== 'number') return null
  const payload = asRecord(state.state)
  if (!payload) return null
  return {
    version: '1',
    event_type: 'renderer.state',
    payload: {
      state_version: state.version,
      state_sha256: typeof state.sha256 === 'string' ? state.sha256 : '',
      state: payload,
    },
  }
}

/** Human-readable text for the channel's failure codes. */
const SUBAPP_FAILURE_TEXT: Record<string, string> = {
  subapp_missing_version: '子应用缺少可实例化的产物版本。',
  subapp_session_invalid_envelope: '子应用会话信封无效，无法建立双向通道。',
  subapp_session_creation_failed: '子应用会话创建失败，双向通道不可用。',
  subapp_iframe_missing: '子应用 iframe 不可用。',
  subapp_session_rejected: '子应用会话已失效，事件通道已停止。',
}

export function subappFailureText(reason: string): string {
  return SUBAPP_FAILURE_TEXT[reason] ?? reason
}

interface SubappActiveSession {
  sessionId: string
  unlockMessage: Record<string, unknown>
}

export interface SubappChannelOptions {
  /** Returns the current subapp iframe (ref may be null until mounted). */
  getIframe: () => HTMLIFrameElement | null
  /** True once the subapp iframe has fired `onLoad` (survives StrictMode remounts). */
  getIframeLoaded?: () => boolean
  /** Published subapp version to instantiate (used when not `provisioned`). */
  artifactVersionId?: string
  chatSessionId?: string | null
  /** Server-pre-provisioned session envelope from the artifact data. */
  provisioned?: { sessionId: string; token: string; unlockMessage: Record<string, unknown> }
  /** Fired after a newer `renderer.state` version is pushed into the iframe. */
  onStatePushed?: (version: number) => void
  /** Fired when the server asks for a one-time Agent consent decision. */
  onConsentRequired?: (info: {
    pendingConsentId: string
    triggers: string[]
  }) => void
  /** Fired when an event-driven Agent turn was queued. */
  onEventQueued?: (info: { eventId?: string | null; runId?: string | null }) => void
  /** Fired when polling observes the session Agent status. */
  onAgentStatus?: (status: {
    agentStatus: 'idle' | 'queued' | 'processing' | 'failed'
    error?: string | null
  }) => void
  /**
   * P3 media injection: resolve a pushed state snapshot's `media` refs into
   * validated byte assets and post them as `renderer.media`. Defaults to
   * `defaultSubappMediaResolver` (files endpoint). Pass null to disable media
   * injection for this channel.
   */
  mediaResolver?: SubappMediaResolver | null
  /** Fired after a `renderer.media` batch is pushed into the iframe. */
  onMediaPushed?: (version: number, count: number) => void
  /** Fired when the channel fails (instantiation, invalid envelope, rejection). */
  onFailed?: (reason: string) => void
}

export interface SubappChannel {
  /** Called by the iframe's `onLoad` to start delivering the unlock + state. */
  handleIframeLoad(): void
  /** Tear down the bridge, timers, and the server-side session. Idempotent. */
  destroy(): void
  /** Current session id once established, else null. */
  sessionId(): string | null
  /** Apply an Agent consent decision. */
  decideConsent(
    decision: 'allow_session' | 'allow_app' | 'allow_global' | 'deny',
  ): Promise<boolean>
  /** Manually retry the latest failed/skipped Agent task. */
  retryAgentTask(): Promise<boolean>
  /** Cancel a processing Agent task. */
  cancelAgentTask(): Promise<boolean>
}

/**
 * T2.6 bidirectional channel (§9.2 / §9.8).
 *
 * Flow:
 *   instantiate/provision → on iframe load: post `renderer.unlock` (the only
 *   token-bearing host→iframe message) → best-effort initial `renderer.state` →
 *   host-relay bridge relays `component.event` to the session endpoint with the
 *   rotating token → poll `GET /sessions/{id}/states` for newer versions and
 *   push them into the iframe.
 *
 * Lifecycle: `destroy()` clears the poll timer, destroys the bridge, and best-
 * effort `POST /sessions/{id}/terminate`s so the server-side token is consumed
 * and no orphan session lingers. Trade-off made explicit: because the raw token
 * only exists in host memory, an unmounted then re-mounted artifact always
 * starts a fresh session.
 */
export function createSubappChannel(options: SubappChannelOptions): SubappChannel {
  let destroyed = false
  let envelope: SubappActiveSession | null = options.provisioned
    ? {
        sessionId: options.provisioned.sessionId,
        unlockMessage: options.provisioned.unlockMessage,
      }
    : null
  let currentToken: string | null = options.provisioned?.token ?? null
  let iframeLoaded = options.getIframeLoaded?.() ?? false
  let delivered = false
  let lastStateVersion = 0
  let pollTimer: number | null = null
  let pollAttempts = 0
  let bridge: SubAppBridge | null = null
  /** P3: whether a media set is currently injected (so a media-free state clears it). */
  let mediaActive = false

  function clearPolling(): void {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer)
      pollTimer = null
    }
  }

  /**
   * P3 media delivery. After a `renderer.state` push, resolve the snapshot's
   * `media` refs and post a `renderer.media` batch. Media is version-tagged:
   * if a newer state is delivered while a batch is resolving, the stale batch
   * is dropped. A state with no media refs clears the previous injected set so
   * memory does not linger across state generations.
   */
  async function deliverMedia(state: SubappStateView): Promise<void> {
    if (destroyed || !envelope) return
    const resolver =
      options.mediaResolver === undefined ? defaultSubappMediaResolver : options.mediaResolver
    if (!resolver) return
    const mediaVersion = state.version
    const refs = parseSubappMediaRefs(state.state)
    if (refs.length === 0) {
      if (mediaActive) {
        postRendererMedia(options.getIframe(), subappMediaMessage(mediaVersion, []))
        mediaActive = false
      }
      return
    }
    let assets: SubappMediaAsset[]
    try {
      assets = await resolver(state.state, refs)
    } catch (error) {
      console.warn('[subapp-bridge] media resolution failed', error)
      return
    }
    if (destroyed) return
    if (mediaVersion < lastStateVersion) return
    if (assets.length === 0) return
    postRendererMedia(options.getIframe(), subappMediaMessage(mediaVersion, assets))
    mediaActive = true
    options.onMediaPushed?.(mediaVersion, assets.length)
  }

  function teardownBridge(): void {
    bridge?.destroy()
    bridge = null
  }

  function fail(reason: string): void {
    if (destroyed) return
    clearPolling()
    teardownBridge()
    options.onFailed?.(reason)
  }

  async function instantiate(): Promise<void> {
    if (!options.artifactVersionId) {
      fail('subapp_missing_version')
      return
    }
    try {
      const created = await apiClient.post<
        SubappSessionCreated,
        { artifact_version_id: string; chat_session_id: string | null }
      >('/subapps/sessions', {
        artifact_version_id: options.artifactVersionId,
        chat_session_id: options.chatSessionId ?? null,
      })
      if (destroyed) {
        // Unmounted while awaiting (e.g. StrictMode remount). Best-effort
        // terminate the orphan session so it does not linger.
        if (created?.session_id) {
          void apiClient
            .post<unknown>(`/subapps/sessions/${created.session_id}/terminate`)
            .catch(() => {})
        }
        return
      }
      if (
        !created ||
        !created.session_id ||
        typeof created.token !== 'string' ||
        !asRecord(created.unlock_message)
      ) {
        fail('subapp_session_invalid_envelope')
        return
      }
      currentToken = created.token
      envelope = { sessionId: created.session_id, unlockMessage: created.unlock_message }
      activate()
    } catch (error) {
      if (destroyed) return
      console.warn('[subapp-channel] failed to instantiate subapp session', error)
      fail('subapp_session_creation_failed')
    }
  }

  async function pollStates(): Promise<void> {
    if (destroyed || !envelope) return
    pollAttempts += 1
    if (pollAttempts > SUBAPP_POLL_MAX_ATTEMPTS) {
      clearPolling()
      return
    }
    try {
      const list = await apiClient.get<SubappStateList>(
        `/subapps/sessions/${envelope.sessionId}/states`,
        { query: { offset: 0, limit: 1 } },
      )
      if (destroyed || !envelope) return
      const latest = list?.items?.[0]
      if (latest && typeof latest.version === 'number' && latest.version > lastStateVersion) {
        lastStateVersion = latest.version
        postRendererState(options.getIframe(), subappStateMessage(latest))
        options.onStatePushed?.(latest.version)
        void deliverMedia(latest)
      }
      const sessionView = await apiClient.get<{
        agent_status?: string
        agent_error?: string | null
      }>(`/subapps/sessions/${envelope.sessionId}`)
      if (destroyed || !envelope) return
      const agentStatus = sessionView?.agent_status
      if (
        agentStatus === 'idle' ||
        agentStatus === 'queued' ||
        agentStatus === 'processing' ||
        agentStatus === 'failed'
      ) {
        options.onAgentStatus?.({
          agentStatus,
          error: sessionView?.agent_error ?? null,
        })
      }
    } catch (error) {
      // Transient poll failure: keep the timer running for the next attempt.
      console.warn('[subapp-channel] state poll failed', error)
    }
  }

  function startPolling(): void {
    if (pollTimer !== null) return
    pollAttempts = 0
    pollTimer = window.setInterval(() => {
      void pollStates()
    }, SUBAPP_POLL_INTERVAL_MS)
  }

  function activate(): void {
    if (delivered || !envelope || !iframeLoaded) return
    const frame = options.getIframe()
    if (!frame) {
      fail('subapp_iframe_missing')
      return
    }
    delivered = true
    // 1) Unlock handshake — the one token-bearing host→iframe message.
    postRendererUnlock(frame, envelope.unlockMessage)
    // 2) Best-effort initial state if the server already produced one.
    void pollStates()
    // 3) Host-relay bridge; events rotate the capability token per acceptance.
    bridge = createSubappBridge(frame, {
      sessionId: envelope.sessionId,
      session: {
        sessionId: envelope.sessionId,
        getToken: () => currentToken,
        onAccepted: (accepted) => {
          currentToken = accepted.nextToken
          pollAttempts = 0
          startPolling()
        },
        onAck: (ack) => {
          postRendererAck(options.getIframe(), {
            version: '1',
            event_type: 'component.event.ack',
            payload: {
              client_event_id: ack.clientEventId,
              status: ack.status,
              error_code: ack.errorCode ?? null,
            },
          })
        },
        onConsentRequired: options.onConsentRequired,
        onEventQueued: options.onEventQueued,
        onRejected: (error) => {
          if (destroyed) return
          const status = apiErrorStatus(error)
          if (status === 401 || status === 409) {
            // Stale/consumed token or session no longer active — the loop is over.
            fail('subapp_session_rejected')
          } else {
            // Schema/budget rejections are contract feedback; keep the session.
            console.warn('[subapp-channel] component.event rejected by session', error)
          }
        },
      },
    })
    startPolling()
  }

  function handleIframeLoad(): void {
    if (destroyed) return
    iframeLoaded = true
    if (envelope) activate()
  }

  async function decideConsent(
    decision: 'allow_session' | 'allow_app' | 'allow_global' | 'deny',
  ): Promise<boolean> {
    if (!envelope || !currentToken) return false
    try {
      await apiClient.post<unknown, { token: string; decision: string }>(
        `/subapps/sessions/${envelope.sessionId}/agent-consent`,
        { token: currentToken, decision },
      )
      pollAttempts = 0
      startPolling()
      return true
    } catch (error) {
      console.warn('[subapp-channel] agent consent decision failed', error)
      return false
    }
  }

  async function retryAgentTask(): Promise<boolean> {
    if (!envelope || !currentToken) return false
    try {
      await apiClient.post<unknown, { token: string }>(
        `/subapps/sessions/${envelope.sessionId}/agent-task/retry`,
        { token: currentToken },
      )
      pollAttempts = 0
      startPolling()
      return true
    } catch (error) {
      console.warn('[subapp-channel] agent task retry failed', error)
      return false
    }
  }

  async function cancelAgentTask(): Promise<boolean> {
    if (!envelope || !currentToken) return false
    try {
      await apiClient.post<unknown, { token: string }>(
        `/subapps/sessions/${envelope.sessionId}/agent-task/cancel`,
        { token: currentToken },
      )
      return true
    } catch (error) {
      console.warn('[subapp-channel] agent task cancel failed', error)
      return false
    }
  }

  if (options.provisioned) {
    if (iframeLoaded) activate()
  } else {
    void instantiate()
  }

  return {
    handleIframeLoad,
    sessionId: () => envelope?.sessionId ?? null,
    decideConsent,
    retryAgentTask,
    cancelAgentTask,
    destroy(): void {
      if (destroyed) return
      destroyed = true
      clearPolling()
      teardownBridge()
      const id = envelope?.sessionId
      if (id) {
        // Best-effort server-side termination so the session token is consumed
        // and no orphan session lingers.
        void apiClient.post<unknown>(`/subapps/sessions/${id}/terminate`).catch(() => {})
      }
      envelope = null
      currentToken = null
      delivered = true
    },
  }
}
