import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  SUBAPP_MEDIA_MAX_BYTES,
  SUBAPP_MEDIA_MAX_COUNT,
  SUBAPP_MEDIA_EVENT_TYPE,
  createSubappMediaReceiver,
  parseSubappMediaRefs,
  postRendererMedia,
  resolveSubappMedia,
  subappMediaMessage,
  subappMediaMimeAllowed,
} from './subapp-media'
import type { SubappMediaRef } from './subapp-media'

// jsdom does not implement URL.createObjectURL / revokeObjectURL. Install
// tracked fakes so the receiver tests can assert lifecycle behaviour.
let objectUrlCounter = 0
const objectUrls = new Map<string, Blob>()
let createObjectURLMock: ReturnType<typeof vi.fn>
let revokeObjectURLMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  objectUrlCounter = 0
  objectUrls.clear()
  createObjectURLMock = vi.fn((blob: Blob) => {
    const url = `blob:mock-${objectUrlCounter}`
    objectUrlCounter += 1
    objectUrls.set(url, blob)
    return url
  })
  revokeObjectURLMock = vi.fn((url: string) => {
    objectUrls.delete(url)
  })
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    writable: true,
    value: createObjectURLMock,
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    writable: true,
    value: revokeObjectURLMock,
  })
})

function mediaRef(overrides: Partial<SubappMediaRef> = {}): SubappMediaRef {
  return {
    id: 'cover',
    kind: 'image',
    mime: 'image/png',
    source: 'file:abc123',
    ...overrides,
  }
}

function mediaState(media: unknown): Record<string, unknown> {
  return { view: 'result', answers: {}, media }
}

function dispatchWindowMessage(data: unknown): void {
  window.dispatchEvent(new MessageEvent('message', { data }))
}

function mediaEnvelope(stateVersion: number, media: unknown[]): Record<string, unknown> {
  return {
    version: '1',
    event_type: SUBAPP_MEDIA_EVENT_TYPE,
    payload: { state_version: stateVersion, media },
  }
}

describe('subappMediaMimeAllowed', () => {
  it('allows only the playable allowlist for each kind', () => {
    expect(subappMediaMimeAllowed('image/png', 'image')).toBe(true)
    expect(subappMediaMimeAllowed('image/jpeg', 'image')).toBe(true)
    expect(subappMediaMimeAllowed('image/svg+xml', 'image')).toBe(false)
    expect(subappMediaMimeAllowed('audio/mpeg', 'audio')).toBe(true)
    expect(subappMediaMimeAllowed('audio/mpeg', 'video')).toBe(false)
    expect(subappMediaMimeAllowed('video/mp4', 'video')).toBe(true)
    expect(subappMediaMimeAllowed('application/octet-stream', 'video')).toBe(false)
    expect(subappMediaMimeAllowed('application/octet-stream', 'image')).toBe(false)
  })

  it('is case-insensitive and trims whitespace', () => {
    expect(subappMediaMimeAllowed('  IMAGE/PNG ', 'image')).toBe(true)
  })
})

