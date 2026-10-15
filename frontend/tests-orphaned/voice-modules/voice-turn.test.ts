import { describe, expect, it } from "vitest";

import {
  beginTurnFor,
  emptyTurnProgress,
  revealPrecisionFor,
  turnMayComplete,
  turnTimeoutExceeded,
  type VoiceTurnProgress,
} from "./voice-turn";

function progress(patch: Partial<VoiceTurnProgress> = {}): VoiceTurnProgress {
  return { ...emptyTurnProgress(), ...patch };
}

describe("voice turn completion", () => {
  it("starts with nothing observed", () => {
    expect(emptyTurnProgress()).toEqual({
      llmClosed: false,
      ttsEnded: false,
      outputDrained: false,
      playbackEnded: false,
      spokenChars: 0,
    });
  });

  it("hands out a fresh progress object every time", () => {
    const first = emptyTurnProgress();
    first.outputDrained = true;
    expect(emptyTurnProgress().outputDrained).toBe(false);
  });

  it("rejects a turn that only went quiet: silence is never a completion signal", () => {
    // The F03 defect shape: the audio went silent between two sentences and the
    // old code finalized the turn on that silence.
    expect(turnMayComplete(progress())).toBe(false);
    expect(turnMayComplete(progress({ spokenChars: 0 }))).toBe(false);
  });

  it("rejects a turn where the LLM stopped but the queue is still draining", () => {
    expect(turnMayComplete(progress({ llmClosed: true }))).toBe(false);
  });

  it("rejects a turn where the queue drained but the LLM is still generating", () => {
    expect(turnMayComplete(progress({ outputDrained: true }))).toBe(false);
  });

  it("rejects a turn whose TTS finished while audio is still playing out", () => {
    expect(turnMayComplete(progress({ llmClosed: true, ttsEnded: true }))).toBe(false);
  });

  it("rejects a turn whose audio played out while the LLM is still generating", () => {
    expect(turnMayComplete(progress({ playbackEnded: true, outputDrained: true }))).toBe(false);
  });

  it("completes a turn once the LLM closed and the output queue drained", () => {
    expect(turnMayComplete(progress({ llmClosed: true, outputDrained: true }))).toBe(true);
  });

  it("never lets spoken character count decide completion", () => {
    expect(turnMayComplete(progress({ spokenChars: 999 }))).toBe(false);
    expect(turnMayComplete(progress({ llmClosed: true, outputDrained: true, spokenChars: 0 }))).toBe(true);
  });

  it("does not mutate the progress it judges", () => {
    const state = progress({ ttsEnded: true, spokenChars: 12 });
    const snapshot = { ...state };
    turnMayComplete(state);
    turnTimeoutExceeded(state, 60_000);
    expect(state).toEqual(snapshot);
  });
});

describe("voice turn timeout backstop", () => {
  it("does not start the clock before the LLM closed", () => {
    expect(turnTimeoutExceeded(progress(), 60_000)).toBe(false);
    expect(turnTimeoutExceeded(progress({ outputDrained: true }), 60_000)).toBe(false);
  });

  it("does not fire before the budget is spent", () => {
    expect(turnTimeoutExceeded(progress({ llmClosed: true }), 7_999)).toBe(false);
  });

  it("fires exactly at the default 8s budget", () => {
    expect(turnTimeoutExceeded(progress({ llmClosed: true }), 8_000)).toBe(true);
  });

  it("honours an explicit budget", () => {
    const state = progress({ llmClosed: true, ttsEnded: true });
    expect(turnTimeoutExceeded(state, 999, 1_000)).toBe(false);
    expect(turnTimeoutExceeded(state, 1_000, 1_000)).toBe(true);
  });

  it("never fires for a turn that can already complete", () => {
    const state = progress({ llmClosed: true, outputDrained: true });
    expect(turnTimeoutExceeded(state, 60_000)).toBe(false);
    expect(turnTimeoutExceeded(state, 60_000, 1)).toBe(false);
  });

  it("stays quiet when the elapsed time is not a usable number", () => {
    // A missing clock must not cut a turn short; waiting is the safe direction.
    expect(turnTimeoutExceeded(progress({ llmClosed: true }), Number.NaN)).toBe(false);
    expect(turnTimeoutExceeded(progress({ llmClosed: true }), -1)).toBe(false);
  });
});

