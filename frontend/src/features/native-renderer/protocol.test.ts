import { describe, expect, it } from 'vitest'
import { composeRequest, contentHeight, normalizePayload, RENDERER_OVERLAY_SELECTOR } from './protocol'

describe('native renderer payloads', () => {
  it('keeps an explicit follow-up surface and exact inherited/current-version anchor', () => {
    const suggestions = { anchorMessageId: 'a', anchorSessionId: 'source', anchorVersion: 2,
      completedMessageFingerprint: 'u:1:completed|a:2:completed', providerId: 'p', modelId: 'm' }
    const payload = normalizePayload({ messageId: 'a-suggestions', sessionId: 'branch', parts: [], surface: 'suggestions', suggestions })
    expect(payload.surface).toBe('suggestions')
    expect(payload.suggestions).toEqual(suggestions)
    expect(normalizePayload({ ...payload, suggestions: { ...suggestions, anchorVersion: 0 } }).suggestions).toBeUndefined()
    expect(normalizePayload({ ...payload, suggestions: [] }).suggestions).toBeUndefined()
  })
  it('preserves native thinking boundaries and defaults without inventing invalid timing', () => {
    const input = { messageId: 'process', sessionId: 's', parts: [], thinkingChain: {
      messageStatus: 'streaming', startedAt: '2026-10-18T12:00:00Z', finalAnswerStarted: true,
      thinkingDurationMs: 0, defaultOpen: false,
    } }
    expect(normalizePayload(input).thinkingChain).toEqual(input.thinkingChain)
    expect(normalizePayload({ ...input, thinkingChain: { messageStatus: 'failed', thinkingDurationMs: -1 } }).thinkingChain)
      .toEqual({ messageStatus: 'failed', finalAnswerStarted: false, defaultOpen: false })
    expect(normalizePayload({ ...input, thinkingChain: [] }).thinkingChain).toBeUndefined()
    expect(normalizePayload({ ...input, thinkingChain: { defaultOpen: false } }).thinkingChain).toBeUndefined()
    expect(normalizePayload({ messageId: 'answer', sessionId: 's', parts: [] }).thinkingChain).toBeUndefined()
  })

  it('recognizes both Radix dialogs and Streamdown full-screen portals with role button', () => {
    const container = document.createElement('div')
    container.innerHTML = '<div role="dialog" data-state="closed"></div><button title="View fullscreen"></button>'
    expect(container.querySelector(RENDERER_OVERLAY_SELECTOR)).toBeNull()
    container.innerHTML = '<div role="button" class="fixed inset-0"><button type="button" title="Exit fullscreen"></button></div>'
    expect(container.querySelector(RENDERER_OVERLAY_SELECTOR)?.getAttribute('title')).toBe('Exit fullscreen')
    container.innerHTML = '<div role="dialog" data-state="open"><button data-slot="dialog-close"></button></div>'
    expect(container.querySelector(RENDERER_OVERLAY_SELECTOR)?.getAttribute('role')).toBe('dialog')
  })
  it('keeps original IDs, schema data and full siblings for citations and grouped components', () => {
    const component = { id: 'p1', type: 'component', status: 'completed', data: { component_type: 'option_group', props: { options: ['a', 'b'] } } }
    const source = { id: 'sources', type: 'source_list', status: 'completed', data: { sources: [{ url: 'https://example.com' }] } }
    const input = { messageId: 'm1', sessionId: 's1', workspaceId: 'w1', parts: [component], siblingParts: [component, source], streaming: false, dark: true }
    const value = normalizePayload(input)
    expect(value.parts[0]).toBe(component)
    expect(value.siblingParts).toEqual([component, source])
    expect(value.dark).toBe(true)
    expect(normalizePayload(JSON.stringify(input))).toEqual(value)
  })

  it('does not invent or discard future registered part types and rejects malformed input', () => {
    const input = { messageId: 'm1', sessionId: 's1', parts: [{ id: 'future', type: 'future_type', status: 'completed', data: { content: '<script>not executed</script>' } }] }
    expect(normalizePayload(input).parts[0].type).toBe('future_type')
    expect(normalizePayload(input).siblingParts).toEqual(input.parts)
    expect(() => normalizePayload({ messageId: 'm1', sessionId: 's1', parts: [{}] })).toThrow('缺少标识')
    expect(() => normalizePayload(null)).toThrow('无效')
  })

  it('measures content independently of native viewport height and rounds only upward', () => {
    expect(contentHeight(120.2, 120)).toBe(121)
    expect(contentHeight(120.2, 120)).toBe(contentHeight(120.2, 120))
    expect(contentHeight(120.2, 120, 800)).toBe(800)
    expect(contentHeight(0, 0)).toBe(1)
  })

  it('preserves the original stream indicator and terminal provider timing without enabling it on other surfaces', () => {
    const providerTrace = { first_token_ms: 125, output_tokens: 48, generation_duration_ms: 900 }
    const input = { messageId: 'm1', sessionId: 's1', parts: [], streaming: true, showStreamIndicator: true, providerTrace }
    expect(normalizePayload(input).showStreamIndicator).toBe(true)
    expect(normalizePayload(input).providerTrace).toBe(providerTrace)
    expect(normalizePayload({ ...input, surface: 'graph', showStreamIndicator: false }).showStreamIndicator).toBe(false)
    expect(normalizePayload({ ...input, providerTrace: [] }).providerTrace).toBeUndefined()
    expect(normalizePayload({ messageId: 'm2', sessionId: 's1', parts: [] }).showStreamIndicator).toBeUndefined()
  })

  it('preserves the graph surface and graph-change compose context without leaking another session', () => {
    expect(normalizePayload({ messageId: 'g', sessionId: 's1', parts: [], surface: 'graph' }).surface).toBe('graph')
    expect(composeRequest({ content: '拆分节点', graphAction: 'propose_update', autoSend: true }, 's1', { sessionId: 's1', graphId: 'g1', nodeId: 'n1' }))
      .toEqual({ content: '拆分节点', graph_action: 'propose_update', generation_mode: 'text', auto_send: true, graph_id: 'g1', node_ids: ['n1'] })
    expect(composeRequest({ content: '预填追问' }, 's2', { sessionId: 's1', graphId: 'g1', nodeId: 'n1' }))
      .toEqual({ content: '预填追问', graph_action: 'none', generation_mode: 'text', auto_send: false })
    expect(() => composeRequest({ content: '拆分节点', graphAction: 'propose_update' }, 's2', { sessionId: 's1', graphId: 'g1' })).toThrow('图谱标识缺失')
  })
})
