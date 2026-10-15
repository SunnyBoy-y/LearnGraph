import { describe, expect, it } from "vitest";

import {
  assistantSentenceId,
  degradedNotice,
  deriveDegradedStages,
  deriveModelPin,
  requiresTextFallback,
  emptyTranscriptState,
  orderedEntries,
  reduceVoiceEvent,
  reduceVoiceEvents,
  seedFromServerTurns,
  userEntryId,
  type VoiceEventLike,
} from "./voice-transcript";

let counter = 0;
function event(overrides: Partial<VoiceEventLike> & { type: string }): VoiceEventLike {
  counter += 1;
  return {
    event_id: `ve_${counter}`,
    event_seq: counter,
    phase: "authoritative",
    payload: {},
    ...overrides,
  };
}

const TURN = "turn_1";

describe("voice transcript reducer", () => {
  it("never dedupes by text: two identical sentences stay two entries", () => {
    let state = emptyTranscriptState();
    state = reduceVoiceEvent(
      state,
      event({ type: "assistant.sentence.queued", turn_id: TURN, payload: { text: "好的", sentence_seq: 1 } }),
    );
    state = reduceVoiceEvent(
      state,
      event({ type: "assistant.sentence.queued", turn_id: TURN, payload: { text: "好的", sentence_seq: 2 } }),
    );
    const sentences = orderedEntries(state).filter((entry) => entry.role === "assistant");
    expect(sentences).toHaveLength(2);
    expect(sentences.map((entry) => entry.text)).toEqual(["好的", "好的"]);
  });

  it("collapses a duplicated marker (same sentence_seq) to one entry", () => {
    let state = emptyTranscriptState();
    state = reduceVoiceEvent(
      state,
      event({ type: "assistant.sentence.queued", turn_id: TURN, payload: { text: "第一句", sentence_seq: 1 } }),
    );
    state = reduceVoiceEvent(
      state,
      event({ type: "assistant.sentence.queued", turn_id: TURN, payload: { text: "第一句", sentence_seq: 1 } }),
    );
    expect(orderedEntries(state).filter((entry) => entry.role === "assistant")).toHaveLength(1);
  });

  it("collapses a re-delivered event_id even when the payload differs", () => {
    let state = emptyTranscriptState();
    const duplicated = event({
      type: "assistant.sentence.queued",
      turn_id: TURN,
      payload: { text: "原句", sentence_seq: 1 },
    });
    state = reduceVoiceEvent(state, duplicated);
    state = reduceVoiceEvent(state, { ...duplicated, payload: { text: "原句", sentence_seq: 1 } });
    expect(orderedEntries(state).filter((entry) => entry.role === "assistant")).toHaveLength(1);
  });

  it("ignores an out-of-order marker with a lower audio cursor", () => {
    let state = emptyTranscriptState();
    state = reduceVoiceEvent(
      state,
      event({ type: "assistant.sentence.queued", turn_id: TURN, audio_cursor_ms: 900, payload: { text: "第二句", sentence_seq: 2 } }),
    );
    state = reduceVoiceEvent(
      state,
      event({ type: "assistant.sentence.queued", turn_id: TURN, audio_cursor_ms: 300, payload: { text: "第一句", sentence_seq: 1 } }),
    );
    expect(state.cursorHighWater[TURN]).toBe(900);
    expect(orderedEntries(state)).toHaveLength(1);
  });

  it("produces exactly one authoritative assistant message when every marker was lost", () => {
    let state = emptyTranscriptState();
    state = reduceVoiceEvent(state, event({ type: "turn.accepted", turn_id: TURN, payload: { text: "问题" } }));
    state = reduceVoiceEvent(
      state,
      event({ type: "turn.finalized", turn_id: TURN, audio_cursor_ms: 5_000, payload: { text: "完整回答" } }),
    );
    const assistant = orderedEntries(state).filter((entry) => entry.role === "assistant");
    expect(assistant).toHaveLength(1);
    expect(assistant[0].text).toBe("完整回答");
    expect(assistant[0].phase).toBe("authoritative");
  });

  it("replaces speculative sentences with the authoritative text exactly once", () => {
    let state = emptyTranscriptState();
    for (const seq of [1, 2, 3]) {
      state = reduceVoiceEvent(
        state,
        event({ type: "assistant.sentence.queued", turn_id: TURN, payload: { text: `句子${seq}`, sentence_seq: seq } }),
      );
    }
    state = reduceVoiceEvent(
      state,
      event({ type: "turn.finalized", turn_id: TURN, payload: { text: "完整回答" } }),
    );
    const assistant = orderedEntries(state).filter((entry) => entry.role === "assistant");
    expect(assistant).toHaveLength(1);
    expect(assistant[0].text).toBe("完整回答");

    // Replaying the whole batch (a reconnect) must not add anything back.
    const replayed = reduceVoiceEvents(state, [
      event({ type: "assistant.sentence.queued", turn_id: TURN, payload: { text: "句子1", sentence_seq: 1 } }),
      event({ type: "turn.finalized", turn_id: TURN, payload: { text: "完整回答" } }),
    ]);
    expect(orderedEntries(replayed).filter((entry) => entry.role === "assistant")).toHaveLength(1);
  });

  it("keeps the partial answer on interrupt but never marks it authoritative", () => {
    let state = emptyTranscriptState();
    state = reduceVoiceEvent(
      state,
      event({ type: "assistant.sentence.playback_started", turn_id: TURN, payload: { text: "被听到的半句", sentence_seq: 1 } }),
    );
    state = reduceVoiceEvent(state, event({ type: "turn.interrupted", turn_id: TURN, payload: { reason: "barge_in" } }));
    const assistant = orderedEntries(state).filter((entry) => entry.role === "assistant");
    expect(assistant).toHaveLength(1);
    expect(assistant[0].interrupted).toBe(true);
    expect(assistant[0].phase).toBe("speculative");
  });

  it("promotes a typed pending bubble in place instead of adding a second user row", () => {
    let state = emptyTranscriptState();
    // Optimistic typed bubble (speculative).
    state = reduceVoiceEvent(
      state,
      event({ type: "user.interim", phase: "speculative", payload: { text: "打字的问题" }, turn_id: TURN }),
    );
    state = reduceVoiceEvent(
      state,
      event({
        type: "turn.accepted",
        turn_id: TURN,
        payload: { text: "打字的问题", client_message_id: "cmid-1" },
      }),
    );
    // The ASR-side final for the same turn must not create a second row.
    state = reduceVoiceEvent(
      state,
      event({ type: "user.final", turn_id: TURN, payload: { text: "打字的问题", client_message_id: "cmid-1" } }),
    );
    const users = orderedEntries(state).filter((entry) => entry.role === "user");
    expect(users).toHaveLength(1);
    expect(users[0].id).toBe(userEntryId(TURN, "cmid-1"));
    expect(users[0].phase).toBe("authoritative");
  });

  it("keys audio-only user turns on turn_id", () => {
    let state = emptyTranscriptState();
    state = reduceVoiceEvent(state, event({ type: "user.final", turn_id: TURN, payload: { text: "说的话" } }));
    state = reduceVoiceEvent(state, event({ type: "user.final", turn_id: TURN, payload: { text: "说的话" } }));
    const users = orderedEntries(state).filter((entry) => entry.role === "user");
    expect(users).toHaveLength(1);
    expect(users[0].id).toBe(userEntryId(TURN, undefined));
  });

  it("merges multi-segment ASR finals into one turn entry", () => {
    let state = emptyTranscriptState();
    state = reduceVoiceEvent(state, event({ type: "user.final", turn_id: TURN, payload: { text: "我想问" } }));
    state = reduceVoiceEvent(state, event({ type: "user.final", turn_id: TURN, payload: { text: "我想问一个问题" } }));
    const users = orderedEntries(state).filter((entry) => entry.role === "user");
    expect(users).toHaveLength(1);
    expect(users[0].text).toBe("我想问一个问题");
  });

  it("recovers the authoritative transcript from server turns after a refresh", () => {
    const seeded = seedFromServerTurns(emptyTranscriptState(), [
      { turn_id: "t1", user_text: "第一个问题", assistant_text: "第一个回答", finalized_at: "2026-01-01T00:00:00Z" },
      { turn_id: "t2", user_text: "第二个问题", assistant_text: "第二个回答", client_message_id: "cmid-2", finalized_at: "2026-01-01T00:01:00Z" },
    ]);
    const entries = orderedEntries(seeded);
    expect(entries.filter((entry) => entry.role === "user")).toHaveLength(2);
    expect(entries.filter((entry) => entry.role === "assistant")).toHaveLength(2);
    expect(entries.every((entry) => entry.phase === "authoritative")).toBe(true);

    // Seeding twice (e.g. reconnect) is idempotent.
    const again = seedFromServerTurns(seeded, [
      { turn_id: "t1", user_text: "第一个问题", assistant_text: "第一个回答" },
    ]);
    expect(again.entries).toHaveLength(4);
  });

  it("names assistant entries per sentence so ordering is stable", () => {
    expect(assistantSentenceId("t", 2)).toBe("assistant-t-s2");
    expect(assistantSentenceId("t", undefined)).toBe("assistant-t-live");
  });

  it("keeps the question and marks it retryable when a turn settles with no answer", () => {
    let state = emptyTranscriptState();
    state = reduceVoiceEvent(state, event({ type: "user.final", turn_id: TURN, payload: { text: "为什么天空是蓝的" } }));
    // Provider error / idle timeout: the turn settles as failed with no text.
    state = reduceVoiceEvent(
      state,
      event({
        type: "turn.finalized",
        turn_id: TURN,
        payload: { text: "", outcome: "failed", failed: true, retryable: true, failure_reason: "llm_error" },
      }),
    );
    const entries = orderedEntries(state);
    expect(entries.filter((entry) => entry.role === "assistant")).toHaveLength(0);
    const user = entries.filter((entry) => entry.role === "user");
    expect(user).toHaveLength(1);
    expect(user[0].text).toBe("为什么天空是蓝的");
    expect(user[0].deliveryStatus).toBe("failed");
    expect(user[0].failureReason).toBe("llm_error");
  });

  it("recovers a failed turn as retryable after a refresh", () => {
    const seeded = seedFromServerTurns(emptyTranscriptState(), [
      { turn_id: "t-failed", user_text: "没被回答的问题", assistant_text: "", status: "failed", failure_reason: "turn_idle_timeout" },
      { turn_id: "t-ok", user_text: "正常问题", assistant_text: "正常回答", status: "finalized" },
    ]);
    const entries = orderedEntries(seeded);
    const failed = entries.find((entry) => entry.turnId === "t-failed");
    expect(failed?.deliveryStatus).toBe("failed");
    expect(failed?.failureReason).toBe("turn_idle_timeout");
    // The successful turn is untouched and gains its answer.
    expect(entries.filter((entry) => entry.role === "assistant")).toHaveLength(1);
  });

  it("keeps the partial answer visible when a turn settles as failed with text", () => {
    let state = emptyTranscriptState();
    state = reduceVoiceEvent(state, event({ type: "user.final", turn_id: TURN, payload: { text: "问题" } }));
    state = reduceVoiceEvent(
      state,
      event({ type: "assistant.sentence.queued", turn_id: TURN, payload: { text: "说了半句", sentence_seq: 1 } }),
    );
    state = reduceVoiceEvent(
      state,
      event({ type: "turn.finalized", turn_id: TURN, payload: { text: "说了半句", outcome: "failed" } }),
    );
    const entries = orderedEntries(state);
    expect(entries.filter((entry) => entry.role === "user")).toHaveLength(1);
    expect(entries.filter((entry) => entry.role === "assistant")).toHaveLength(0);
  });

  it("does not project a provisional assistant draft at all", () => {
    let state = emptyTranscriptState();
    state = reduceVoiceEvent(
      state,
      event({ type: "assistant.llm.delta", phase: "speculative", turn_id: TURN, payload: { text: "草稿" } }),
    );
    expect(orderedEntries(state)).toHaveLength(0);
  });
});