describe('parseSubappMediaRefs', () => {
  it('extracts and validates a well-formed media array', () => {
    const state = mediaState([
      { id: 'cover', kind: 'image', mime: 'image/png', source: 'file:a1' },
      { id: 'narration', kind: 'audio', mime: 'audio/mpeg', source: 'file:b2', alt: '解说' },
    ])
    expect(parseSubappMediaRefs(state)).toEqual([
      { id: 'cover', kind: 'image', mime: 'image/png', source: 'file:a1' },
      { id: 'narration', kind: 'audio', mime: 'audio/mpeg', source: 'file:b2', alt: '解说' },
    ])
  })

  it('returns [] for null, undefined, or non-object state', () => {
    expect(parseSubappMediaRefs(null)).toEqual([])
    expect(parseSubappMediaRefs(undefined)).toEqual([])
    expect(parseSubappMediaRefs(['nope'])).toEqual([])
  })

  it('returns [] when the media field is absent or not an array', () => {
    expect(parseSubappMediaRefs({ view: 'x' })).toEqual([])
    expect(parseSubappMediaRefs(mediaState('not-an-array'))).toEqual([])
  })

  it('drops items with an invalid id, kind, mime, or missing source', () => {
    const state = mediaState([
      { id: 'bad id with spaces', kind: 'image', mime: 'image/png', source: 'file:a' },
      { id: 'ok', kind: 'banner', mime: 'image/png', source: 'file:b' },
      { id: 'svg', kind: 'image', mime: 'image/svg+xml', source: 'file:c' },
      { id: 'nosrc', kind: 'image', mime: 'image/png' },
      { id: 'good', kind: 'image', mime: 'image/png', source: 'file:z' },
    ])
    expect(parseSubappMediaRefs(state)).toEqual([
      { id: 'good', kind: 'image', mime: 'image/png', source: 'file:z' },
    ])
  })

  it('dedupes by id, keeping the first occurrence', () => {
    const state = mediaState([
      { id: 'cover', kind: 'image', mime: 'image/png', source: 'file:a' },
      { id: 'cover', kind: 'audio', mime: 'audio/mpeg', source: 'file:b' },
    ])
    expect(parseSubappMediaRefs(state)).toEqual([
      { id: 'cover', kind: 'image', mime: 'image/png', source: 'file:a' },
    ])
  })

  it('fail-closes when the media array exceeds the count bound', () => {
    const many = Array.from({ length: SUBAPP_MEDIA_MAX_COUNT + 1 }, (_, i) => ({
      id: `m${i}`,
      kind: 'image' as const,
      mime: 'image/png',
      source: `file:m${i}`,
    }))
    expect(parseSubappMediaRefs(mediaState(many))).toEqual([])
  })
})

describe('resolveSubappMedia', () => {
  it('resolves each ref into a validated asset via fetchSource', async () => {
    const refs = [
      mediaRef(),
      mediaRef({ id: 'a2', kind: 'audio', mime: 'audio/mpeg', source: 'file:mp3' }),
    ]
    const fetchSource = vi.fn(async (source: string) => {
      if (source === 'file:abc123') return new Blob(['png'], { type: 'image/png' })
      return new Blob(['mp3'], { type: 'audio/mpeg' })
    })
    const assets = await resolveSubappMedia(refs, fetchSource)
    expect(assets).toHaveLength(2)
    expect(assets[0]).toMatchObject({ id: 'cover', kind: 'image', mime: 'image/png' })
    expect(assets[0].blob.size).toBeGreaterThan(0)
  })

  it('drops refs whose scheme fetchSource does not handle (returns null)', async () => {
    const assets = await resolveSubappMedia(
      [mediaRef({ id: 'x', source: 'http://example.com/evil' })],
      vi.fn(async () => null),
    )
    expect(assets).toEqual([])
  })

  it('drops refs whose fetchSource throws', async () => {
    const assets = await resolveSubappMedia(
      [mediaRef()],
      vi.fn(async () => {
        throw new Error('404')
      }),
    )
    expect(assets).toEqual([])
  })

  it('drops blobs over the per-asset byte cap', async () => {
    const big = new Blob([new Uint8Array(SUBAPP_MEDIA_MAX_BYTES + 1)], { type: 'image/png' })
    const assets = await resolveSubappMedia([mediaRef()], vi.fn(async () => big))
    expect(assets).toEqual([])
  })

  it('drops blobs whose own type is not on the allowlist even when ref.mime was allowed', async () => {
    const assets = await resolveSubappMedia(
      [mediaRef({ mime: 'image/png' })],
      vi.fn(async () => new Blob(['x'], { type: 'application/x-msdownload' })),
    )
    expect(assets).toEqual([])
  })

  it('falls back to the declared ref.mime when the blob type is empty', async () => {
    const assets = await resolveSubappMedia(
      [mediaRef({ kind: 'video', mime: 'video/mp4' })],
      vi.fn(async () => new Blob(['v'])),
    )
    expect(assets).toHaveLength(1)
    expect(assets[0].mime).toBe('video/mp4')
  })
})

