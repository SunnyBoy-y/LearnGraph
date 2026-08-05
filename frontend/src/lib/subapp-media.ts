/**
 * P3 blob media injection for interactive sub-application iframes.
 *
 * A subapp state snapshot (`renderer.state` payload `state`) may declare media
 * assets under an optional top-level `media` array. The host resolves those
 * references into bytes, validates shape / MIME / size, and pushes them into the
 * opaque-origin iframe as a NEW host→iframe protocol message `renderer.media`.
 * The real iframe content is self-implemented by the sub-application; this
 * module delivers the host→iframe media channel plus the frontend-testable
 * injection function (`createSubappMediaReceiver`) a future `subapp-boot.html`
 * template bundles.
 *
 * Delivery rationale (§9.9 / design §5 P3): an opaque-origin iframe loading a
 * `blob:` URL created by the HOST origin is a cross-origin fetch with undefined,
 * implementation-dependent semantics. Instead the host postMessages the media
 * Blobs (structured clone), and the iframe-side receiver calls
 * `URL.createObjectURL` in ITS OWN origin — the resulting `blob:` URLs are
 * same-origin to the iframe and load reliably under the unchanged CSP
 * (`img-src data: blob:` / `media-src data: blob:`). The iframe still never
 * touches the network (`connect-src 'none'` untouched).
 *
 * Security posture (design §9.9):
 * - Media only travels host→iframe via postMessage; never inside the iframe→host
 *   channel, never inside a capability-token-bearing message.
 * - Before injection the host validates ref shape, MIME against a playable
 *   allowlist, a per-asset byte cap (SUBAPP_MEDIA_MAX_BYTES) and a bounded count
 *   (SUBAPP_MEDIA_MAX_COUNT).
 * - The receiver re-validates every incoming message (shape / mime / size) and
 *   fails the whole batch closed on any invalid item; media for a stale
 *   `state_version` is dropped.
 * - Media bytes never land in the host main-DOM untrusted execution context.
 */

export type SubappMediaKind = 'image' | 'video' | 'audio'

/** Per-asset byte ceiling for injected media (5 MiB, tunable). */
export const SUBAPP_MEDIA_MAX_BYTES = 5 * 1024 * 1024

/** Upper bound on media assets a single state snapshot may declare. */
export const SUBAPP_MEDIA_MAX_COUNT = 16

/** Bounded id charset so a ref id can never carry payload / surprise paths. */
const MEDIA_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/

/**
 * MIME allowlist — playable media only. No SVG (script-capable container), no
 * PDFs, no archive/application types. `kind` must match a playable family.
 */
const SUBAPP_MEDIA_MIME_ALLOWLIST: Record<SubappMediaKind, ReadonlySet<string>> = {
  image: new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/avif']),
  audio: new Set(['audio/mpeg', 'audio/ogg', 'audio/wav', 'audio/webm', 'audio/mp4']),
  video: new Set(['video/mp4', 'video/webm', 'video/ogg']),
}

export function subappMediaMimeAllowed(mime: string, kind: SubappMediaKind): boolean {
  return SUBAPP_MEDIA_MIME_ALLOWLIST[kind]?.has(mime.trim().toLowerCase()) ?? false
}

/** A media reference declared inside a state snapshot. */
export interface SubappMediaRef {
  id: string
  kind: SubappMediaKind
  mime: string
  source: string
  alt?: string
}

/** A resolved, validated media asset ready to be injected. */
export interface SubappMediaAsset {
  id: string
  kind: SubappMediaKind
  mime: string
  blob: Blob
}

export const SUBAPP_MEDIA_EVENT_TYPE = 'renderer.media'
export const SUBAPP_MEDIA_MESSAGE_VERSION = '1'

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function isBlobLike(value: unknown): value is Blob {
  return (
    value !== null &&
    typeof value === 'object' &&
    typeof (value as Blob).size === 'number' &&
    typeof (value as Blob).type === 'string' &&
    typeof (value as Blob).arrayBuffer === 'function'
  )
}