describe("voice turn bubble ownership", () => {
  it("opens the first bubble with a locally generated key", () => {
    expect(beginTurnFor(null, null, () => "local-1")).toEqual({ turnId: "local-1", beginsNewTurn: true });
    expect(beginTurnFor(null, "turn_a", () => "local-1")).toEqual({ turnId: "local-1", beginsNewTurn: true });
  });

  it("keeps a sentence pause inside the same bubble", () => {
    // F03: the second marker of the same turn arrives after the audio went
    // quiet; it must land in the bubble that is already on screen.
    const previous = { turnId: "turn_a", progress: progress({ llmClosed: false }) };
    const keyCalls: number[] = [];
    const next = beginTurnFor(previous, "turn_a", () => {
      keyCalls.push(1);
      return "local-1";
    });
    expect(next).toEqual({ turnId: "turn_a", beginsNewTurn: false });
    expect(keyCalls).toHaveLength(0);
  });

  it("keeps a marker with no turn id inside the open bubble", () => {
    const previous = { turnId: "turn_a", progress: progress({ llmClosed: true, ttsEnded: true }) };
    expect(beginTurnFor(previous, null, () => "local-1")).toEqual({
      turnId: "turn_a",
      beginsNewTurn: false,
    });
  });

  it("treats a turn whose LLM closed but queue is still draining as still open", () => {
    const previous = { turnId: "turn_a", progress: progress({ llmClosed: true, ttsEnded: true }) };
    expect(beginTurnFor(previous, "turn_a", () => "local-1").beginsNewTurn).toBe(false);
  });

  it("opens a new bubble when the turn id changes", () => {
    const previous = { turnId: "turn_a", progress: progress({ llmClosed: false }) };
    expect(beginTurnFor(previous, "turn_b", () => "local-1")).toEqual({
      turnId: "turn_b",
      beginsNewTurn: true,
    });
  });

  it("carries the server turn id when the previous turn already settled", () => {
    // New bubble, but keyed by the authoritative turn id so the durable
    // finalize event for turn_b lands on this bubble and not on turn_a.
    const previous = { turnId: "turn_a", progress: progress({ llmClosed: true, outputDrained: true }) };
    expect(beginTurnFor(previous, "turn_b", () => "local-1")).toEqual({
      turnId: "turn_b",
      beginsNewTurn: true,
    });
  });

  it("generates a new key for a repeat marker of a settled turn", () => {
    // A late marker of a finished turn must not be able to reuse (and thus
    // reopen) the settled bubble's row id.
    const previous = { turnId: "turn_a", progress: progress({ llmClosed: true, outputDrained: true }) };
    const keyCalls: string[] = [];
    const next = beginTurnFor(previous, "turn_a", () => {
      keyCalls.push("turn_a");
      return "local-2";
    });
    expect(next).toEqual({ turnId: "local-2", beginsNewTurn: true });
    expect(keyCalls).toEqual(["turn_a"]);
  });

  it("generates a new key for an unattributed marker after a settled turn", () => {
    const previous = { turnId: "turn_a", progress: progress({ llmClosed: true, outputDrained: true }) };
    expect(beginTurnFor(previous, null, () => "local-2")).toEqual({
      turnId: "local-2",
      beginsNewTurn: true,
    });
  });

  it("never mutates the previous turn record", () => {
    const previous = { turnId: "turn_a", progress: progress({ llmClosed: true }) };
    const snapshot = { turnId: previous.turnId, progress: { ...previous.progress } };
    beginTurnFor(previous, "turn_b", () => "local-1");
    expect(previous).toEqual(snapshot);
  });

  it("reuses the previous turn id verbatim", () => {
    const previous = { turnId: "turn_1f0a:s2", progress: progress() };
    expect(beginTurnFor(previous, "turn_1f0a:s2", () => "local-1").turnId).toBe("turn_1f0a:s2");
  });
});

describe("voice reveal precision", () => {
  it("promises sentence precision when end offsets exist and playback was observed", () => {
    expect(revealPrecisionFor({ hasEndOffsets: true, hasPlaybackObservation: true })).toBe("sentence");
  });

  it("still reports sentence precision without a playback observation", () => {
    expect(revealPrecisionFor({ hasEndOffsets: true, hasPlaybackObservation: false })).toBe("sentence");
  });

  it("labels the reveal as estimated when the end offsets are missing", () => {
    expect(revealPrecisionFor({ hasEndOffsets: false, hasPlaybackObservation: true })).toBe("estimated");
    expect(revealPrecisionFor({ hasEndOffsets: false, hasPlaybackObservation: false })).toBe("estimated");
  });

  it("never claims more than sentence precision", () => {
    const results = new Set(
      [true, false].flatMap((hasEndOffsets) =>
        [true, false].map((hasPlaybackObservation) =>
          revealPrecisionFor({ hasEndOffsets, hasPlaybackObservation }),
        ),
      ),
    );
    expect([...results].sort()).toEqual(["estimated", "sentence"]);
  });
});
