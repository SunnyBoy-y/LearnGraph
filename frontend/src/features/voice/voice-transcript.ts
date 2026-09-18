/**
 * Pure transcript reduction for the full-duplex voice client.
 *
 * The audio pipeline is allowed to lose, duplicate or reorder frames; the
 * transcript must not. Everything here is therefore keyed on server-assigned
 * identifiers — ``event_id``, ``turn_id``, ``sentence_seq`` and a monotonic
 * ``audio_cursor_ms`` — and never on text equality. Two consecutive sentences
 * are allowed to have identical text and must stay two entries; the same
 * sentence delivered twice must collapse to one.
 *
 * Two layers are maintained:
 *  - *speculative*: interim ASR, LLM drafts, sentences queued for playback.
 *    Rollback is allowed and expected.
 *  - *authoritative*: the turn the server committed to (``turn.accepted`` for
 *    the user side, ``turn.finalized`` for the assistant side).
 *
 * This module is deliberately free of React, the DOM and network access so it
 * can be unit tested directly.
 */

export type VoiceTranscriptRole = "user" | "assistant" | "background";
export type VoicePhase = "speculative" | "authoritative";
export type VoiceDeliveryStatus = "pending" | "accepted" | "failed";

export interface TranscriptEntry {
  /** Stable identity of the rendered entry. */
  id: string;
  role: VoiceTranscriptRole;
  text: string;
  final: boolean;
  createdAt: string;
  phase: VoicePhase;
  turnId?: string;
  clientMessageId?: string;
  sentenceSeq?: number;
  audioCursorMs?: number;
  eventId?: string;
  eventSeq?: number;
  interrupted?: boolean;
  pending?: boolean;
  authoritative?: boolean;
  deliveryStatus?: VoiceDeliveryStatus;
  /** Why a settled turn produced no answer (provider error, idle timeout). */
  failureReason?: string;
}

export interface VoiceEventLike {
  event_id?: string;
  event_seq?: number;
  type?: string;
  phase?: string;
  turn_id?: string | null;
  audio_cursor_ms?: number | null;
  causality?: Record<string, unknown>;
  payload?: Record<string, unknown>;
}

export interface TranscriptState {
  entries: TranscriptEntry[];
  /** Every event_id already folded in; the only cross-channel dedupe key. */
  seenEventIds: string[];
  /** turn_id -> highest sentence_seq applied. */
  sentenceHighWater: Record<string, number>;
  /** turn_id -> highest audio_cursor_ms applied. */
  cursorHighWater: Record<string, number>;
  /** client_message_id -> turn_id, learned from turn.accepted. */
  typedTurns: Record<string, string>;
}

export const MAX_SEEN_EVENT_IDS = 2000;

export function emptyTranscriptState(): TranscriptState {
  return {
    entries: [],
    seenEventIds: [],
    sentenceHighWater: {},
    cursorHighWater: {},
    typedTurns: {},
  };
}

function rememberEvent(state: TranscriptState, eventId: string): TranscriptState {
  if (!eventId || state.seenEventIds.includes(eventId)) return state;
  const next = [...state.seenEventIds, eventId];
  return {
    ...state,
    seenEventIds: next.length > MAX_SEEN_EVENT_IDS ? next.slice(-MAX_SEEN_EVENT_IDS) : next,
  };
}

function upsert(state: TranscriptState, entry: TranscriptEntry): TranscriptState {
  const index = state.entries.findIndex((existing) => existing.id === entry.id);
  if (index === -1) return { ...state, entries: [...state.entries, entry] };
  const entries = state.entries.slice();
  entries[index] = { ...entries[index], ...entry };
  return { ...state, entries };
}

/**
 * Drop speculative user drafts that the authoritative entry supersedes.
 *
 * A typed turn starts life as a speculative bubble and is only promoted when
 * the server echoes its ``client_message_id``. Because the speculative entry is
 * keyed by ``turn_id`` while the authoritative one is keyed by
 * ``client_message_id``, promotion would otherwise leave two rows for one
 * utterance — the exact duplication this layer exists to prevent.
 */