function parseSubappMediaRef(item: unknown): SubappMediaRef | null {
  const record = asRecord(item)
  if (!record) return null
  const id = typeof record.id === 'string' ? record.id.trim() : ''
  if (!MEDIA_ID_PATTERN.test(id)) return null
  const kind = record.kind
  if (kind !== 'image' && kind !== 'video' && kind !== 'audio') return null
  const mime = typeof record.mime === 'string' ? record.mime.trim() : ''
  if (!subappMediaMimeAllowed(mime, kind)) return null
  const source = typeof record.source === 'string' ? record.source.trim() : ''
  if (!source) return null
  const alt =
    typeof record.alt === 'string' && record.alt.trim() ? record.alt.trim().slice(0, 200) : undefined
  return { id, kind, mime, source, alt }
}

/**
 * Extract and shape-validate the media references declared by a state snapshot.
 * Returns [] when the snapshot carries no `media` array, when the array exceeds
 * SUBAPP_MEDIA_MAX_COUNT (fail-closed), or when no valid item remains. Every
 * returned ref has passed the id / kind / mime allowlist checks; the byte size
 * is validated at resolution time.
 */
export function parseSubappMediaRefs(state: unknown): SubappMediaRef[] {
  const record = asRecord(state)
  if (!record) return []
  const media = record.media
  if (!Array.isArray(media)) return []
  if (media.length > SUBAPP_MEDIA_MAX_COUNT) return []
  const refs: SubappMediaRef[] = []
  const seen = new Set<string>()
  for (const item of media) {
    const ref = parseSubappMediaRef(item)
    if (!ref) continue
    if (seen.has(ref.id)) continue
    seen.add(ref.id)
    refs.push(ref)
  }
  return refs
}

/**
 * Resolve media references into validated byte assets. `fetchSource` returns
 * the bytes for a source locator it handles, or null when it does not handle
 * the scheme. Per-ref failures (missing file, oversized, disallowed MIME,
 * unknown scheme, thrown fetch) are dropped without throwing, so one bad asset
 * can never stall the rest of the batch.
 */
export async function resolveSubappMedia(
  refs: SubappMediaRef[],
  fetchSource: (source: string) => Promise<Blob | null>,
): Promise<SubappMediaAsset[]> {
  const assets: SubappMediaAsset[] = []
  for (const ref of refs) {
    let blob: Blob | null = null
    try {
      blob = await fetchSource(ref.source)
    } catch {
      blob = null
    }
    if (!isBlobLike(blob)) continue
    if (blob.size > SUBAPP_MEDIA_MAX_BYTES) continue
    // Defense in depth: the resolved blob's own type (or the declared mime)
    // must still sit on the playable allowlist for the declared kind.
    const effectiveMime = blob.type.trim() || ref.mime
    if (!subappMediaMimeAllowed(effectiveMime, ref.kind)) continue
    assets.push({ id: ref.id, kind: ref.kind, mime: effectiveMime.toLowerCase(), blob })
  }
  return assets
}

/**
 * Build the host→iframe `renderer.media` protocol message. The `blob` members
 * are real Blob objects carried by structured clone through postMessage — this
 * message is never JSON-serialized and never travels the API. An empty `media`
 * array is a valid "clear the injected set for this state version" signal.
 */
export function subappMediaMessage(
  stateVersion: number,
  media: SubappMediaAsset[],
): Record<string, unknown> {
  return {
    version: SUBAPP_MEDIA_MESSAGE_VERSION,
    event_type: SUBAPP_MEDIA_EVENT_TYPE,
    payload: {
      state_version: stateVersion,
      media: media.map((asset) => ({
        id: asset.id,
        kind: asset.kind,
        mime: asset.mime,
        blob: asset.blob,
      })),
    },
  }
}

/**
 * Post a `renderer.media` message into the subapp iframe. Pairs with the
 * `renderer.unlock` / `renderer.state` posts and uses the identical
 * `targetOrigin '*'` — the only option for an opaque-origin iframe. Never
 * throws, so a media push can never break the artifact render.
 */
export function postRendererMedia(
  iframe: HTMLIFrameElement | null,
  message: Record<string, unknown> | null,
): void {
  if (!iframe || !message) return
  try {
    iframe.contentWindow?.postMessage(message, '*')
  } catch {
    // Inert opaque iframe / jsdom: there is no receiver, drop silently.
  }
}

// --------------------------------------------------------------------------- //
// iframe-side receiver (the frontend-testable injection function)
// --------------------------------------------------------------------------- //

/** A media asset injected into the iframe, addressable by its blob URL. */
export interface SubappMediaRenderable {
  id: string
  url: string
  kind: SubappMediaKind
  mime: string
}