describe('subappMediaMessage', () => {
  it('builds the renderer.media envelope with blob-carrying items', () => {
    const blob = new Blob(['png'], { type: 'image/png' })
    const message = subappMediaMessage(3, [{ id: 'cover', kind: 'image', mime: 'image/png', blob }])
    expect(message.version).toBe('1')
    expect(message.event_type).toBe(SUBAPP_MEDIA_EVENT_TYPE)
    const payload = message.payload as Record<string, unknown>
    expect(payload.state_version).toBe(3)
    const media = (payload.media as Array<Record<string, unknown>>)[0]
    expect(media).toMatchObject({ id: 'cover', kind: 'image', mime: 'image/png' })
    expect(media.blob).toBe(blob)
  })

  it('supports an empty media array as a clear signal', () => {
    const message = subappMediaMessage(4, [])
    const payload = message.payload as Record<string, unknown>
    expect(payload.media).toEqual([])
  })
})

describe('postRendererMedia', () => {
  it('posts with the opaque-origin targetOrigin *', () => {
    const postMessage = vi.fn()
    const iframe = { contentWindow: { postMessage } } as unknown as HTMLIFrameElement
    postRendererMedia(iframe, subappMediaMessage(1, []))
    expect(postMessage).toHaveBeenCalledWith(
      expect.objectContaining({ event_type: SUBAPP_MEDIA_EVENT_TYPE }),
      '*',
    )
  })

  it('is a no-op for a null iframe or message, and never throws', () => {
    expect(() => postRendererMedia(null, subappMediaMessage(1, []))).not.toThrow()
    const iframe = { contentWindow: { postMessage: vi.fn() } } as unknown as HTMLIFrameElement
    expect(() => postRendererMedia(iframe, null)).not.toThrow()
    expect(() =>
      postRendererMedia({ contentWindow: null } as unknown as HTMLIFrameElement, subappMediaMessage(1, [])),
    ).not.toThrow()
  })
})