function dropSpeculativeUserEntries(state: TranscriptState, turnId: string | undefined): TranscriptState {
  if (!turnId) return state;
  return {
    ...state,
    entries: state.entries.filter(
      (entry) =>
        !(
          entry.role === "user" &&
          entry.phase === "speculative" &&
          (entry.turnId === turnId || entry.turnId === undefined)
        ),
    ),
  };
}

/** Identity of the user-side entry for a turn: typed wins over audio. */
export function userEntryId(turnId: string | undefined, clientMessageId: string | undefined): string {
  if (clientMessageId) return `user-typed-${clientMessageId}`;
  return `user-turn-${turnId ?? "unknown"}`;
}

/** Identity of an assistant sentence: one per (turn, sentence_seq). */
export function assistantSentenceId(turnId: string, sentenceSeq: number | undefined): string {
  return Number.isFinite(sentenceSeq) && Number(sentenceSeq) > 0
    ? `assistant-${turnId}-s${sentenceSeq}`
    : `assistant-${turnId}-live`;
}

function payloadOf(event: VoiceEventLike): Record<string, unknown> {
  return event.payload ?? {};
}

function textOf(event: VoiceEventLike): string {
  const payload = payloadOf(event);
  const value = payload.text ?? payload.content ?? payload.delta ?? "";
  return String(value ?? "");
}

/**
 * Fold one durable event into the transcript.
 *
 * Returns a new state; the input state is never mutated. Repeated or
 * out-of-order deliveries are dropped, not merged, so ordering is stable.
 */
