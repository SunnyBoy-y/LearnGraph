import { describe, expect, it } from 'vitest'

import { groupPartsForDisplay } from '@/features/chat/chat-message-parts'
import type { Message, MessagePart } from '@/types/sessions'

/**
 * 复制 chat-pages.tsx 中 appendPart 的核心合并逻辑（流式 delta 累积），
 * 用真实后端 SSE 事件序列（StubModelProvider 验证输出）驱动，
 * 验证极速/思考模式下文本 part 最终落在 answer body（而非思考链）。
 */
function appendPart(
  parts: MessagePart[],
  incoming: MessagePart,
): MessagePart[] {
  const visibleParts =
    incoming.type === 'image'
      ? parts.filter((part) => part.data?.optimistic !== true)
      : parts;
  const index = visibleParts.findIndex((part) => part.id === incoming.id);
  const nextContent =
    typeof incoming.content === 'string'
      ? incoming.content
      : `${visibleParts[index]?.content ?? ''}${incoming.content_delta ?? ''}`;
  if (index === -1)
    return [
      ...visibleParts,
      {
        ...incoming,
        content: nextContent,
        sequence:
          typeof incoming.sequence === 'number'
            ? incoming.sequence
            : visibleParts.length,
      },
    ];
  return visibleParts.map((part, partIndex) =>
    partIndex === index
      ? {
          ...part,
          ...incoming,
          data: { ...(part.data ?? {}), ...(incoming.data ?? {}) },
          content: nextContent,
          sequence:
            typeof incoming.sequence === 'number'
              ? incoming.sequence
              : (part.sequence ?? partIndex),
        }
      : part,
  );
}

/** 修复后的解析：answer.started 边界字段从 data.payload 读取（真实后端位置）。 */
const applyStreamUpdatesFixed = (
  message: Message,
  updates: Array<{ type?: string; part?: MessagePart; payload?: Record<string, unknown> }>,
): Message =>
  updates.reduce<Message>((current, data) => {
    const eventType = data.type ?? '';
    if (eventType === 'answer.started') {
      const payload =
        typeof data.payload === 'object' && data.payload !== null
          ? data.payload
          : {};
      return {
        ...current,
        finalAnswerStarted: {
          finalPartId:
            typeof payload.final_part_id === 'string'
              ? payload.final_part_id
              : undefined,
          boundarySequence:
            typeof payload.boundary_sequence === 'number'
              ? payload.boundary_sequence
              : undefined,
          thinkingDurationMs:
            typeof payload.thinking_duration_ms === 'number'
              ? payload.thinking_duration_ms
              : undefined,
        },
      } as Message;
    }
    if (data.part) {
      return {
        ...current,
        parts: appendPart(current.parts, data.part),
      } as Message;
    }
    return current;
  }, message);

/** 修复前的解析：从顶层 data.final_part_id 读取（真实后端顶层没有该字段）。 */
const applyStreamUpdatesBroken = (
  message: Message,
  updates: Array<{ type?: string; part?: MessagePart; payload?: Record<string, unknown> }>,
): Message =>
  updates.reduce<Message>((current, data) => {
    const eventType = data.type ?? '';
    const raw = data as Record<string, unknown>;
    if (eventType === 'answer.started') {
      return {
        ...current,
        finalAnswerStarted: {
          finalPartId:
            typeof raw.final_part_id === 'string'
              ? raw.final_part_id
              : undefined,
          boundarySequence:
            typeof raw.boundary_sequence === 'number'
              ? raw.boundary_sequence
              : undefined,
        },
      } as Message;
    }
    if (data.part) {
      return {
        ...current,
        parts: appendPart(current.parts, data.part),
      } as Message;
    }
    return current;
  }, message);

function message(overrides: Partial<Message> & { id: string }): Message {
  return {
    workspace_id: 'ws',
    session_id: 'sess',
    parent_message_id: null,
    role: 'assistant',
    version: 1,
    status: 'streaming',
    content: '',
    parts: [],
    provider_trace: {},
    created_at: new Date().toISOString(),
    ...overrides,
  }
}

