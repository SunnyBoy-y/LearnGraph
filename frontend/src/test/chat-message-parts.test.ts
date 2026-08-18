import { describe, expect, it } from 'vitest'

import { groupAnswerParts } from '@/components/chat/question-set-pager'
import {
  artifactLayout,
  groupPartsForDisplay,
  isInlineLayoutArtifactPart,
  isSideLayoutArtifactPart,
} from '@/features/chat/chat-message-parts'
import type { MessagePart } from '@/types/sessions'

function part(overrides: Partial<MessagePart> & { id: string; type: MessagePart['type'] }): MessagePart {
  return { status: 'completed', ...overrides }
}

function firstChain(segments: ReturnType<typeof groupPartsForDisplay>) {
  return segments.find((segment) => segment.kind === 'chain')
}

function firstAnswer(segments: ReturnType<typeof groupPartsForDisplay>) {
  return segments.find((segment) => segment.kind === 'parts')
}

describe('groupPartsForDisplay chain/answer split', () => {
  it('hoists reasoning parts emitted after the final-answer boundary into the chain', () => {
    // OpenAI Responses streams the reasoning summary as a trailing output item:
    // reasoning_content → final answer text → reasoning_summary. The summary
    // must render inside the thinking chain, never below the answer body.
    const parts: MessagePart[] = [
      part({ id: 'r1', type: 'reasoning_content', content: '思考中…', sequence: 0 }),
      part({
        id: 'a1',
        type: 'text',
        content: '答案正文',
        sequence: 1,
        data: { kind: 'final_answer' },
      }),
      part({ id: 'r2', type: 'reasoning_summary', content: '推理摘要', sequence: 2 }),
    ]

    const segments = groupPartsForDisplay(parts)
    const chain = firstChain(segments)
    const answer = firstAnswer(segments)

    expect(segments).toHaveLength(2)
    expect(segments[0]?.kind).toBe('chain')
    expect(segments[1]?.kind).toBe('parts')
    expect(chain?.kind === 'chain' ? chain.parts.map((item) => item.id) : []).toEqual(['r1', 'r2'])
    expect(answer?.kind === 'parts' ? answer.parts.map((item) => item.id) : []).toEqual(['a1'])
  })

  it('cuts exactly once at a live boundary and keeps post-answer text in order', () => {
    const parts: MessagePart[] = [
      part({ id: 'r1', type: 'reasoning_content', content: '思考中…', sequence: 0 }),
      part({ id: 'a1', type: 'text', content: '第一段', sequence: 1 }),
      part({ id: 'a2', type: 'text', content: '第二段', sequence: 2 }),
    ]

    const segments = groupPartsForDisplay(parts, {
      boundary: { finalPartId: 'a1' },
    })
    const chain = firstChain(segments)
    const answer = firstAnswer(segments)

    expect(chain?.kind === 'chain' ? chain.parts.map((item) => item.id) : []).toEqual(['r1'])
    expect(answer?.kind === 'parts' ? answer.parts.map((item) => item.id) : []).toEqual([
      'a1',
      'a2',
    ])
  })

  it('legacy fallback (no boundary) still keeps reasoning above the answer', () => {
    const parts: MessagePart[] = [
      part({ id: 'a1', type: 'text', content: '答案正文' }),
      part({ id: 'r1', type: 'reasoning_summary', content: '推理摘要' }),
    ]

    const segments = groupPartsForDisplay(parts)
    const chain = firstChain(segments)
    const answer = firstAnswer(segments)

    expect(segments).toHaveLength(2)
    expect(segments[0]?.kind).toBe('chain')
    expect(segments[1]?.kind).toBe('parts')
    expect(chain?.kind === 'chain' ? chain.parts.map((item) => item.id) : []).toEqual(['r1'])
    expect(answer?.kind === 'parts' ? answer.parts.map((item) => item.id) : []).toEqual(['a1'])
  })

  it('keeps reasoning at its stream position inside the chain', () => {
    // A tool call may stream before the reasoning that follows it; the chain
    // renders 思考过程 rows interleaved with tool rows in stream order — the
    // reasoning after a tool call stays right below that tool, never aggregated
    // at the top.
    const parts: MessagePart[] = [
      part({ id: 't1', type: 'tool_call', sequence: 0, data: { tool_name: 'x' } }),
      part({ id: 'r1', type: 'reasoning_content', content: '思考中…', sequence: 1 }),
      part({
        id: 'a1',
        type: 'text',
        content: '答案正文',
        sequence: 2,
        data: { kind: 'final_answer' },
      }),
      part({ id: 'r2', type: 'reasoning_summary', content: '推理摘要', sequence: 3 }),
    ]

    const segments = groupPartsForDisplay(parts)
    const chain = firstChain(segments)

    expect(chain?.kind === 'chain' ? chain.parts.map((item) => item.id) : []).toEqual([
      't1',
      'r1',
      'r2',
    ])
  })

  it('hoists artifacts produced before the boundary into the answer body', () => {
    // Sandbox images / magic cards / components produced mid-turn must render
    // in the answer body (where the final answer references them), never hidden
    // inside the collapsed thinking chain.
    const parts: MessagePart[] = [
      part({ id: 'r1', type: 'reasoning_content', content: '思考中…', sequence: 0 }),
      part({ id: 'img1', type: 'image', sequence: 1, data: { url: 'x' } }),
      part({ id: 'card1', type: 'magic_card', sequence: 2, data: { title: 'x' } }),
      part({ id: 't1', type: 'tool_call', sequence: 3, data: { tool_name: 'x' } }),
      part({
        id: 'a1',
        type: 'text',
        content: '答案正文',
        sequence: 4,
        data: { kind: 'final_answer' },
      }),
    ]

    const segments = groupPartsForDisplay(parts)
    const chain = firstChain(segments)
    const answer = firstAnswer(segments)

    expect(chain?.kind === 'chain' ? chain.parts.map((item) => item.id) : []).toEqual([
      'r1',
      't1',
    ])
    // Artifacts keep stream order and sit ahead of the final-answer text.
    expect(answer?.kind === 'parts' ? answer.parts.map((item) => item.id) : []).toEqual([
      'img1',
      'card1',
      'a1',
    ])
  })

  it('live stream without a boundary still surfaces artifacts outside the chain', () => {
    const parts: MessagePart[] = [
      part({ id: 't1', type: 'tool_call', sequence: 0, data: { tool_name: 'x' } }),
      part({ id: 'img1', type: 'sandbox_artifact', sequence: 1, data: { kind: 'html' } }),
      part({ id: 'r1', type: 'reasoning_content', content: '思考中…', sequence: 2 }),
    ]

    const segments = groupPartsForDisplay(parts, { live: true })
    const chain = firstChain(segments)
    const answer = firstAnswer(segments)

    expect(chain?.kind === 'chain' ? chain.parts.map((item) => item.id) : []).toEqual([
      't1',
      'r1',
    ])
    expect(answer?.kind === 'parts' ? answer.parts.map((item) => item.id) : []).toEqual([
      'img1',
    ])
  })

  it('keeps narration that anchors an artifact in the answer body, in stream order', () => {
    // "下面先看整体结构：" introduces the image: it must render ABOVE the
    // image, not hidden inside the collapsed thinking chain.
    const parts: MessagePart[] = [
      part({
        id: 'n1',
        type: 'text',
        content: '下面先看整体结构：',
        sequence: 0,
        data: { kind: 'plan_narration' },
      }),
      part({ id: 't1', type: 'tool_call', sequence: 1, data: { tool_name: 'generate' } }),
      part({ id: 'img1', type: 'image', sequence: 2, data: { url: 'x' } }),
      part({
        id: 'a1',
        type: 'text',
        content: '答案正文',
        sequence: 3,
        data: { kind: 'final_answer' },
      }),
    ]

    const segments = groupPartsForDisplay(parts)
    const chain = firstChain(segments)
    const answer = firstAnswer(segments)

    expect(chain?.kind === 'chain' ? chain.parts.map((item) => item.id) : []).toEqual(['t1'])
    expect(answer?.kind === 'parts' ? answer.parts.map((item) => item.id) : []).toEqual([
      'n1',
      'img1',
      'a1',
    ])
  })

  it('keeps pure process narration inside the chain when no artifact follows', () => {
    // "我先查一下官方文档…" is followed by a search tool with no visible
    // artifact — forcing it into the body would make the UX worse.
    const parts: MessagePart[] = [
      part({
        id: 'n1',
        type: 'text',
        content: '我先查一下官方文档…',
        sequence: 0,
        data: { kind: 'plan_narration' },
      }),
      part({ id: 't1', type: 'tool_call', sequence: 1, data: { tool_name: 'search_web' } }),
      part({
        id: 'a1',
        type: 'text',
        content: '答案正文',
        sequence: 2,
        data: { kind: 'final_answer' },
      }),
    ]

    const segments = groupPartsForDisplay(parts)
    const chain = firstChain(segments)
    const answer = firstAnswer(segments)

    expect(chain?.kind === 'chain' ? chain.parts.map((item) => item.id) : []).toEqual([
      'n1',
      't1',
    ])
    expect(answer?.kind === 'parts' ? answer.parts.map((item) => item.id) : []).toEqual(['a1'])
  })

  it('keeps post-artifact explanation narration in the answer body too', () => {
    // "从图中可以看到…" explains the image that just appeared: backward
    // adjacency also counts as anchoring.
    const parts: MessagePart[] = [
      part({
        id: 'n1',
        type: 'text',
        content: '下面先看整体结构：',
        sequence: 0,
        data: { kind: 'plan_narration' },
      }),
      part({ id: 't1', type: 'tool_call', sequence: 1, data: { tool_name: 'generate' } }),
      part({ id: 'img1', type: 'image', sequence: 2, data: { url: 'x' } }),
      part({
        id: 'n2',
        type: 'text',
        content: '从图中可以看到，核心是 Broker…',
        sequence: 3,
        data: { kind: 'plan_narration' },
      }),
      part({
        id: 'a1',
        type: 'text',
        content: '答案正文',
        sequence: 4,
        data: { kind: 'final_answer' },
      }),
    ]

    const segments = groupPartsForDisplay(parts)
    const answer = firstAnswer(segments)

    expect(answer?.kind === 'parts' ? answer.parts.map((item) => item.id) : []).toEqual([
      'n1',
      'img1',
      'n2',
      'a1',
    ])
  })

  it('only lifts the narration directly adjacent to an artifact', () => {
    // nA ("我先查一下文档…") is separated from the image by nB, so nA stays
    // in the chain while nB (immediately before the image) moves to the body.
    const parts: MessagePart[] = [
      part({
        id: 'nA',
        type: 'text',
        content: '我先查一下文档…',
        sequence: 0,
        data: { kind: 'plan_narration' },
      }),
      part({
        id: 'nB',
        type: 'text',
        content: '下面先看整体结构：',
        sequence: 1,
        data: { kind: 'plan_narration' },
      }),
      part({ id: 't1', type: 'tool_call', sequence: 2, data: { tool_name: 'generate' } }),
      part({ id: 'img1', type: 'image', sequence: 3, data: { url: 'x' } }),
      part({
        id: 'a1',
        type: 'text',
        content: '答案正文',
        sequence: 4,
        data: { kind: 'final_answer' },
      }),
    ]

    const segments = groupPartsForDisplay(parts)
    const chain = firstChain(segments)
    const answer = firstAnswer(segments)

    expect(chain?.kind === 'chain' ? chain.parts.map((item) => item.id) : []).toEqual([
      'nA',
      't1',
    ])
    expect(answer?.kind === 'parts' ? answer.parts.map((item) => item.id) : []).toEqual([
      'nB',
      'img1',
      'a1',
    ])
  })

  it('legacy messages without kind marks keep every text in the body, interleaved', () => {
    // Old-protocol history has no kind/boundary marks: the legacy partition
    // already interleaves text and artifacts by ordinal — must not regress to
    // hiding narration inside the chain.
    const parts: MessagePart[] = [
      part({ id: 't1', type: 'tool_call', sequence: 0, data: { tool_name: 'x' } }),
      part({ id: 'n1', type: 'text', content: '下面先看整体结构：', sequence: 1 }),
      part({ id: 'img1', type: 'image', sequence: 2, data: { url: 'x' } }),
      part({ id: 'a1', type: 'text', content: '答案正文', sequence: 3 }),
    ]

    const segments = groupPartsForDisplay(parts)
    const chain = firstChain(segments)
    const answer = firstAnswer(segments)

    expect(chain?.kind === 'chain' ? chain.parts.map((item) => item.id) : []).toEqual(['t1'])
    expect(answer?.kind === 'parts' ? answer.parts.map((item) => item.id) : []).toEqual([
      'n1',
      'img1',
      'a1',
    ])
  })

  it('empty streaming text placeholders stay invisible inside the chain', () => {
    const parts: MessagePart[] = [
      part({ id: 't1', type: 'tool_call', sequence: 0, data: { tool_name: 'x' } }),
      part({ id: 'emptyText', type: 'text', status: 'pending', content: '', sequence: 1 }),
      part({ id: 'img1', type: 'image', sequence: 2, data: { url: 'x' } }),
    ]

    const segments = groupPartsForDisplay(parts)
    const chain = firstChain(segments)
    const answer = firstAnswer(segments)

    expect(chain?.kind === 'chain' ? chain.parts.map((item) => item.id) : []).toEqual([
      't1',
      'emptyText',
    ])
    expect(answer?.kind === 'parts' ? answer.parts.map((item) => item.id) : []).toEqual(['img1'])
  })

  it('live final-answer text streams into the body once the boundary is known', () => {
    const parts: MessagePart[] = [
      part({ id: 't1', type: 'tool_call', sequence: 0, data: { tool_name: 'x' } }),
      part({ id: 'img1', type: 'image', sequence: 1, data: { url: 'x' } }),
      part({ id: 'a1', type: 'text', status: 'streaming', content: '答案正文', sequence: 2 }),
    ]

    const segments = groupPartsForDisplay(parts, {
      boundary: { finalPartId: 'a1' },
      live: true,
    })
    const answer = firstAnswer(segments)

    expect(answer?.kind === 'parts' ? answer.parts.map((item) => item.id) : []).toEqual([
      'img1',
      'a1',
    ])
  })
})

