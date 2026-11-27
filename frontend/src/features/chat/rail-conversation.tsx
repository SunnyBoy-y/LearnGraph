import type { FormEvent, ReactNode, Ref } from "react";
import { ArrowUp, Square } from "lucide-react";
import { CopyMessageAction } from '@/components/shared/copy-feedback';
import { Message as AiMessage, MessageContent, MessageActions } from "@/components/ai-elements/message";
import { ChatStreamPartRenderer } from "@/components/chat/chat-stream-part-renderer";
import { QuestionSetPager, groupAnswerParts } from "@/components/chat/question-set-pager";
import { SandboxImageStrip } from "@/components/chat/sandbox-image-artifact";
import { ReasoningSummaryRow } from "@/components/chat/reasoning-summary-row";
import { ThinkingChain } from "@/components/chat/thinking-chain";
import { MessageReading } from "@/components/chat/message-reading";
import { StreamStatsAction, StreamStatsBadge } from "@/components/chat/stream-stats-badge";
import { Button } from "@/components/ui/button";
import { InputGroup } from "@/components/ui/input-group";
import { Textarea } from "@/components/ui/textarea";
import { groupPartsForDisplay, isReasoningTextPart, thinkingDurationSeconds, toolCallCount } from "@/features/chat/chat-message-parts";
import type { TrustedComponentAction } from "@/components/chat/trusted-component-renderer";
import { messageCopyText } from "./message-copy";
import type { Message, MessagePart } from "@/types/sessions";

function railMessageParts(message: Message): MessagePart[] {
  if (message.parts.length) return message.parts;
  return message.content
    ? [
        {
          id: `${message.id}-text`,
          type: "text",
          status: "completed",
          content: message.content,
        },
      ]
    : [];
}

function RailMessageContent({
  message,
  onAction,
  thinkingChainDefaultOpen,
}: {
  message: Message;
  onAction?: (action: TrustedComponentAction) => void | Promise<void>;
  thinkingChainDefaultOpen: boolean;
}) {
  const parts = railMessageParts(message);
  const displaySegments = groupPartsForDisplay(parts, {
    boundary: message.finalAnswerStarted,
    live: message.status === "streaming",
  });
  const messageSettled =
    message.status !== "streaming" && message.status !== "pending";
  const renderPart = (part: MessagePart) => (
    <ChatStreamPartRenderer
      interactive={messageSettled}
      key={part.id}
      onAction={onAction}
      part={part}
      settled={messageSettled}
      siblingParts={parts}
      streaming={
        message.status === "streaming" &&
        (part.status === "streaming" || part.status === "pending")
      }
    />
  );
  const renderAnswerParts = (answerParts: MessagePart[]) =>
    groupAnswerParts(answerParts).map((group) => {
      if (group.kind === "question_set") {
        return (
          <QuestionSetPager
            interactive={message.status !== "streaming"}
            key={`question-set-${group.parts.map((part) => part.id).join("-")}`}
            storageKey={group.parts.map((part) => part.id).join(":")}
            onSubmit={
              onAction
                ? (submission) =>
                    onAction({
                      componentId:
                        group.questions[0]?.componentId ?? group.parts[0].id,
                      componentType:
                        group.questions.length > 1
                          ? "question_set"
                          : group.questions[0]?.componentType ?? "question_set",
                      event: "submit",
                      payload: {
                        answer: submission.summaryText,
                        summaryText: submission.summaryText,
                        results: submission.results,
                        labels: submission.results.flatMap((item) => item.labels),
                        graded_count: submission.gradedCount,
                        correct_count: submission.correctCount,
                      },
                    })
                : undefined
            }
            questions={group.questions}
          />
        );
      }
      if (group.kind === "image_strip") {
        return (
          <SandboxImageStrip
            key={`image-strip-${group.parts[0]?.id ?? "empty"}`}
            parts={group.parts}
          />
        );
      }
      if (group.kind === "side_pair") {
        return (
          <div
            className="grid min-w-0 gap-3 sm:grid-cols-2 sm:items-start"
            key={`side-pair-${group.artifact.id}`}
          >
            <div className="min-w-0">{renderPart(group.text)}</div>
            <div className="min-w-0">{renderPart(group.artifact)}</div>
          </div>
        );
      }
      return renderPart(group.part);
    });

  return (
    <div className="right-rail-rich-message__content">
      <MessageReading process={displaySegments.filter(segment => segment.kind === "chain").map(segment => (
          <div className="right-rail-rich-message__chain" key={`chain-${message.id}`}>
            <ThinkingChain
              chainParts={segment.parts}
              completedDurationSec={thinkingDurationSeconds(message.provider_trace)}
              defaultOpen={thinkingChainDefaultOpen}
              finalAnswerStarted={Boolean(message.finalAnswerStarted)}
              messageStatus={message.status}
              renderPart={(part) =>
                isReasoningTextPart(part) ? (
                  <ReasoningSummaryRow
                    key={part.id}
                    part={part}
                    streaming={message.status === "streaming"}
                  />
                ) : (
                  renderPart(part)
                )
              }
              startedAt={
                typeof message.provider_trace.generation_started_at === "string"
                  ? message.provider_trace.generation_started_at
                  : message.created_at
              }
              thinkingDurationMs={message.finalAnswerStarted?.thinkingDurationMs}
              toolCallCount={toolCallCount(parts)}
            />
          </div>
      ))}>
        {displaySegments.filter(segment => segment.kind === "parts").map(segment => (
          <div className="message-answer-segment answer-flow" key={`answer-${message.id}`}>
            {renderAnswerParts(segment.parts)}
          </div>
        ))}
      </MessageReading>
    </div>
  );
}