describe("model pin truth", () => {
  it("never claims a switch that was not confirmed", () => {
    // A backend that reports the new id but no confirmation must not move the
    // model the UI presents as live.
    const pin = deriveModelPin(
      { model_id: "new-model", applies_to: "next_turn", applied_epoch: 7 },
      { session_epoch: 7 },
      "old-model",
    );
    expect(pin?.repointed).toBe(false);
    expect(pin?.certainty).toBe("next_connection");
    expect(pin?.effectiveModelId).toBe("old-model");
    expect(pin?.requestedModelId).toBe("new-model");
  });

  it("moves the live model only on an explicit re-point confirmation", () => {
    const pin = deriveModelPin(
      { model_id: "new-model", effective_model_id: "new-model", repointed: true, applied_epoch: 8 },
      { session_epoch: 8 },
      "old-model",
    );
    expect(pin?.repointed).toBe(true);
    expect(pin?.certainty).toBe("repointed");
    expect(pin?.effectiveModelId).toBe("new-model");
    expect(pin?.epoch).toBe(8);
  });

  it("treats a non-boolean repointed flag as unconfirmed", () => {
    const pin = deriveModelPin(
      { model_id: "new-model", repointed: "true" },
      {},
      "old-model",
    );
    expect(pin?.repointed).toBe(false);
    expect(pin?.effectiveModelId).toBe("old-model");
  });

  it("ignores the context snapshot that every call creation writes", () => {
    // 这条事件与模型无关；把它当声明处理，就会出现"本次通话仍在使用「X」，
    // 「新模型」将在下次接通后生效"——一次模型都没切过的通话也会挂着它。
    const pin = deriveModelPin(
      {
        reason: "context_snapshot",
        context_version: "ctx-3",
        applies_to: "next_turn",
        applied_epoch: 3,
      },
      { session_epoch: 3 },
      "deepseek-flash",
    );
    expect(pin).toBeNull();
  });

  it("ignores a context.updated that names no model at all", () => {
    // 老后端不会写 reason：没有模型名的事件同样不是模型声明。
    expect(deriveModelPin({ context_version: "ctx-3" }, {}, "deepseek-flash")).toBeNull();
  });

  it("accepts an explicit switch request whose target cannot be named", () => {
    const pin = deriveModelPin(
      { reason: "model_switch", model_id: null },
      {},
      "deepseek-flash",
    );
    expect(pin?.requestedModelId).toBeNull();
    expect(pin?.repointed).toBe(false);
    expect(pin?.effectiveModelId).toBe("deepseek-flash");
  });

  it("never lets the pipeline's own report invent a pin", () => {
    const pin = deriveModelPin(
      {
        origin: "pipeline",
        reason: "context_snapshot",
        repointed: true,
        effective_model_id: "deepseek-flash",
      },
      {},
      "deepseek-flash",
      null,
    );
    expect(pin).toBeNull();
  });

  it("upgrades a pending switch once the pipeline confirms it", () => {
    const pending = deriveModelPin(
      { reason: "model_switch", model_id: "new-model" },
      {},
      "old-model",
    );
    const confirmed = deriveModelPin(
      {
        origin: "pipeline",
        reason: "model_switch",
        model_id: "new-model",
        repointed: true,
        effective_model_id: "new-model",
      },
      {},
      "old-model",
      pending,
    );
    expect(confirmed?.repointed).toBe(true);
    expect(confirmed?.certainty).toBe("repointed");
    expect(confirmed?.requestedModelId).toBe("new-model");
    expect(confirmed?.effectiveModelId).toBe("new-model");
  });

  it("keeps the switch pending when the report says it did not take effect", () => {
    const pending = deriveModelPin(
      { reason: "model_switch", model_id: "new-model" },
      {},
      "old-model",
    );
    const reported = deriveModelPin(
      {
        origin: "pipeline",
        reason: "model_switch",
        model_id: "new-model",
        repointed: false,
        effective_model_id: "old-model",
      },
      {},
      "old-model",
      pending,
    );
    expect(reported?.repointed).toBe(false);
    expect(reported?.effectiveModelId).toBe("old-model");
    expect(reported?.requestedModelId).toBe("new-model");
  });
});