describe('artifact layout hints', () => {
  it('artifactLayout returns the validated layout when present', () => {
    const partSide = part({ id: 'p1', type: 'image', data: { layout: 'side' } })
    const partInline = part({ id: 'p2', type: 'magic_card', data: { layout: 'inline' } })
    const partNone = part({ id: 'p3', type: 'image', data: {} })
    const partUnknown = part({ id: 'p4', type: 'image', data: { layout: 'weird' } })

    expect(artifactLayout(partSide)).toBe('side')
    expect(artifactLayout(partInline)).toBe('inline')
    expect(artifactLayout(partNone)).toBeUndefined()
    expect(artifactLayout(partUnknown)).toBeUndefined()
  })

  it('isSideLayoutArtifactPart / isInlineLayoutArtifactPart classify by layout', () => {
    expect(isSideLayoutArtifactPart(part({ id: 'p1', type: 'image', data: { layout: 'side' } }))).toBe(true)
    expect(isSideLayoutArtifactPart(part({ id: 'p2', type: 'magic_card', data: { layout: 'inline' } }))).toBe(false)
    expect(isSideLayoutArtifactPart(part({ id: 'p3', type: 'text', data: { layout: 'side' } }))).toBe(false)
    expect(isInlineLayoutArtifactPart(part({ id: 'p4', type: 'chart', data: { layout: 'inline' } }))).toBe(true)
    expect(isInlineLayoutArtifactPart(part({ id: 'p5', type: 'image', data: {} }))).toBe(false)
  })
})

