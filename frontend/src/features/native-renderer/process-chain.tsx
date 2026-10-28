import type { ReactNode } from 'react'
import { ThinkingChain } from '@/components/chat/thinking-chain'
import { ReasoningSummaryRow } from '@/components/chat/reasoning-summary-row'
import { isReasoningTextPart, thinkingDurationSeconds, toolCallCount } from '@/features/chat/chat-message-parts'
import type { UnknownRecord } from '@/types/common'
import type { MessagePart } from '@/types/sessions'
import type { NativeThinkingChainPayload } from './protocol'

/** The same outer fold and inner reasoning rows used by AssistantMessage. */
export function NativeProcessChain({ parts, siblingParts, providerTrace = {}, state, renderPart }: {
  parts: MessagePart[]
  siblingParts: MessagePart[]
  providerTrace?: UnknownRecord
  state: NativeThinkingChainPayload
  renderPart(part: MessagePart): ReactNode
}) {
  return <div data-drawer-swipe="ignore">
    <ThinkingChain chainParts={parts} messageStatus={state.messageStatus}
      startedAt={typeof providerTrace.generation_started_at === 'string' ? providerTrace.generation_started_at : state.startedAt}
      completedDurationSec={thinkingDurationSeconds(providerTrace)}
      finalAnswerStarted={state.finalAnswerStarted} thinkingDurationMs={state.thinkingDurationMs}
      toolCallCount={toolCallCount(siblingParts)} defaultOpen={state.defaultOpen}>
      {parts.map(part => isReasoningTextPart(part)
        ? <ReasoningSummaryRow key={part.id} part={part} streaming={state.messageStatus === 'streaming'} />
        : renderPart(part))}
    </ThinkingChain>
  </div>
}