/** The rail keeps the main conversation's message primitives and reading styles. */
export function RailConversationMessage({ message, onAction, thinkingChainDefaultOpen = false }: {
  message: Message;
  onAction?: (action: TrustedComponentAction) => void | Promise<void>;
  thinkingChainDefaultOpen?: boolean;
}) {
  const user = message.role === "user";
  return (
    <AiMessage from={user ? "user" : "assistant"} data-message-id={message.id}>
      <MessageContent className={user ? undefined : "message-reading-surface message-reading-surface--compact w-full gap-1 overflow-visible"} data-message-content>
        {user ? <>
          {message.parts.filter((part) => ["attachment", "document_selection", "selection_quote"].includes(part.type)).map((part) => (
            <ChatStreamPartRenderer key={part.id} part={part} siblingParts={message.parts} />
          ))}
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        </> : <RailMessageContent message={message} onAction={onAction} thinkingChainDefaultOpen={thinkingChainDefaultOpen} />}
      </MessageContent>
      {!user ? <>
        <StreamStatsBadge messageId={message.id} showCompleted={false} status={message.status} providerTrace={message.provider_trace} />
        {message.status !== "streaming" && message.status !== "pending" ? (
          <MessageActions className="opacity-60 transition-opacity focus-within:opacity-100 hover:opacity-100 has-[[data-state=open]]:opacity-100">
            <CopyMessageAction label="复制全文" scope={message.id} text={messageCopyText(message.parts, message.content)} />
            <StreamStatsAction messageId={message.id} status={message.status} providerTrace={message.provider_trace} />
          </MessageActions>
        ) : null}
      </> : null}
    </AiMessage>
  );
}

export function RailConversationComposer({ value, onChange, onSubmit, onStop, busy, disabled, inputRef, label, placeholder, sendLabel, stopLabel = "停止生成", controls, children }: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  busy: boolean;
  disabled?: boolean;
  inputRef?: Ref<HTMLTextAreaElement>;
  label: string;
  placeholder: string;
  sendLabel: string;
  stopLabel?: string;
  controls?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <form className="rail-chat-composer chat-composer" onSubmit={(event: FormEvent) => { event.preventDefault(); if (!busy && !disabled) onSubmit(); }}>
      {children}
      <InputGroup>
        <Textarea aria-label={label} className="rail-chat-composer__textarea" disabled={disabled} ref={inputRef} rows={1}
          value={value} placeholder={placeholder} onChange={(event) => onChange(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              if (!busy && !disabled && value.trim()) onSubmit();
            }
          }} />
        <div className="rail-chat-composer__actions">
          <div className="rail-chat-composer__controls">{controls}</div>
          {busy ? <Button aria-label={stopLabel} className="chat-composer__submit chat-composer__submit--stop" onClick={onStop} size="icon-sm" type="button" variant="secondary"><Square className="size-3.5" fill="currentColor" strokeWidth={0} /></Button>
            : <Button aria-label={sendLabel} className="chat-composer__submit" disabled={disabled || !value.trim()} size="icon-sm" type="submit"><ArrowUp className="size-4" /></Button>}
        </div>
      </InputGroup>
    </form>
  );
}