describe('groupAnswerParts side pairs', () => {
  it('pairs a text segment with the side-layout artifact right after it', () => {
    const parts: MessagePart[] = [
      part({ id: 't1', type: 'text', content: '下面看这张图：', sequence: 0 }),
      part({ id: 'img1', type: 'image', sequence: 1, data: { url: 'x', layout: 'side' } }),
      part({ id: 't2', type: 'text', content: '后续文字', sequence: 2 }),
    ]
    const groups = groupAnswerParts(parts)
    expect(groups).toHaveLength(2)
    expect(groups[0]).toEqual({
      kind: 'side_pair',
      text: expect.objectContaining({ id: 't1' }),
      artifact: expect.objectContaining({ id: 'img1' }),
    })
    expect(groups[1]).toEqual({
      kind: 'part',
      part: expect.objectContaining({ id: 't2' }),
    })
  })

  it('does not pair when the side artifact is the first part (no introducing text)', () => {
    const parts: MessagePart[] = [
      part({ id: 'img1', type: 'image', sequence: 0, data: { url: 'x', layout: 'side' } }),
      part({ id: 't1', type: 'text', content: '后面文字', sequence: 1 }),
    ]
    const groups = groupAnswerParts(parts)
    expect(groups).toHaveLength(2)
    expect(groups[0]?.kind).toBe('part')
    expect(groups[1]?.kind).toBe('part')
  })

  it('keeps block / inline artifacts as plain parts (no pairing)', () => {
    const parts: MessagePart[] = [
      part({ id: 't1', type: 'text', content: '介绍', sequence: 0 }),
      part({ id: 'block1', type: 'image', sequence: 1, data: { url: 'x', layout: 'block' } }),
      part({ id: 'inline1', type: 'magic_card', sequence: 2, data: { title: 'x', layout: 'inline' } }),
    ]
    const groups = groupAnswerParts(parts)
    expect(groups.map((group) => group.kind)).toEqual(['part', 'part', 'part'])
  })
})
