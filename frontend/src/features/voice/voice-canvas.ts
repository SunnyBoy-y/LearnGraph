import type { Message, MessagePart } from "@/types/sessions";
import type { VoiceRenderUpdate } from "./voice-session-controller";
import { mergeVoiceTrace, retireVoiceRows, voiceTurnParentRowId } from "./voice-rows";
import { shouldCommitVoiceRender } from "./voice-render-policy";
import { markStreamTextUpdate } from "@/lib/stream-text-epoch";

/** Shared by the live event listener and regression tests; one row and part per turn. */
export function applyVoiceCanvasUpdate(current: Message[], item: VoiceRenderUpdate, workspaceId: string, sessionId: string): Message[] {
  if (!item.id || !shouldCommitVoiceRender(item)) return current;
  const text = item.text.trim();
  const removals = item.removes ?? [];
  const retireTurnSegments = item.retireTurnSegments ?? "";
      const id = `temp-voice-${item.id}`;
      const status = item.deliveryStatus === "failed"
        ? "failed"
        : item.pending
          ? "pending"
          : item.final
            ? "completed"
            : "streaming";
      // Keep the full LLM draft visible while sentence-level audio markers
      // advance. A playback marker must not roll the canvas back to only the
      // sentence currently being spoken.
      const displayText = text;
      // 官方 `bot-output` 宣布过的段落，按到达顺序；每段自带"已读游标"。
      const captions =
        item.role === "assistant" ? (item.captionParts ?? []) : [];
      // 整段回答仍是**一个块**（不是一句一段）：这一块里既有已经点亮的句子，也有还没
      // 朗读的灰字（官方 Karaoke 的预读）。灰字只活在前端内存里，刷新/打断即消失。
      const live = !item.final && captions.length > 0;
      const parts: MessagePart[] = captions.length
        ? [
            {
              id: `${id}-text`,
              type: "text" as const,
              status: (live
                ? "streaming"
                : "completed") as MessagePart["status"],
              content: displayText,
              sequence: 0,
              // 每段原样交给渲染器：Karaoke 模式下它把未读部分画成灰字。
              data: {
                kind: "final_answer",
                          voice_captions: {
                  parts: captions.map((caption) => ({
                    text: caption.text,
                    segmentId: caption.segmentId,
                    spokenChars: caption.spokenChars,
                    status: caption.status,
                    willBeSpoken: caption.willBeSpoken,
                    playbackStarted: caption.playbackStarted,
                  })),
                },
              },
            },
          ]
        : [
            {
              id: `${id}-text`,
              type: "text" as const,
              status,
              content: displayText,
              sequence: 0,
              data: { kind: "final_answer" },
            },
          ];
      const trace: Record<string, unknown> = mergeVoiceTrace(undefined, {
        voice_turn: true,
        voice_segment: Boolean(item.voiceSegment),
        interrupted: Boolean(item.interrupted),
        event_id: item.eventId,
        turn_id: item.turnId,
        client_message_id: item.clientMessageId,
        authoritative: Boolean(item.authoritative || item.final),
        retryable: item.deliveryStatus === "failed",
        voice_failure_reason: item.failureReason ?? null,
      });
        const kept = retireVoiceRows(current, {
          removes: removals,
          retireTurnSegments,
        });
        // 导师行要挂到同一回合的本地用户行上（见 `voiceTurnParentRowId`）：这样即便
        // 客户端从没拿到服务端的回合 id，画布仍能顺着"持久化用户行 → 持久化回答行的
        // parent_message_id"这条链找到它的孪生行，而不是把落库的那一条当成另一条回答
        // 并排画出来。
        const parentRowId =
          item.role === "assistant"
            ? voiceTurnParentRowId(kept, {
                turnId: item.turnId,
                clientMessageId: item.clientMessageId,
              })
            : null;
        // A per-segment row is matched by its own id only: every segment of a
        // turn shares the turn id, so a turn-id match would rewrite one segment
        // with another segment's text.
        const existing = kept.find((message) => message.id === id) ?? (item.voiceSegment
          ? undefined
          : kept.find((message) =>
              message.role === item.role &&
              message.provider_trace?.voice_segment !== true &&
              ((item.turnId && message.provider_trace?.turn_id === item.turnId) ||
                (item.clientMessageId && message.provider_trace?.client_message_id === item.clientMessageId)),
            ));
        const liveParts = parts.map((part) => {
          if (part.type !== "text") return part;
          const previous = existing?.parts.find((candidate) => candidate.type === "text");
          part = { ...part, id: previous?.id ?? part.id };
          return item.final ? part : markStreamTextUpdate(part, previous, !displayText.startsWith(previous?.content ?? ""));
        });
        if (existing) {
          if (existing.provider_trace?.authoritative === true && item.role === "assistant" && !item.final) return kept;
          if (!displayText) return kept;
          return kept.map((message) => message.id === existing.id
            ? {
                ...message,
                content: displayText,
                status,
                parts: liveParts,
                parent_message_id: parentRowId ?? message.parent_message_id,
                provider_trace: mergeVoiceTrace(message.provider_trace, trace),
              }
            : message);
        }
        if (!displayText) return kept;
        const voiceMessage: Message = {
          id,
          workspace_id: workspaceId,
          session_id: sessionId,
          parent_message_id: parentRowId,
          role: item.role,
          version: 1,
          status,
          content: displayText,
          parts: liveParts,
          provider_trace: trace,
          created_at: item.createdAt || new Date().toISOString(),
        };
        // A late authoritative user echo belongs before its answer, not at the end.
        const answerIndex = item.role === "user" && item.turnId
          ? kept.findIndex((message) => message.role === "assistant" && message.provider_trace?.turn_id === item.turnId)
          : -1;
        return answerIndex < 0 ? [...kept, voiceMessage] : [...kept.slice(0, answerIndex), voiceMessage, ...kept.slice(answerIndex)];

}