export function reduceVoiceEvent(state: TranscriptState, event: VoiceEventLike): TranscriptState {
  const eventId = String(event.event_id ?? "");
  if (eventId && state.seenEventIds.includes(eventId)) return state;
  const type = String(event.type ?? "");
  const payload = payloadOf(event);
  const turnId = event.turn_id ? String(event.turn_id) : undefined;
  const cursor = typeof event.audio_cursor_ms === "number" ? event.audio_cursor_ms : undefined;
  const sentenceSeqRaw = payload.sentence_seq ?? payload.sentenceSeq;
  const sentenceSeq = Number.isFinite(Number(sentenceSeqRaw)) ? Number(sentenceSeqRaw) : undefined;
  const text = textOf(event);
  const now = new Date().toISOString();

  let next = rememberEvent(state, eventId);

  // Monotonic cursor guard: a reordered caption must not move the cursor back.
  if (cursor !== undefined && turnId) {
    const previous = next.cursorHighWater[turnId] ?? -1;
    if (cursor < previous) return next;
    next = { ...next, cursorHighWater: { ...next.cursorHighWater, [turnId]: cursor } };
  }

  switch (type) {
    case "user.final": {
      const clientMessageId =
        (payload.client_message_id as string | undefined) ??
        (payload.clientMessageId as string | undefined) ??
        (event.causality?.client_message_id as string | undefined);
      const resolvedTurn = turnId ?? `pending-${clientMessageId ?? next.entries.length}`;
      next = dropSpeculativeUserEntries(next, turnId);
      return upsert(next, {
        id: userEntryId(resolvedTurn, clientMessageId),
        role: "user",
        text,
        final: true,
        createdAt: now,
        phase: "authoritative",
        turnId: resolvedTurn,
        clientMessageId,
        audioCursorMs: cursor,
        eventId,
      });
    }
    case "turn.accepted": {
      const clientMessageId =
        (payload.client_message_id as string | undefined) ??
        (event.causality?.client_message_id as string | undefined);
      const acceptedTurn = turnId ?? String(payload.turn_id ?? payload.turnId ?? "");
      const typedTurns = clientMessageId && acceptedTurn
        ? { ...next.typedTurns, [clientMessageId]: acceptedTurn }
        : next.typedTurns;
      next = { ...next, typedTurns };
      if (!clientMessageId) return next;
      // Promote the optimistic pending bubble in place: same entry identity as
      // the speculative typed bubble, so no second user row can appear.
      next = dropSpeculativeUserEntries(next, acceptedTurn);
      return upsert(next, {
        id: userEntryId(acceptedTurn, clientMessageId),
        role: "user",
        text: text || String(payload.user_text ?? payload.text ?? ""),
        final: true,
        createdAt: now,
        phase: "authoritative",
        turnId: acceptedTurn,
        clientMessageId,
        pending: false,
        deliveryStatus: "accepted",
        eventId,
      });
    }
    case "user.interim": {
      const draftId = userEntryId(turnId ?? "live", undefined);
      return upsert(next, {
        id: draftId,
        role: "user",
        text,
        final: false,
        createdAt: now,
        phase: "speculative",
        turnId,
        pending: true,
        eventId,
      });
    }
    case "assistant.llm.delta": {
      const draftTurn = turnId ?? "live";
      return upsert(next, {
        id: `assistant-draft-${draftTurn}`,
        role: "assistant",
        text,
        final: false,
        createdAt: now,
        phase: "speculative",
        turnId,
        eventId,
      });
    }
    case "assistant.sentence.queued":
    case "assistant.sentence.playback_started": {
      if (!turnId) return next;
      // A duplicate or older sentence_seq for the same turn is ignored. A LOST
      // marker is harmless: `turn.finalized` carries the full text.
      if (sentenceSeq !== undefined && sentenceSeq > 0) {
        const previous = next.sentenceHighWater[turnId] ?? 0;
        if (sentenceSeq <= previous) return next;
        next = { ...next, sentenceHighWater: { ...next.sentenceHighWater, [turnId]: sentenceSeq } };
      }
      return upsert(next, {
        id: assistantSentenceId(turnId, sentenceSeq),
        role: "assistant",
        text,
        final: false,
        createdAt: now,
        phase: "speculative",
        turnId,
        sentenceSeq,
        audioCursorMs: cursor,
        eventId,
      });
    }
    case "assistant.sentence.playback_ended":
      return next;
    case "turn.finalized": {
      const role = String(payload.role ?? "assistant");
      const finalizedTurn = turnId ?? "unknown";
      if (role === "user") {
        return upsert(next, {
          id: userEntryId(finalizedTurn, undefined),
          role: "user",
          text,
          final: true,
          createdAt: now,
          phase: "authoritative",
          turnId: finalizedTurn,
          eventId,
        });
      }
      // Replace every speculative assistant fragment for this turn with the one
      // authoritative message. This is what makes a lost, duplicated or
      // reordered TTS marker irrelevant to the final transcript.
      const kept = next.entries.filter(
        (entry) => !(entry.role === "assistant" && entry.turnId === finalizedTurn && entry.phase === "speculative"),
      );
      next = { ...next, entries: kept };
      const failed = payload.failed === true || String(payload.outcome ?? "") === "failed";
      if (failed || !text.trim()) {
        // The turn settled without an answer (provider error or idle timeout).
        // The question must stay visible and be retryable; an empty assistant
        // bubble would be worse than no bubble.
        return {
          ...next,
          entries: next.entries.map((entry) =>
            entry.role === "user" && entry.turnId === finalizedTurn
              ? {
                  ...entry,
                  deliveryStatus: "failed" as VoiceDeliveryStatus,
                  failureReason: String(payload.failure_reason ?? "no_answer"),
                }
              : entry,
          ),
        };
      }
      return upsert(next, {
        id: `assistant-final-${finalizedTurn}`,
        role: "assistant",
        text,
        final: true,
        createdAt: now,
        phase: "authoritative",
        turnId: finalizedTurn,
        audioCursorMs: cursor,
        eventId,
      });
    }
    case "task.activity": {
      const taskId = String(payload.task_id ?? payload.taskId ?? "");
      if (!taskId || !text) return next;
      return upsert(next, {
        id: `background-${eventId || taskId}`,
        role: "background",
        text,
        final: true,
        createdAt: now,
        phase: "authoritative",
        turnId: taskId,
        eventId,
        eventSeq: event.event_seq,
      });
    }
    case "turn.interrupted": {
      if (!turnId) return next;
      // The partial answer the user actually heard stays visible for audit, but
      // it is never promoted to authoritative.
      return {
        ...next,
        entries: next.entries.map((entry) =>
          entry.role === "assistant" && entry.turnId === turnId
            ? { ...entry, interrupted: true, final: true, phase: "speculative" }
            : entry,
        ),
      };
    }
    default:
      return next;
  }
}