/** 极速/思考模式真实 SSE 事件序列（verify_stream_fast_structured.py / verify_stream_thinking.py 输出）。 */
function streamEvents(textId: string, reasoningId?: string) {
  const events: Array<{ type?: string; part?: MessagePart; payload?: Record<string, unknown> }> = [
    { type: 'part.started', part: { id: 'ack-persisted', type: 'acknowledgement', status: 'pending', content: '正在思考' } as MessagePart },
    { type: 'part.started', part: { id: textId, type: 'text', status: 'pending', content: '' } as MessagePart },
  ];
  if (reasoningId) {
    events.push(
      { type: 'part.delta', part: { id: reasoningId, type: 'reasoning_content', status: 'streaming', content_delta: '让我想想：', sequence: 2, data: {} } as MessagePart },
      { type: 'part.delta', part: { id: reasoningId, type: 'reasoning_content', status: 'streaming', content_delta: '先分析问题。', sequence: 2, data: {} } as MessagePart },
    );
  }
  events.push(
    // answer.started：final_part_id / boundary_sequence 在 payload 里（后端 _event_envelope）
    { type: 'answer.started', payload: { final_part_id: textId, boundary_sequence: 1, thinking_duration_ms: 1234 } },
    { type: 'part.delta', part: { id: textId, type: 'text', status: 'streaming', content_delta: '你好', sequence: 1, data: {} } as MessagePart },
    { type: 'part.delta', part: { id: textId, type: 'text', status: 'streaming', content_delta: '，世界', sequence: 1, data: {} } as MessagePart },
  );
  return events;
}

describe('极速/思考模式 answer.started 边界解析（payload 修复）', () => {
  it('修复前：从顶层 data.* 读取 → 边界无效 → 流式文本落入思考链（复现 bug）', () => {
    const textId = 'text-record-id-bug';
    const assistant = message({
      id: 'temp-assistant-bug',
      parts: [{ id: 'temp-ack', type: 'acknowledgement', status: 'pending', content: '' }],
    });

    const state = streamEvents(textId).reduce<Message>(
      (current, event) => applyStreamUpdatesBroken(current, [event]),
      assistant,
    );
    // 边界字段确实丢失
    expect(state.finalAnswerStarted?.finalPartId).toBeUndefined();
    expect(state.finalAnswerStarted?.boundarySequence).toBeUndefined();

    const segments = groupPartsForDisplay(state.parts, {
      boundary: state.finalAnswerStarted,
      live: state.status === 'streaming',
    });
    const chain = segments.find((s) => s.kind === 'chain');
    const answer = segments.find((s) => s.kind === 'parts');
    // 流式文本被错误地折叠进思考链，answer body 里没有正文
    expect(chain?.kind === 'chain' ? chain.parts.some((p) => p.type === 'text' && p.content === '你好，世界') : false).toBe(true);
    expect(answer?.kind === 'parts' ? answer.parts.some((p) => p.type === 'text' && p.content === '你好，世界') : false).toBe(false);
  })

  it('修复后：从 payload 读取 → 边界有效 → 极速模式文本流式显示在 answer body', () => {
    const textId = 'text-record-id-1';
    const assistant = message({
      id: 'temp-assistant-1',
      parts: [{ id: 'temp-ack-1', type: 'acknowledgement', status: 'pending', content: '' }],
    });

    const state = streamEvents(textId).reduce<Message>(
      (current, event) => applyStreamUpdatesFixed(current, [event]),
      assistant,
    );
    expect(state.finalAnswerStarted?.finalPartId).toBe(textId);
    expect(state.finalAnswerStarted?.boundarySequence).toBe(1);
    expect(state.finalAnswerStarted?.thinkingDurationMs).toBe(1234);

    const textPart = state.parts.find((p) => p.type === 'text');
    expect(textPart?.content).toBe('你好，世界');

    const segments = groupPartsForDisplay(state.parts, {
      boundary: state.finalAnswerStarted,
      live: state.status === 'streaming',
    });
    const chain = segments.find((s) => s.kind === 'chain');
    const answer = segments.find((s) => s.kind === 'parts');
    expect(answer?.kind === 'parts' ? answer.parts.some((p) => p.type === 'text' && p.content === '你好，世界') : false).toBe(true);
    expect(chain?.kind === 'chain' ? chain.parts.some((p) => p.type === 'text' && p.content === '你好，世界') : false).toBe(false);
  })

  it('修复后：思考模式 reasoning 在 chain、文本流式显示在 answer body', () => {
    const textId = 'text-record-id-2';
    const reasoningId = 'reasoning-record-id-2';
    const assistant = message({
      id: 'temp-assistant-2',
      parts: [{ id: 'temp-ack-2', type: 'acknowledgement', status: 'pending', content: '' }],
    });

    const state = streamEvents(textId, reasoningId).reduce<Message>(
      (current, event) => applyStreamUpdatesFixed(current, [event]),
      assistant,
    );

    const segments = groupPartsForDisplay(state.parts, {
      boundary: state.finalAnswerStarted,
      live: state.status === 'streaming',
    });
    const chain = segments.find((s) => s.kind === 'chain');
    const answer = segments.find((s) => s.kind === 'parts');
    expect(chain?.kind === 'chain' ? chain.parts.some((p) => p.type === 'reasoning_content') : false).toBe(true);
    expect(answer?.kind === 'parts' ? answer.parts.some((p) => p.type === 'text' && p.content === '你好，世界') : false).toBe(true);
  })
})
