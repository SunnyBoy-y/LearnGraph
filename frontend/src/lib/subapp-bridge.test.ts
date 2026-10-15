import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  SUBAPP_POLL_INTERVAL_MS,
  SUBAPP_POLL_MAX_ATTEMPTS,
  createSubappChannel,
  subappSessionTrigger,
  subappStateMessage,
} from './subapp-bridge'

const { post, get, getBlob } = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  getBlob: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  apiClient: { post, get, getBlob },
}))

function unlockMessage(token: string) {
  return {
    version: '1',
    event_type: 'renderer.unlock',
    payload: { token, component_id: 'card', render_ref: 'sess-1' },
  }
}

function createSessionResponse(token: string) {
  return {
    session_id: 'sess-1',
    status: 'active',
    state_version: 0,
    state_sha256: null,
    token,
    token_prefix: token.slice(0, 8),
    component_id: 'card',
    render_ref: 'sess-1',
    artifact_version_id: 'av-1',
    chat_session_id: 'chat-1',
    event_schema: { type: 'object' },
    state_schema: { type: 'object' },
    unlock_message: unlockMessage(token),
  }
}

function emptyStates() {
  return { items: [], offset: 0, limit: 1, total: 0 }
}

type FakeIframe = { contentWindow: { postMessage: ReturnType<typeof vi.fn> } } & HTMLIFrameElement

function fakeIframe(): FakeIframe {
  const postMessage = vi.fn()
  return { contentWindow: { postMessage } } as unknown as FakeIframe
}

function dispatchIframeMessage(iframe: FakeIframe, data: unknown): void {
  const event = new Event('message')
  Object.defineProperty(event, 'source', { value: iframe.contentWindow })
  Object.defineProperty(event, 'data', { value: data })
  window.dispatchEvent(event)
}

async function flushPromises(): Promise<void> {
  for (let i = 0; i < 20; i += 1) {
    await Promise.resolve()
  }
}

