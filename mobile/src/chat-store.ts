/**
 * 聊天状态：历史分页 + SSE 流式增量（与桌面端同语义的 part.* 事件应用）。
 */

import { create } from 'zustand'
import * as api from './api/endpoints'
import { createUuid } from './storage'
import { useAppStore, useToastStore } from './store'
import type {
  AnswerStartedEvent,
  Message,
  MessageCompletedEvent,
  MessagePart,
  MessagePartStreamEvent,
  SessionMessageStreamData,
} from './types'

export type LiveStatus = 'idle' | 'streaming' | 'done' | 'error' | 'cancelled'

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function eventKind(data: SessionMessageStreamData): string {
  if (typeof data !== 'object' || data === null) return ''
  const d = data as Record<string, unknown>
  return (typeof d.event === 'string' ? d.event : typeof d.type === 'string' ? d.type : '') || ''
}

function upsertPart(parts: MessagePart[], part: MessagePart): MessagePart[] {
  const idx = parts.findIndex((p) => p.id === part.id)
  if (idx === -1) return [...parts, part]
  const next = [...parts]
  next[idx] = part
  return next
}

function buildAssistantMessage(
  sessionId: string,
  messageId: string,
  parts: MessagePart[],
  status: string,
  finalAnswer?: { finalPartId?: string; boundarySequence?: number; thinkingDurationMs?: number },
): Message {
  return {
    id: messageId,
    workspace_id: '',
    session_id: sessionId,
    parent_message_id: null,
    role: 'assistant',
    version: 1,
    status,
    content: parts
      .filter((p) => p.type === 'text' && typeof p.content === 'string')
      .map((p) => p.content ?? '')
      .join('\n'),
    parts,
    provider_trace: {},
    created_at: new Date().toISOString(),
    ...(finalAnswer ? { finalAnswerStarted: finalAnswer } : {}),
  }
}

function makeUserMessage(sessionId: string, content: string): Message {
  const id = `local-${createUuid()}`
  const part: MessagePart = {
    id: `local-part-${createUuid()}`,
    type: 'text',
    status: 'completed',
    content,
    sequence: 0,
  }
  return {
    id,
    workspace_id: '',
    session_id: sessionId,
    parent_message_id: null,
    role: 'user',
    version: 1,
    status: 'completed',
    content,
    parts: [part],
    provider_trace: {},
    created_at: new Date().toISOString(),
  }
}

interface ChatState {
  sessionId: string | null
  messages: Message[]
  hasMoreBefore: boolean
  messagesLoading: boolean
  messagesError: string | null

  liveMessageId: string | null
  liveParts: MessagePart[]
  liveStatus: LiveStatus
  liveError: string | null
  lastEventId: string | null
  controller: AbortController | null
  finalAnswerStarted: { finalPartId?: string; boundarySequence?: number; thinkingDurationMs?: number } | null
  thinkingExpanded: boolean

  open: (sessionId: string) => Promise<void>
  reset: () => void
  loadOlder: () => Promise<void>
  send: (text: string) => Promise<void>
  retry: (messageId: string) => Promise<void>
  cancel: () => Promise<void>
  syncAfterDrop: () => Promise<void>
  setThinkingExpanded: (v: boolean) => void
}

