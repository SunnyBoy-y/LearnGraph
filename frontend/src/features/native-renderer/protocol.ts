import type { MessageCreateRequest, MessagePart } from '@/types/sessions'
export const RENDERER_OVERLAY_SELECTOR = '[role="dialog"][data-state="open"], [role="alertdialog"][data-state="open"], dialog[open], .fullscreen-preview--expanded, :fullscreen, button[title="Exit fullscreen"], button[aria-label="Exit fullscreen"]'
import type { UnknownRecord } from '@/types/common'

/** Native already owns this message state; the original Web fold owns its UI. */
export interface NativeThinkingChainPayload {
  messageStatus: string
  startedAt?: string
  finalAnswerStarted: boolean
  thinkingDurationMs?: number
  defaultOpen: boolean
}

export interface NativeSuggestionsPayload {
  anchorMessageId: string
  anchorSessionId: string
  anchorVersion: number
  completedMessageFingerprint: string
  providerId?: string
  modelId?: string
}

export interface NativeRenderPayload {
  messageId: string
  sessionId: string
  workspaceId: string
  parts: MessagePart[]
  siblingParts: MessagePart[]
  streaming: boolean
  dark: boolean
  surface?: 'message' | 'graph' | 'suggestions'
  showStreamIndicator?: boolean
  providerTrace?: UnknownRecord
  thinkingChain?: NativeThinkingChainPayload
  suggestions?: NativeSuggestionsPayload
}

export type NativeSendRequest = {
  content: string
  graph_action: 'none' | 'propose_update'
  generation_mode: 'text'
  graph_id?: string
  node_ids?: string[]
  analysis_only?: boolean
  ui_action?: MessageCreateRequest['ui_action']
  /** False means populate the composer's draft, matching learngraph:compose. */
  auto_send?: boolean
}

export interface NativeRendererBridge {
  ready(): void
  resize(height: number): void
  send(json: string): void
  navigate(path: string): void
  refresh(): void
  failed(message: string): void
  download(url: string, filename?: string): void
  saveBase64(dataUrl: string, filename?: string): void
  clearAuth(): void
  overlay?(open: boolean): void
  drawer?(event: { phase: 'start' | 'move' | 'end' | 'cancel'; side: 'left' | 'right'; dx: number; dy: number }): void
}

declare global {
  interface Window {
    LearnGraphRenderer?: NativeRendererBridge
    __lgNativeInitial?: NativeRenderPayload
    __lgRender?: (payload: NativeRenderPayload | string) => void
    __lgDismissOverlay?: () => void
  }
}