export interface SubappMediaReceiver {
  /** Object URL for a media id, or null when the asset was not injected. */
  resolve(id: string): string | null
  /**
   * Object URLs for every media ref declared by a state snapshot — the hook a
   * subapp template uses to render `<img src>` / `<video src>` / `<audio src>`.
   * Ids declared by the state but not yet injected simply resolve to null.
   */
  mediaForState(
    state: Record<string, unknown> | null | undefined,
  ): Record<string, SubappMediaRenderable>
  /** Process one host message explicitly (also wired to `window` on create). */
  handleMessage(event: MessageEvent): void
  /** Stop listening and revoke every object URL created by this receiver. */
  destroy(): void
}

function createObjectUrl(blob: Blob): string | null {
  try {
    return URL.createObjectURL(blob)
  } catch {
    return null
  }
}

function revokeObjectUrl(url: string): void {
  try {
    URL.revokeObjectURL(url)
  } catch {
    // ignore
  }
}

function parseInjectedMediaItem(item: unknown): SubappMediaRenderable | null {
  const record = asRecord(item)
  if (!record) return null
  const id = typeof record.id === 'string' ? record.id : ''
  if (!MEDIA_ID_PATTERN.test(id)) return null
  const kind = record.kind
  if (kind !== 'image' && kind !== 'video' && kind !== 'audio') return null
  const mime = typeof record.mime === 'string' ? record.mime.trim().toLowerCase() : ''
  if (!subappMediaMimeAllowed(mime, kind)) return null
  const blob = record.blob
  if (!isBlobLike(blob)) return null
  if (blob.size > SUBAPP_MEDIA_MAX_BYTES) return null
  const url = createObjectUrl(blob)
  if (!url) return null
  return { id, url, kind, mime }
}

/**
 * Create the iframe-side media receiver. On creation it starts listening for
 * `window` "message" events; call destroy() on channel teardown. Only
 * `renderer.media` messages whose `state_version` advances the monotonic version
 * are applied, and any structurally invalid item fails the whole batch closed,
 * so a malformed or over-allocating message cannot partially inject media.
 */
export function createSubappMediaReceiver(): SubappMediaReceiver {
  const assets = new Map<string, SubappMediaRenderable>()
  let lastVersion = 0
  let destroyed = false

  function applyMedia(message: Record<string, unknown>): void {
    if (destroyed) return
    const payload = asRecord(message.payload)
    if (!payload) return
    const stateVersion = payload.state_version
    if (typeof stateVersion !== 'number' || !Number.isInteger(stateVersion) || stateVersion <= 0) {
      return
    }
    if (stateVersion <= lastVersion) return
    const media = payload.media
    if (!Array.isArray(media) || media.length > SUBAPP_MEDIA_MAX_COUNT) return

    const next = new Map<string, SubappMediaRenderable>()
    const created: string[] = []
    for (const item of media) {
      const renderable = parseInjectedMediaItem(item)
      if (!renderable || next.has(renderable.id)) {
        // Fail closed: revoke URLs created for earlier items in this batch.
        for (const url of created) revokeObjectUrl(url)
        return
      }
      created.push(renderable.url)
      next.set(renderable.id, renderable)
    }

    for (const [id, renderable] of assets) {
      if (!next.has(id)) revokeObjectUrl(renderable.url)
    }
    assets.clear()
    for (const [id, renderable] of next) assets.set(id, renderable)
    lastVersion = stateVersion
  }

  function onMessage(event: MessageEvent): void {
    const data = asRecord(event.data)
    if (!data || data.event_type !== SUBAPP_MEDIA_EVENT_TYPE) return
    applyMedia(data)
  }

  if (typeof window !== 'undefined') {
    window.addEventListener('message', onMessage)
  }

  return {
    resolve(id) {
      return assets.get(id)?.url ?? null
    },
    mediaForState(state) {
      const refs = parseSubappMediaRefs(state)
      const result: Record<string, SubappMediaRenderable> = {}
      for (const ref of refs) {
        const renderable = assets.get(ref.id)
        if (renderable) result[ref.id] = renderable
      }
      return result
    },
    handleMessage: onMessage,
    destroy() {
      if (destroyed) return
      destroyed = true
      if (typeof window !== 'undefined') {
        window.removeEventListener('message', onMessage)
      }
      for (const renderable of assets.values()) revokeObjectUrl(renderable.url)
      assets.clear()
      lastVersion = 0
    },
  }
}