function componentEvent(type: string, value: Record<string, unknown>) {
  return {
    version: '1',
    event_type: 'component.event',
    payload: { type, value },
    token: 'token-0',
  }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('subappSessionTrigger', () => {
  it('returns null for a plain static artifact (no subapp marker)', () => {
    expect(
      subappSessionTrigger({
        preview_html: '<html></html>',
        title: '静态预览',
        trusted_renderer: null,
      }),
    ).toBeNull()
  })

  it('recognizes a pre-provisioned session with its token envelope', () => {
    const trigger = subappSessionTrigger({
      subapp_session_id: 'sess-1',
      unlock_message: unlockMessage('token-0'),
    })
    expect(trigger).toEqual({
      kind: 'provisioned',
      sessionId: 'sess-1',
      token: 'token-0',
      unlockMessage: unlockMessage('token-0'),
    })
  })

  it('treats a subapp_session_id without a usable token as a broken marker', () => {
    expect(subappSessionTrigger({ subapp_session_id: 'sess-1' })).toBeNull()
    expect(
      subappSessionTrigger({
        subapp_session_id: 'sess-1',
        unlock_message: { version: '1', event_type: 'renderer.unlock', payload: {} },
      }),
    ).toBeNull()
  })

  it('instantiates when a published version is marked interactive', () => {
    expect(
      subappSessionTrigger({
        artifact_version_id: 'av-1',
        subapp_mode: true,
        preview_html: '<html></html>',
      }),
    ).toEqual({ kind: 'instantiate', artifactVersionId: 'av-1' })

    expect(
      subappSessionTrigger({
        artifact_version_id: 'av-1',
        interaction_contract: { event_schema: {}, state_schema: {} },
      }),
    ).toEqual({ kind: 'instantiate', artifactVersionId: 'av-1' })
  })

  it('does not instantiate from a version alone or a flag alone', () => {
    expect(subappSessionTrigger({ artifact_version_id: 'av-1' })).toBeNull()
    expect(subappSessionTrigger({ subapp_mode: true, preview_html: '<html></html>' })).toBeNull()
  })
})

describe('subappStateMessage', () => {
  it('builds the §9.3 renderer.state message shape', () => {
    const message = subappStateMessage({
      id: 'st-1',
      session_id: 'sess-1',
      version: 3,
      sha256: 'a'.repeat(64),
      state: { view: 'result', answers: { q1: 'a' } },
      created_at: '2026-08-05T00:00:00Z',
    })
    expect(message).toEqual({
      version: '1',
      event_type: 'renderer.state',
      payload: {
        state_version: 3,
        state_sha256: 'a'.repeat(64),
        state: { view: 'result', answers: { q1: 'a' } },
      },
    })
  })

  it('returns null for missing/invalid state snapshots', () => {
    expect(subappStateMessage(null)).toBeNull()
    expect(subappStateMessage(undefined)).toBeNull()
    expect(
      subappStateMessage({
        id: 'st-1',
        session_id: 'sess-1',
        version: 0,
        sha256: '',
        state: { bad: true },
        created_at: '',
      }),
    ).not.toBeNull()
  })
})

describe('createSubappChannel', () => {
  beforeEach(() => {
    post.mockReset()
    get.mockReset()
  })

  it('instantiates a session, unlocks on load, and relays events with the rotating token', async () => {
    post.mockResolvedValue({}).mockResolvedValueOnce(createSessionResponse('token-0'))
    get.mockResolvedValue(emptyStates())

    const iframe = fakeIframe()
    const channel = createSubappChannel({
      getIframe: () => iframe,
      artifactVersionId: 'av-1',
      chatSessionId: 'chat-1',
    })
    channel.handleIframeLoad()
    await flushPromises()

    expect(post).toHaveBeenCalledWith('/subapps/sessions', {
      artifact_version_id: 'av-1',
      chat_session_id: 'chat-1',
    })
    // Unlock handshake carries the token host→iframe exactly once.
    expect(iframe.contentWindow.postMessage).toHaveBeenCalledWith(
      unlockMessage('token-0'),
      '*',
    )
    expect(get).toHaveBeenCalledWith('/subapps/sessions/sess-1/states', {
      query: { offset: 0, limit: 1 },
    })

    // First event is relayed with the session token; 202 rotates it.
    post.mockResolvedValueOnce({
      accepted: true,
      session_id: 'sess-1',
      event: {},
      next_token: 'token-1',
      next_token_prefix: 'token-1',
    })
    dispatchIframeMessage(iframe, componentEvent('choice.selected', { a: 1 }))
    await flushPromises()
    expect(post).toHaveBeenCalledWith('/subapps/sessions/sess-1/events', {
      token: 'token-0',
      event_type: 'choice.selected',
      payload: { value: { a: 1 } },
    })

    // A second event is relayed with the rotated token.
    post.mockResolvedValueOnce({
      accepted: true,
      session_id: 'sess-1',
      event: {},
      next_token: 'token-2',
      next_token_prefix: 'token-2',
    })
    dispatchIframeMessage(iframe, componentEvent('form.submitted', {}))
    await flushPromises()
    expect(post).toHaveBeenCalledWith('/subapps/sessions/sess-1/events', {
      token: 'token-1',
      event_type: 'form.submitted',
      payload: { value: {} },
    })

    channel.destroy()
  })

  it('does not relay messages from a foreign window', async () => {
    post.mockResolvedValue({}).mockResolvedValueOnce(createSessionResponse('token-0'))
    get.mockResolvedValue(emptyStates())

    const iframe = fakeIframe()
    const channel = createSubappChannel({
      getIframe: () => iframe,
      artifactVersionId: 'av-1',
    })
    channel.handleIframeLoad()
    await flushPromises()

    // A message whose source is NOT the bound iframe must be ignored.
    const foreignEvent = new Event('message')
    Object.defineProperty(foreignEvent, 'source', { value: { notTheIframe: true } })
    Object.defineProperty(foreignEvent, 'data', { value: componentEvent('choice.selected', {}) })
    window.dispatchEvent(foreignEvent)
    await flushPromises()

    expect(post).not.toHaveBeenCalledWith(
      '/subapps/sessions/sess-1/events',
      expect.anything(),
    )
    channel.destroy()
  })

  it('drops malformed component.event messages before they reach the API', async () => {
    post.mockResolvedValue({}).mockResolvedValueOnce(createSessionResponse('token-0'))
    get.mockResolvedValue(emptyStates())

    const iframe = fakeIframe()
    const channel = createSubappChannel({ getIframe: () => iframe, artifactVersionId: 'av-1' })
    channel.handleIframeLoad()
    await flushPromises()

    // Wrong protocol type and unbounded event_type are dropped.
    dispatchIframeMessage(iframe, { version: '1', event_type: 'renderer.state', payload: {} })
    dispatchIframeMessage(iframe, {
      version: '1',
      event_type: 'component.event',
      payload: { type: 'UPPER_CASE_NOT_ALLOWED', value: {} },
      token: 'token-0',
    })
    await flushPromises()

    expect(post.mock.calls.filter(([path]) => String(path).includes('/events')).length).toBe(0)
    channel.destroy()
  })

  it('polls for newer state versions, pushes them, and never regresses to an older version', async () => {
    vi.useFakeTimers()
    post.mockResolvedValue({}).mockResolvedValueOnce(createSessionResponse('token-0'))
    get.mockResolvedValue(emptyStates())

    const iframe = fakeIframe()
    const onStatePushed = vi.fn()
    const channel = createSubappChannel({
      getIframe: () => iframe,
      artifactVersionId: 'av-1',
      onStatePushed,
    })
    channel.handleIframeLoad()
    await flushPromises()

    // New version 2 arrives → pushed into the iframe as renderer.state.
    get.mockResolvedValueOnce({
      items: [
        {
          id: 'st-2',
          session_id: 'sess-1',
          version: 2,
          sha256: 'b'.repeat(64),
          state: { view: 'updated' },
          created_at: '2026-08-05T00:00:01Z',
        },
      ],
      offset: 0,
      limit: 1,
      total: 1,
    })
    await vi.advanceTimersByTimeAsync(SUBAPP_POLL_INTERVAL_MS)
    await flushPromises()

    expect(iframe.contentWindow.postMessage).toHaveBeenCalledWith(
      {
        version: '1',
        event_type: 'renderer.state',
        payload: {
          state_version: 2,
          state_sha256: 'b'.repeat(64),
          state: { view: 'updated' },
        },
      },
      '*',
    )
    expect(onStatePushed).toHaveBeenCalledWith(2)

    // Same version still latest → no re-push.
    const stateCallsBefore = iframe.contentWindow.postMessage.mock.calls.length
    get.mockResolvedValueOnce({
      items: [
        {
          id: 'st-2',
          session_id: 'sess-1',
          version: 2,
          sha256: 'b'.repeat(64),
          state: { view: 'updated' },
          created_at: '2026-08-05T00:00:01Z',
        },
      ],
      offset: 0,
      limit: 1,
      total: 1,
    })
    await vi.advanceTimersByTimeAsync(SUBAPP_POLL_INTERVAL_MS)
    await flushPromises()
    expect(iframe.contentWindow.postMessage.mock.calls.length).toBe(stateCallsBefore)

    channel.destroy()
  })

  it('stops polling after the bounded attempt budget', async () => {
    vi.useFakeTimers()
    post.mockResolvedValue({}).mockResolvedValueOnce(createSessionResponse('token-0'))
    get.mockResolvedValue(emptyStates())

    const iframe = fakeIframe()
    const channel = createSubappChannel({ getIframe: () => iframe, artifactVersionId: 'av-1' })
    channel.handleIframeLoad()
    await flushPromises()

    await vi.advanceTimersByTimeAsync(
      SUBAPP_POLL_INTERVAL_MS * (SUBAPP_POLL_MAX_ATTEMPTS + 8),
    )
    const pollCount = get.mock.calls.filter(
      ([path]) => path === '/subapps/sessions/sess-1/states',
    ).length
    // Initial poll + one poll per bounded attempt.
    expect(pollCount).toBe(SUBAPP_POLL_MAX_ATTEMPTS + 1)

    // Advancing far beyond the budget does not add further polls.
    await vi.advanceTimersByTimeAsync(SUBAPP_POLL_INTERVAL_MS * 20)
    expect(
      get.mock.calls.filter(([path]) => path === '/subapps/sessions/sess-1/states').length,
    ).toBe(pollCount)

    channel.destroy()
  })

  it('destroys the bridge, clears polling, and terminates the server session', async () => {
    post.mockResolvedValue({}).mockResolvedValueOnce(createSessionResponse('token-0'))
    get.mockResolvedValue(emptyStates())

    const iframe = fakeIframe()
    const channel = createSubappChannel({ getIframe: () => iframe, artifactVersionId: 'av-1' })
    channel.handleIframeLoad()
    await flushPromises()

    const pollCallsBefore = get.mock.calls.length
    channel.destroy()

    expect(post).toHaveBeenCalledWith('/subapps/sessions/sess-1/terminate')
    expect(channel.sessionId()).toBeNull()

    // No further polling after destroy.
    expect(get.mock.calls.length).toBe(pollCallsBefore)

    // Events relayed after destroy are dropped (bridge destroyed).
    dispatchIframeMessage(iframe, componentEvent('choice.selected', {}))
    await flushPromises()
    expect(post.mock.calls.filter(([path]) => String(path).includes('/events')).length).toBe(0)
  })
})

describe('createSubappChannel P3 media injection', () => {
  function mediaPostCalls(iframe: FakeIframe) {
    return iframe.contentWindow.postMessage.mock.calls.filter(
      ([message]) => (message as Record<string, unknown>).event_type === 'renderer.media',
    )
  }

  function stateItem(version: number, state: Record<string, unknown>) {
    return {
      id: `st-${version}`,
      session_id: 'sess-1',
      version,
      sha256: String(version).repeat(64),
      state,
      created_at: '2026-08-05T00:00:00Z',
    }
  }

  it('resolves file: media refs via the files endpoint and posts renderer.media', async () => {
    post.mockResolvedValue({}).mockResolvedValueOnce(createSessionResponse('token-0'))
    getBlob.mockResolvedValue(new Blob(['png-bytes'], { type: 'image/png' }))
    get.mockResolvedValue({
      items: [
        stateItem(1, {
          view: 'result',
          media: [{ id: 'cover', kind: 'image', mime: 'image/png', source: 'file:abc123' }],
        }),
      ],
      offset: 0,
      limit: 1,
      total: 1,
    })

    const iframe = fakeIframe()
    const channel = createSubappChannel({ getIframe: () => iframe, artifactVersionId: 'av-1' })
    channel.handleIframeLoad()
    await flushPromises()

    expect(getBlob).toHaveBeenCalledWith('/files/abc123/content')
    const mediaPosts = mediaPostCalls(iframe)
    expect(mediaPosts).toHaveLength(1)
    const payload = (mediaPosts[0][0] as Record<string, unknown>).payload as Record<string, unknown>
    expect(payload.state_version).toBe(1)
    const media = (payload.media as Array<Record<string, unknown>>)[0]
    expect(media).toMatchObject({ id: 'cover', kind: 'image', mime: 'image/png' })
    expect(media.blob).toBeInstanceOf(Blob)
    channel.destroy()
  })

  it('does not fetch or post media for states without media refs', async () => {
    post.mockResolvedValue({}).mockResolvedValueOnce(createSessionResponse('token-0'))
    get.mockResolvedValue({
      items: [stateItem(1, { view: 'result' })],
      offset: 0,
      limit: 1,
      total: 1,
    })
    const iframe = fakeIframe()
    const channel = createSubappChannel({ getIframe: () => iframe, artifactVersionId: 'av-1' })
    channel.handleIframeLoad()
    await flushPromises()
    expect(mediaPostCalls(iframe)).toHaveLength(0)
    expect(getBlob).not.toHaveBeenCalled()
    channel.destroy()
  })

  it('never fetches a non-file source or a path-traversal file id', async () => {
    post.mockResolvedValue({}).mockResolvedValueOnce(createSessionResponse('token-0'))
    get.mockResolvedValue({
      items: [
        stateItem(1, {
          view: 'result',
          media: [
            { id: 'http', kind: 'image', mime: 'image/png', source: 'http://evil.example/x.png' },
            { id: 'traversal', kind: 'image', mime: 'image/png', source: 'file:../etc/passwd' },
          ],
        }),
      ],
      offset: 0,
      limit: 1,
      total: 1,
    })
    const iframe = fakeIframe()
    const channel = createSubappChannel({ getIframe: () => iframe, artifactVersionId: 'av-1' })
    channel.handleIframeLoad()
    await flushPromises()
    expect(getBlob).not.toHaveBeenCalled()
    expect(mediaPostCalls(iframe)).toHaveLength(0)
    channel.destroy()
  })

  it('clears the injected set when a later state drops media', async () => {
    vi.useFakeTimers()
    post.mockResolvedValue({}).mockResolvedValueOnce(createSessionResponse('token-0'))
    getBlob.mockResolvedValue(new Blob(['png'], { type: 'image/png' }))
    get.mockResolvedValue({
      items: [
        stateItem(1, {
          view: 'result',
          media: [{ id: 'cover', kind: 'image', mime: 'image/png', source: 'file:abc123' }],
        }),
      ],
      offset: 0,
      limit: 1,
      total: 1,
    })
    const iframe = fakeIframe()
    const channel = createSubappChannel({ getIframe: () => iframe, artifactVersionId: 'av-1' })
    channel.handleIframeLoad()
    await flushPromises()
    expect(mediaPostCalls(iframe)).toHaveLength(1)

    get.mockResolvedValueOnce({
      items: [stateItem(2, { view: 'plain' })],
      offset: 0,
      limit: 1,
      total: 1,
    })
    await vi.advanceTimersByTimeAsync(SUBAPP_POLL_INTERVAL_MS)
    await flushPromises()

    const mediaPosts = mediaPostCalls(iframe)
    expect(mediaPosts).toHaveLength(2)
    const lastPayload = (mediaPosts[1][0] as Record<string, unknown>).payload as Record<string, unknown>
    expect(lastPayload.state_version).toBe(2)
    expect(lastPayload.media).toEqual([])
    channel.destroy()
  })

  it('drops a media batch resolved after a newer state was delivered', async () => {
    vi.useFakeTimers()
    post.mockResolvedValue({}).mockResolvedValueOnce(createSessionResponse('token-0'))
    let resolveBlob!: (blob: Blob) => void
    const pendingBlob = new Promise<Blob>((resolve) => {
      resolveBlob = resolve
    })
    getBlob.mockReturnValueOnce(pendingBlob)
    get.mockResolvedValueOnce({
      items: [
        stateItem(1, {
          view: 'result',
          media: [{ id: 'cover', kind: 'image', mime: 'image/png', source: 'file:abc123' }],
        }),
      ],
      offset: 0,
      limit: 1,
      total: 1,
    })
    const iframe = fakeIframe()
    const channel = createSubappChannel({ getIframe: () => iframe, artifactVersionId: 'av-1' })
    channel.handleIframeLoad()
    await flushPromises()

    // A newer media-free state (version 2) is delivered before version 1's blob
    // resolves, so version 1 is superseded.
    get.mockResolvedValueOnce({
      items: [stateItem(2, { view: 'plain' })],
      offset: 0,
      limit: 1,
      total: 1,
    })
    await vi.advanceTimersByTimeAsync(SUBAPP_POLL_INTERVAL_MS)
    await flushPromises()

    resolveBlob(new Blob(['png'], { type: 'image/png' }))
    await flushPromises()

    expect(mediaPostCalls(iframe)).toHaveLength(0)
    channel.destroy()
  })

  it('uses a custom mediaResolver and reports onMediaPushed', async () => {
    const customResolver = vi.fn(async () => [
      {
        id: 'cover',
        kind: 'image' as const,
        mime: 'image/png',
        blob: new Blob(['png'], { type: 'image/png' }),
      },
    ])
    const onMediaPushed = vi.fn()
    post.mockResolvedValue({}).mockResolvedValueOnce(createSessionResponse('token-0'))
    get.mockResolvedValue({
      items: [
        stateItem(1, {
          view: 'result',
          media: [{ id: 'cover', kind: 'image', mime: 'image/png', source: 'file:abc123' }],
        }),
      ],
      offset: 0,
      limit: 1,
      total: 1,
    })
    const iframe = fakeIframe()
    const channel = createSubappChannel({
      getIframe: () => iframe,
      artifactVersionId: 'av-1',
      mediaResolver: customResolver,
      onMediaPushed,
    })
    channel.handleIframeLoad()
    await flushPromises()
    expect(customResolver).toHaveBeenCalled()
    expect(mediaPostCalls(iframe)).toHaveLength(1)
    expect(onMediaPushed).toHaveBeenCalledWith(1, 1)
    channel.destroy()
  })

  it('does not resolve media when mediaResolver is null (disabled)', async () => {
    post.mockResolvedValue({}).mockResolvedValueOnce(createSessionResponse('token-0'))
    get.mockResolvedValue({
      items: [
        stateItem(1, {
          view: 'result',
          media: [{ id: 'cover', kind: 'image', mime: 'image/png', source: 'file:abc123' }],
        }),
      ],
      offset: 0,
      limit: 1,
      total: 1,
    })
    const iframe = fakeIframe()
    const channel = createSubappChannel({
      getIframe: () => iframe,
      artifactVersionId: 'av-1',
      mediaResolver: null,
    })
    channel.handleIframeLoad()
    await flushPromises()
    expect(getBlob).not.toHaveBeenCalled()
    expect(mediaPostCalls(iframe)).toHaveLength(0)
    channel.destroy()
  })
})