/** Fold a batch (replay page or polling response) in server order. */
export function reduceVoiceEvents(state: TranscriptState, events: readonly VoiceEventLike[]): TranscriptState {
  let next = state;
  for (const event of events) next = reduceVoiceEvent(next, event);
  return next;
}

export interface ServerTurnLike {
  turn_id: string;
  user_text?: string;
  assistant_text?: string;
  client_message_id?: string | null;
  finalized_at?: string | null;
  /** "finalized" | "interrupted" | "failed" */
  status?: string;
  failure_reason?: string | null;
}

/**
 * Seed the authoritative transcript from `/turns`-style server records.
 *
 * Used on page load and reconnect so a refresh reproduces the conversation
 * without replaying speculative captions. Entries already present are kept.
 */
export function seedFromServerTurns(state: TranscriptState, turns: readonly ServerTurnLike[]): TranscriptState {
  let next = state;
  for (const turn of turns) {
    const createdAt = turn.finalized_at ?? new Date().toISOString();
    const clientMessageId = turn.client_message_id ?? undefined;
    // A turn that is still running has no assistant text yet and no failure: it
    // must come back as the user's question on its own, not as a dropped turn
    // with a retry badge, otherwise a reload mid-answer nags the user to resend a
    // question the tutor is answering right now.
    const inFlight = turn.status === "accepted";
    const unanswered = !inFlight && (turn.status === "failed" || (!turn.assistant_text && turn.status !== "finalized"));
    if (turn.user_text) {
      next = upsert(next, {
        id: userEntryId(turn.turn_id, clientMessageId),
        role: "user",
        text: turn.user_text,
        final: true,
        createdAt,
        phase: "authoritative",
        turnId: turn.turn_id,
        clientMessageId,
        // A refresh must recover the retry affordance too, otherwise a dropped
        // turn looks like the user simply never got an answer.
        deliveryStatus: unanswered ? "failed" : undefined,
        failureReason: unanswered ? (turn.failure_reason ?? "no_answer") : undefined,
      });
    }
    if (turn.assistant_text) {
      next = upsert(next, {
        id: `assistant-final-${turn.turn_id}`,
        role: "assistant",
        text: turn.assistant_text,
        final: true,
        createdAt,
        phase: "authoritative",
        turnId: turn.turn_id,
      });
    }
  }
  return next;
}

export type VoiceStage = "asr" | "llm" | "tts" | "network";

/**
 * Which pipeline stages are currently unusable, derived from `processor.error`.
 *
 * Only a *degraded* error (the stage exhausted its retry budget) counts: a
 * transient, retryable error is not a degradation and must not flip the call
 * into text mode. ASR loss is the one that forces text input, because the user
 * can no longer be understood by speaking.
 */
export function deriveDegradedStages(
  previous: readonly string[],
  payload: Record<string, unknown>,
): string[] {
  if (payload.degraded !== true) return [...previous];
  const stage = typeof payload.stage === "string" ? payload.stage : "network";
  return previous.includes(stage) ? [...previous] : [...previous, stage];
}

/** True when the user cannot be understood by voice any more. */
export function requiresTextFallback(stages: readonly string[]): boolean {
  return stages.includes("asr");
}

/**
 * A human-readable reason for the degraded banner.
 *
 * Kept separate from the flag so the UI never renders a bare "语音不可用": the
 * user needs to know *what* broke and that their transcript and tasks survive.
 */
export function degradedNotice(stages: readonly string[]): string | null {
  if (stages.length === 0) return null;
  if (stages.includes("asr")) {
    return "语音识别已不可用，已切换为文字输入；本次通话的记录与任务都会保留。";
  }
  if (stages.includes("tts")) {
    return "语音播报已不可用，回答会以文字显示；你仍然可以继续说话。";
  }
  return "语音处理暂时不可用，已切换为文字输入；本次通话的记录与任务都会保留。";
}