export const useChatStore = create<ChatState>((set, get) => {
  /** 单条 SSE 事件的增量应用；返回是否收到 message.completed */
  function applyEvent(data: SessionMessageStreamData): boolean {
    const kind = eventKind(data)
    const msgId = (data as MessagePartStreamEvent).message_id

    if (kind === 'part.started' || kind === 'tool.started') {
      const part = (data as MessagePartStreamEvent).part
      if (!part) return false
      set((s) => ({
        liveMessageId: msgId,
        liveParts: upsertPart(s.liveParts, part),
      }))
      return false
    }

    if (kind === 'part.delta') {
      const part = (data as MessagePartStreamEvent).part
      if (!part) return false
      const delta = part.content_delta
      const full = typeof part.content === 'string' ? part.content : null
      set((s) => ({
        liveParts: s.liveParts.map((p) => {
          if (p.id !== part.id) return p
          if (delta) {
            return {
              ...p,
              status: 'streaming',
              content: (p.content ?? '') + delta,
            }
          }
          if (full !== null) {
            return { ...p, status: 'streaming', content: full }
          }
          return p
        }),
      }))
      return false
    }

    if (kind === 'part.replaced') {
      const part = (data as MessagePartStreamEvent).part
      if (!part) return false
      set((s) => ({ liveParts: upsertPart(s.liveParts, part) }))
      return false
    }

    if (kind === 'part.completed' || kind === 'tool.completed') {
      const part = (data as MessagePartStreamEvent).part
      if (!part) return false
      set((s) => ({
        liveParts: upsertPart(s.liveParts, { ...part, status: 'completed' }),
      }))
      return false
    }

    if (kind === 'part.failed') {
      const part = (data as MessagePartStreamEvent).part
      if (!part) return false
      set((s) => ({
        liveParts: upsertPart(s.liveParts, { ...part, status: 'failed' }),
      }))
      return false
    }

    if (kind === 'answer.started') {
      const ev = data as AnswerStartedEvent
      set({
        finalAnswerStarted: {
          finalPartId: ev.final_part_id,
          boundarySequence: ev.boundary_sequence,
          thinkingDurationMs: ev.thinking_duration_ms,
        },
      })
      return false
    }

    if (kind === 'message.completed') {
      const ev = data as MessageCompletedEvent
      const { sessionId, liveParts, finalAnswerStarted } = get()
      const message = buildAssistantMessage(
        sessionId ?? '',
        ev.message_id,
        liveParts,
        ev.status,
        finalAnswerStarted ?? undefined,
      )
      const failed = ev.status === 'failed'
      set((s) => ({
        messages: [...s.messages, message],
        liveParts: [],
        liveMessageId: null,
        liveStatus: failed ? 'error' : 'done',
        liveError: failed ? '生成失败' : null,
        finalAnswerStarted: null,
      }))
      return true
    }

    if (kind === 'error') {
      const d = data as Record<string, unknown>
      const message = typeof d.message === 'string' ? d.message : '生成失败'
      set({ liveStatus: 'error', liveError: message })
      return false
    }

    return false
  }

  function applyReplayEvents(events: SessionMessageStreamData[]): boolean {
    let completed = false
    for (const ev of events) {
      if (applyEvent(ev)) completed = true
    }
    return completed
  }

  return {
    sessionId: null,
    messages: [],
    hasMoreBefore: false,
    messagesLoading: false,
    messagesError: null,

    liveMessageId: null,
    liveParts: [],
    liveStatus: 'idle',
    liveError: null,
    lastEventId: null,
    controller: null,
    finalAnswerStarted: null,
    thinkingExpanded: false,

    async open(sessionId) {
      get().controller?.abort()
      set({
        sessionId,
        messages: [],
        hasMoreBefore: false,
        messagesLoading: true,
        messagesError: null,
        liveMessageId: null,
        liveParts: [],
        liveStatus: 'idle',
        liveError: null,
        lastEventId: null,
        controller: null,
        finalAnswerStarted: null,
      })
      try {
        const page = await api.listMessages(sessionId, { limit: 50 })
        set({
          messages: page.items,
          hasMoreBefore: page.has_more_before,
          messagesLoading: false,
        })
      } catch (error) {
        set({
          messagesLoading: false,
          messagesError: error instanceof Error ? error.message : String(error),
        })
      }
    },

    reset() {
      get().controller?.abort()
      set({
        sessionId: null,
        messages: [],
        hasMoreBefore: false,
        messagesLoading: false,
        messagesError: null,
        liveMessageId: null,
        liveParts: [],
        liveStatus: 'idle',
        liveError: null,
        lastEventId: null,
        controller: null,
        finalAnswerStarted: null,
      })
    },

    async loadOlder() {
      const { sessionId, messages, hasMoreBefore, messagesLoading } = get()
      if (!sessionId || !hasMoreBefore || messagesLoading || messages.length === 0) return
      const oldest = messages[0]
      set({ messagesLoading: true })
      try {
        const page = await api.listMessages(sessionId, { limit: 50, before_id: oldest.id })
        set((s) => ({
          messages: [...page.items, ...s.messages],
          hasMoreBefore: page.has_more_before,
          messagesLoading: false,
        }))
      } catch (error) {
        set({
          messagesLoading: false,
          messagesError: error instanceof Error ? error.message : String(error),
        })
      }
    },

    async send(text) {
      const { sessionId, liveStatus } = get()
      if (!sessionId || !text.trim()) return
      if (liveStatus === 'streaming') {
        useToastStore.getState().showToast('info', '上一条回复还在生成中')
        return
      }
      const idempotencyKey = createUuid()
      const userMessage = makeUserMessage(sessionId, text)
      const controller = new AbortController()
      set((s) => ({
        messages: [...s.messages, userMessage],
        liveMessageId: null,
        liveParts: [],
        liveStatus: 'streaming',
        liveError: null,
        lastEventId: null,
        controller,
        finalAnswerStarted: null,
      }))

      const consume = async (
        generator: AsyncGenerator<{ event: string; id?: string; data: SessionMessageStreamData; rawData: string }>,
      ) => {
        let completed = false
        for await (const ev of generator) {
          if (ev.id) set({ lastEventId: ev.id })
          if (applyEvent(ev.data)) completed = true
        }
        return completed
      }

      try {
        const generator = api.streamMessage(
          sessionId,
          { content: text, generation_mode: 'text' },
          {
            signal: controller.signal,
            headers: { 'Idempotency-Key': idempotencyKey },
          },
        )
        const completed = await consume(generator)
        if (!completed) {
          // 流正常结束但未收到 message.completed：分离式流（网络被掐/服务端
          // 继续生成）。保留 liveParts 并进入可「同步」的错误态。
          set((s) => ({
            liveStatus: s.liveStatus === 'streaming' ? 'error' : s.liveStatus,
            liveError:
              s.liveStatus === 'streaming'
                ? '连接中断，生成已在电脑端继续'
                : s.liveError,
          }))
        } else {
          set((s) => ({ liveStatus: s.liveStatus === 'streaming' ? 'done' : s.liveStatus }))
        }
        void maybeAutoTitle(sessionId, userMessage.id)
      } catch (error) {
        if (isAbort(error)) {
          set({ liveStatus: 'cancelled' })
        } else {
          set({
            liveStatus: 'error',
            liveError:
              error instanceof Error ? error.message : '连接中断，生成已在电脑端继续',
          })
        }
      } finally {
        set({ controller: null })
        void useAppStore.getState().loadSessions()
      }
    },

    async retry(messageId) {
      const { sessionId, liveStatus } = get()
      if (!sessionId || liveStatus === 'streaming') return
      const controller = new AbortController()
      set({
        liveMessageId: messageId,
        liveParts: [],
        liveStatus: 'streaming',
        liveError: null,
        lastEventId: null,
        controller,
        finalAnswerStarted: null,
      })
      try {
        const generator = api.retryMessage(sessionId, messageId, {}, { signal: controller.signal })
        let completed = false
        for await (const ev of generator) {
          if (ev.id) set({ lastEventId: ev.id })
          if (applyEvent(ev.data)) completed = true
        }
        if (completed) {
          set((s) => ({ liveStatus: s.liveStatus === 'streaming' ? 'done' : s.liveStatus }))
        } else {
          set((s) => ({
            liveStatus: s.liveStatus === 'streaming' ? 'error' : s.liveStatus,
            liveError: s.liveStatus === 'streaming' ? '连接中断，生成已在电脑端继续' : s.liveError,
          }))
        }
      } catch (error) {
        if (isAbort(error)) {
          set({ liveStatus: 'cancelled' })
        } else {
          set({ liveStatus: 'error', liveError: error instanceof Error ? error.message : String(error) })
        }
      } finally {
        set({ controller: null })
        void useAppStore.getState().loadSessions()
      }
    },

    async cancel() {
      const { sessionId, liveMessageId, controller } = get()
      controller?.abort()
      if (sessionId && liveMessageId) {
        try {
          await api.cancelMessage(sessionId, liveMessageId)
        } catch {
          // 服务端已自然结束等情况忽略
        }
      }
      set({ liveStatus: 'cancelled', controller: null })
    },

    /** 断线补同步：生成在电脑端继续，这里回放事件把最新状态捞回来 */
    async syncAfterDrop() {
      const { sessionId, liveMessageId, lastEventId } = get()
      if (!sessionId || !liveMessageId) return
      try {
        const events = await api.messageEvents(sessionId, liveMessageId, {
          after_event_id: lastEventId ?? undefined,
        })
        const completed = applyReplayEvents(events)
        set((s) => ({
          liveStatus: completed ? 'done' : s.liveStatus === 'error' ? 'done' : s.liveStatus,
          liveError: null,
        }))
        void useAppStore.getState().loadSessions()
      } catch (error) {
        set({ liveError: error instanceof Error ? error.message : '刷新失败' })
      }
    },

    setThinkingExpanded(v) {
      set({ thinkingExpanded: v })
    },
  }
})

/** 首条对话完成后自动生成标题（乐观锁：expected_title 传当前标题） */
async function maybeAutoTitle(sessionId: string, sourceMessageId: string): Promise<void> {
  const app = useAppStore.getState()
  const session = app.activeSession
  if (!session || session.id !== sessionId) return
  const expected = session.title || '新学习会话'
  try {
    const updated = await api.autoTitleSession(sessionId, {
      source_message_id: sourceMessageId,
      expected_title: expected,
    })
    app.setActiveSessionMeta(updated)
  } catch {
    // 标题生成失败不打扰用户
  }
}