describe('createSubappMediaReceiver', () => {
  it('injects media from renderer.media and resolves blob URLs by id', () => {
    const receiver = createSubappMediaReceiver()
    const blob = new Blob(['png'], { type: 'image/png' })
    dispatchWindowMessage(
      mediaEnvelope(2, [{ id: 'cover', kind: 'image', mime: 'image/png', blob }]),
    )
    expect(createObjectURLMock).toHaveBeenCalledWith(blob)
    const url = receiver.resolve('cover')
    expect(url).toMatch(/^blob:mock-0$/)
    expect(receiver.resolve('missing')).toBeNull()
    receiver.destroy()
  })

  it('mediaForState exposes renderables only for ids the state declares and media injected', () => {
    const receiver = createSubappMediaReceiver()
    dispatchWindowMessage(
      mediaEnvelope(1, [
        { id: 'cover', kind: 'image', mime: 'image/png', blob: new Blob(['p'], { type: 'image/png' }) },
        { id: 'clip', kind: 'video', mime: 'video/mp4', blob: new Blob(['v'], { type: 'video/mp4' }) },
      ]),
    )
    const renderables = receiver.mediaForState(
      mediaState([
        { id: 'cover', kind: 'image', mime: 'image/png', source: 'file:a' },
        { id: 'not-injected', kind: 'image', mime: 'image/png', source: 'file:b' },
      ]),
    )
    expect(Object.keys(renderables)).toEqual(['cover'])
    expect(renderables.cover.url).toMatch(/^blob:mock-/)
    expect(renderables.cover.kind).toBe('image')
    receiver.destroy()
  })

  it('mediaForState returns {} for a state without media', () => {
    const receiver = createSubappMediaReceiver()
    expect(receiver.mediaForState({ view: 'plain' })).toEqual({})
    receiver.destroy()
  })

  it('ignores a stale state_version so late resolution cannot clobber newer media', () => {
    const receiver = createSubappMediaReceiver()
    const first = new Blob(['a'], { type: 'image/png' })
    dispatchWindowMessage(mediaEnvelope(3, [{ id: 'cover', kind: 'image', mime: 'image/png', blob: first }]))
    const url3 = receiver.resolve('cover')
    expect(url3).toMatch(/^blob:mock-/)

    const second = new Blob(['b'], { type: 'image/png' })
    dispatchWindowMessage(mediaEnvelope(2, [{ id: 'cover', kind: 'image', mime: 'image/png', blob: second }]))
    expect(receiver.resolve('cover')).toBe(url3)
    // The stale v2 batch must not have created a second object URL.
    expect(createObjectURLMock).toHaveBeenCalledTimes(1)
    receiver.destroy()
  })

  it('replaces the set on a newer version and revokes dropped ids', () => {
    const receiver = createSubappMediaReceiver()
    dispatchWindowMessage(
      mediaEnvelope(1, [
        { id: 'a', kind: 'image', mime: 'image/png', blob: new Blob(['a'], { type: 'image/png' }) },
      ]),
    )
    dispatchWindowMessage(
      mediaEnvelope(2, [
        { id: 'b', kind: 'image', mime: 'image/webp', blob: new Blob(['b'], { type: 'image/webp' }) },
      ]),
    )
    expect(receiver.resolve('a')).toBeNull()
    expect(receiver.resolve('b')).toMatch(/^blob:mock-1$/)
    expect(revokeObjectURLMock).toHaveBeenCalledWith('blob:mock-0')
    receiver.destroy()
  })

  it('an empty media batch for a newer version clears the injected set', () => {
    const receiver = createSubappMediaReceiver()
    dispatchWindowMessage(
      mediaEnvelope(1, [
        { id: 'a', kind: 'image', mime: 'image/png', blob: new Blob(['a'], { type: 'image/png' }) },
      ]),
    )
    dispatchWindowMessage(mediaEnvelope(2, []))
    expect(receiver.resolve('a')).toBeNull()
    expect(revokeObjectURLMock).toHaveBeenCalledWith('blob:mock-0')
    receiver.destroy()
  })

  it('fails the whole batch closed when any item is invalid', () => {
    const receiver = createSubappMediaReceiver()
    const good = new Blob(['g'], { type: 'image/png' })
    const badMime = new Blob(['x'], { type: 'application/x-msdownload' })
    dispatchWindowMessage(
      mediaEnvelope(1, [
        { id: 'good', kind: 'image', mime: 'image/png', blob: good },
        { id: 'bad', kind: 'image', mime: 'application/x-msdownload', blob: badMime },
      ]),
    )
    expect(receiver.resolve('good')).toBeNull()
    expect(receiver.resolve('bad')).toBeNull()
    // The URL created for the earlier good item is revoked on fail-closed.
    expect(revokeObjectURLMock).toHaveBeenCalled()
    receiver.destroy()
  })

  it('rejects an oversize asset', () => {
    const receiver = createSubappMediaReceiver()
    const big = new Blob([new Uint8Array(SUBAPP_MEDIA_MAX_BYTES + 1)], { type: 'image/png' })
    dispatchWindowMessage(mediaEnvelope(1, [{ id: 'big', kind: 'image', mime: 'image/png', blob: big }]))
    expect(receiver.resolve('big')).toBeNull()
    expect(createObjectURLMock).not.toHaveBeenCalled()
    receiver.destroy()
  })

  it('rejects a non-blob payload member', () => {
    const receiver = createSubappMediaReceiver()
    dispatchWindowMessage(mediaEnvelope(1, [{ id: 'x', kind: 'image', mime: 'image/png', blob: 'nope' }]))
    expect(receiver.resolve('x')).toBeNull()
    receiver.destroy()
  })

  it('destroy revokes every object URL and stops listening', () => {
    const receiver = createSubappMediaReceiver()
    dispatchWindowMessage(
      mediaEnvelope(1, [
        { id: 'a', kind: 'audio', mime: 'audio/mpeg', blob: new Blob(['a'], { type: 'audio/mpeg' }) },
      ]),
    )
    expect(objectUrls.size).toBe(1)
    receiver.destroy()
    expect(objectUrls.size).toBe(0)
    // After destroy, further messages are ignored.
    dispatchWindowMessage(
      mediaEnvelope(2, [
        { id: 'b', kind: 'image', mime: 'image/png', blob: new Blob(['b'], { type: 'image/png' }) },
      ]),
    )
    expect(receiver.resolve('b')).toBeNull()
  })
})