export interface ModelPinState {
  requestedModelId: string | null;
  effectiveModelId: string | null;
  repointed: boolean;
  certainty: "repointed" | "next_connection";
  epoch: number;
  at: string;
}

/**
 * Interpret a `context.updated` event as a model-pin statement.
 *
 * Three different things share this event type, and only one of them is about
 * the model:
 *
 * 1. a switch request from the control plane (`reason: "model_switch"`, or any
 *    event that names the requested `model_id` on an older backend);
 * 2. a context-snapshot write (`reason: "context_snapshot"`) — emitted on every
 *    call creation, nothing to do with the model;
 * 3. the running pipeline's report (`origin: "pipeline"`), which answers 1 or 2.
 *
 * Two rules decide whether this event says anything about the model at all, and
 * `null` is the honest answer when it does not (the caller then keeps whatever
 * pin it already had):
 *
 * - a context snapshot (2) is never a model statement, and neither is any other
 *   event that names no model and asks for no switch;
 * - a report (3) never *invents* a pin, because a report carries no user request.
 *
 * After that, the rule that matters: **absence of confirmation is never treated
 * as confirmation.** Only an explicit `repointed: true` may change the model the
 * UI presents as live; anything else keeps the previous effective model and marks
 * the switch as taking effect on the next connection.
 *
 * The snapshot exclusion is what fixed a false banner: every call creation writes
 * a context snapshot, the client replayed that event and rendered
 * "本次通话仍在使用「X」，「新模型」将在下次接通后生效" on a call where nothing
 * was switched at all.
 */
export function deriveModelPin(
  payload: Record<string, unknown>,
  event: { session_epoch?: number },
  previousEffectiveModelId: string | null,
  previousPin: ModelPinState | null = null,
): ModelPinState | null {
  const reportedRequest =
    (payload.model_id as string | undefined) ??
    (payload.requested_model_id as string | undefined) ??
    null;
  const reason = typeof payload.reason === "string" ? payload.reason : "";
  const isPipelineReport = payload.origin === "pipeline";
  const asksForSwitch = reason === "model_switch" || Boolean(reportedRequest);
  // 无关事件（上下文快照等）：不建立、也不改写 pin。
  if (!isPipelineReport && !asksForSwitch) return null;
  // 回执是"对某次请求的答复"：没有请求、也没有既有 pin 时，不能凭它造出一条声明。
  if (isPipelineReport && !asksForSwitch && !previousPin) return null;

  const requested = reportedRequest ?? previousPin?.requestedModelId ?? null;
  const repointed = payload.repointed === true;
  const reported = payload.effective_model_id as string | undefined;
  return {
    requestedModelId: requested,
    effectiveModelId: repointed
      ? (reported ?? previousEffectiveModelId)
      : previousEffectiveModelId,
    repointed,
    certainty: repointed ? "repointed" : "next_connection",
    epoch: Number(payload.applied_epoch ?? event.session_epoch ?? 0),
    at: new Date().toISOString(),
  };
}

/** Ordered entries for rendering: authoritative first, speculative last. */
export function orderedEntries(state: TranscriptState): TranscriptEntry[] {
  return [...state.entries].sort((a, b) => {
    if (a.phase !== b.phase) return a.phase === "authoritative" ? -1 : 1;
    return a.createdAt.localeCompare(b.createdAt);
  });
}

/**
 * Mark a pending typed bubble as failed so the UI can offer a retry.
 *
 * Retrying reuses the same ``client_message_id``, so the server can never open
 * a second turn for one typed message.
 */
export function markTypedFailed(state: TranscriptState, clientMessageId: string): TranscriptState {
  return {
    ...state,
    entries: state.entries.map((entry) =>
      entry.clientMessageId === clientMessageId
        ? { ...entry, pending: true, deliveryStatus: "failed" as VoiceDeliveryStatus, final: false }
        : entry,
    ),
  };
}

/** Entries still waiting for a server acknowledgement. */
export function pendingTypedEntries(state: TranscriptState): TranscriptEntry[] {
  return state.entries.filter(
    (entry) => entry.role === "user" && entry.deliveryStatus === "pending" && entry.clientMessageId,
  );
}