describe("degraded text fallback", () => {
  it("ignores a transient retryable error", () => {
    const stages = deriveDegradedStages([], { stage: "asr", message: "reset", retryable: true });
    expect(stages).toEqual([]);
    expect(requiresTextFallback(stages)).toBe(false);
    expect(degradedNotice(stages)).toBeNull();
  });

  it("records only stages that actually degraded, once each", () => {
    let stages = deriveDegradedStages([], { stage: "asr", degraded: true });
    stages = deriveDegradedStages(stages, { stage: "asr", degraded: true });
    stages = deriveDegradedStages(stages, { stage: "tts", degraded: true });
    expect(stages).toEqual(["asr", "tts"]);
  });

  it("forces text input only when ASR is gone", () => {
    expect(requiresTextFallback(["tts"])).toBe(false);
    expect(requiresTextFallback(["network"])).toBe(false);
    expect(requiresTextFallback(["tts", "asr"])).toBe(true);
  });

  it("explains what broke and that the record survives", () => {
    expect(degradedNotice(["asr"])).toContain("文字输入");
    expect(degradedNotice(["asr"])).toContain("保留");
    expect(degradedNotice(["tts"])).toContain("文字显示");
    expect(degradedNotice([])).toBeNull();
    // An unknown stage still yields a usable message rather than a blank banner.
    expect(degradedNotice(["network"])).toBeTruthy();
  });
});