export function normalizePayload(value: unknown): NativeRenderPayload {
  const input = typeof value === 'string' ? JSON.parse(value) as unknown : value
  if (!input || typeof input !== 'object') throw new Error('消息渲染数据无效')
  const record = input as Record<string, unknown>
  if (typeof record.messageId !== 'string' || typeof record.sessionId !== 'string' || !Array.isArray(record.parts)) {
    throw new Error('消息渲染缺少消息、会话或 Parts')
  }
  const validateParts = (parts: unknown[]): MessagePart[] => parts.map((part) => {
    if (!part || typeof part !== 'object' || typeof (part as MessagePart).id !== 'string' || typeof (part as MessagePart).type !== 'string') {
      throw new Error('Message Part 缺少标识或类型')
    }
    return part as MessagePart
  })
  const chain = record.thinkingChain && typeof record.thinkingChain === 'object' && !Array.isArray(record.thinkingChain)
    ? record.thinkingChain as Record<string, unknown> : undefined
  const thinkingChain: NativeThinkingChainPayload | undefined = chain && typeof chain.messageStatus === 'string' ? {
    messageStatus: chain.messageStatus,
    finalAnswerStarted: chain.finalAnswerStarted === true,
    defaultOpen: chain.defaultOpen === true,
    ...(typeof chain.startedAt === 'string' ? { startedAt: chain.startedAt } : {}),
    ...(typeof chain.thinkingDurationMs === 'number' && Number.isFinite(chain.thinkingDurationMs) && chain.thinkingDurationMs >= 0
      ? { thinkingDurationMs: chain.thinkingDurationMs } : {}),
  } : undefined
  const rawSuggestions = record.suggestions && typeof record.suggestions === 'object' && !Array.isArray(record.suggestions)
    ? record.suggestions as Record<string, unknown> : undefined
  const suggestions: NativeSuggestionsPayload | undefined = rawSuggestions &&
    typeof rawSuggestions.anchorMessageId === 'string' && !!rawSuggestions.anchorMessageId &&
    typeof rawSuggestions.anchorSessionId === 'string' && !!rawSuggestions.anchorSessionId &&
    typeof rawSuggestions.anchorVersion === 'number' && Number.isSafeInteger(rawSuggestions.anchorVersion) && rawSuggestions.anchorVersion > 0 &&
    typeof rawSuggestions.completedMessageFingerprint === 'string' ? {
      anchorMessageId: rawSuggestions.anchorMessageId, anchorSessionId: rawSuggestions.anchorSessionId,
      anchorVersion: rawSuggestions.anchorVersion, completedMessageFingerprint: rawSuggestions.completedMessageFingerprint,
      ...(typeof rawSuggestions.providerId === 'string' && rawSuggestions.providerId ? { providerId: rawSuggestions.providerId } : {}),
      ...(typeof rawSuggestions.modelId === 'string' && rawSuggestions.modelId ? { modelId: rawSuggestions.modelId } : {}),
    } : undefined
  return {
    messageId: record.messageId,
    sessionId: record.sessionId,
    workspaceId: typeof record.workspaceId === 'string' ? record.workspaceId : '',
    parts: validateParts(record.parts),
    siblingParts: validateParts(Array.isArray(record.siblingParts) ? record.siblingParts : record.parts),
    streaming: record.streaming === true,
    dark: record.dark === true,
    ...(record.surface === 'graph' || record.surface === 'message' || record.surface === 'suggestions' ? { surface: record.surface } : {}),
    ...(typeof record.showStreamIndicator === 'boolean' ? { showStreamIndicator: record.showStreamIndicator } : {}),
    ...(record.providerTrace && typeof record.providerTrace === 'object' && !Array.isArray(record.providerTrace) ? { providerTrace: record.providerTrace as UnknownRecord } : {}),
    ...(thinkingChain ? { thinkingChain } : {}),
    ...(suggestions ? { suggestions } : {}),
  }
}

/** Measure content, never document.scrollHeight (which includes the native viewport). */
export function contentHeight(rectHeight: number, scrollHeight: number, dialogHeight = 0): number {
  return Math.max(1, Math.ceil(Math.max(rectHeight, scrollHeight, dialogHeight)))
}

/** Preserve the existing compose event's node context and graph-change intent. */
export function composeRequest(detail: Record<string, unknown>, sessionId: string, learning?: Record<string, unknown>): NativeSendRequest | null {
  if (typeof detail.content !== 'string' || !detail.content.trim()) return null
  const context = !learning?.sessionId || learning.sessionId === sessionId ? learning : undefined
  const graphId = typeof detail.graphId === 'string' ? detail.graphId : typeof context?.graphId === 'string' ? context.graphId : undefined
  const nodeIds = Array.isArray(detail.nodeIds) ? detail.nodeIds.filter((id): id is string => typeof id === 'string' && !!id && id.length <= 36).slice(0, 8)
    : typeof context?.nodeId === 'string' ? [context.nodeId] : []
  const graphAction = detail.graphAction === 'propose_update' ? 'propose_update' : 'none'
  if (graphAction === 'propose_update' && (!graphId || graphId.length > 36)) throw new Error('当前图谱标识缺失，未发起图谱变更')
  return {
    content: detail.content, graph_action: graphAction, generation_mode: 'text', auto_send: detail.autoSend === true,
    ...(graphAction === 'propose_update' ? { graph_id: graphId } : {}), ...(nodeIds.length ? { node_ids: nodeIds } : {}),
  }
}
