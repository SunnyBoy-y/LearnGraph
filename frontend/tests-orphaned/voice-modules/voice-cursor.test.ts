import { describe, expect, it } from "vitest";

import {
  MAX_TRACKED_GAPS,
  confirmThrough,
  emptyCursor,
  fetchAfter,
  observeSeq,
  pendingGaps,
  type CursorState,
} from "./voice-cursor";

const cursor = (overrides: Partial<CursorState> = {}): CursorState => ({
  ...emptyCursor("sess-a", 3),
  ...overrides,
});

/** 按给定顺序观察一串序号，返回最终状态（不关心途中的 gap 标记）。 */
function observeAll(state: CursorState, seqs: readonly number[]): CursorState {
  return seqs.reduce((current, seq) => observeSeq(current, seq).state, state);
}

describe("voice cursor basics", () => {
  it("starts at zero so the first poll replays everything", () => {
    const state = emptyCursor("sess-a", 3);
    expect(state).toEqual({ confirmed: 0, observed: 0, sessionId: "sess-a", sessionEpoch: 3, gaps: [] });
    expect(fetchAfter(state)).toBe(0);
    expect(pendingGaps(state)).toEqual([]);
  });

  it("fetches from confirmed, never from observed", () => {
    const state = observeAll(emptyCursor("sess-a", 3), [1, 5]);
    expect(state.confirmed).toBe(1);
    expect(state.observed).toBe(5);
    expect(fetchAfter(state)).toBe(1);
  });

  it("fetches from confirmed so a skipped seq can still be replayed", () => {
    const { state, gap } = observeSeq(emptyCursor("sess-a", 3), 3);
    expect(gap).toBe(true);
    expect(fetchAfter(state)).toBe(0);
    expect(pendingGaps(state)).toEqual([3]);
  });
});

describe("voice cursor gap tracking", () => {
  it("closes the gap when the missing seq arrives", () => {
    const first = observeSeq(emptyCursor("sess-a", 3), 1).state;
    const { state, gap } = observeSeq(first, 3);
    expect(gap).toBe(true);
    expect(state.confirmed).toBe(1);
    expect(pendingGaps(state)).toEqual([3]);

    const filled = observeSeq(state, 2);
    expect(filled.gap).toBe(false);
    expect(filled.state.confirmed).toBe(3);
    expect(pendingGaps(filled.state)).toEqual([]);
  });

  it("absorbs a run of previously missing seqs once the hole is filled", () => {
    const state = observeAll(emptyCursor("sess-a", 3), [1, 3, 4, 5]);
    expect(state.confirmed).toBe(1);
    expect(pendingGaps(state)).toEqual([3, 4, 5]);

    const filled = observeSeq(state, 2).state;
    expect(filled.confirmed).toBe(5);
    expect(pendingGaps(filled)).toEqual([]);
  });

  it("treats a seq at or below confirmed as a duplicate, not a gap", () => {
    const state = observeAll(emptyCursor("sess-a", 3), [1, 2, 3]);
    const repeated = observeSeq(state, 2);
    expect(repeated.gap).toBe(false);
    expect(repeated.state).toBe(state);

    const older = observeSeq(state, 1);
    expect(older.gap).toBe(false);
    expect(older.state.confirmed).toBe(3);
    expect(pendingGaps(older.state)).toEqual([]);
  });

  it("keeps observed as the maximum seen, gaps included", () => {
    const state = observeAll(emptyCursor("sess-a", 3), [9, 4, 4, 12]);
    expect(state.observed).toBe(12);
    expect(pendingGaps(state)).toEqual([4, 9, 12]);
  });

  it("deduplicates repeated gap observations", () => {
    const state = observeAll(emptyCursor("sess-a", 3), [5, 5, 5]);
    expect(pendingGaps(state)).toEqual([5]);
  });

  it("reports a gap for the very first event when the stream does not start at 1", () => {
    const { state, gap } = observeSeq(emptyCursor("sess-a", 3), 7);
    expect(gap).toBe(true);
    expect(state.gaps).toEqual([7]);
  });

  it("caps the gap table at MAX_TRACKED_GAPS, keeping the nearest holes", () => {
    const seqs = Array.from({ length: MAX_TRACKED_GAPS + 5 }, (_item, index) => 100 + index * 2);
    const state = observeAll(emptyCursor("sess-a", 3), seqs);
    const gaps = pendingGaps(state);
    expect(gaps).toHaveLength(MAX_TRACKED_GAPS);
    expect(gaps[0]).toBe(100);
    expect(gaps[gaps.length - 1]).toBe(100 + (MAX_TRACKED_GAPS - 1) * 2);
  });
});

describe("voice cursor watermark", () => {
  it("moves confirmed up to the server watermark", () => {
    const state = confirmThrough(emptyCursor("sess-a", 3), 5);
    expect(state.confirmed).toBe(5);
    expect(state.observed).toBe(5);
    expect(fetchAfter(state)).toBe(5);
  });

  it("clears the gaps the watermark already covers", () => {
    const state = confirmThrough(cursor({ confirmed: 1, observed: 6, gaps: [3, 4, 6] }), 4);
    expect(state.confirmed).toBe(4);
    expect(pendingGaps(state)).toEqual([6]);
  });

  it("absorbs a contiguous gap that the watermark reaches", () => {
    const state = confirmThrough(cursor({ confirmed: 2, observed: 5, gaps: [4, 5] }), 3);
    expect(state.confirmed).toBe(5);
    expect(pendingGaps(state)).toEqual([]);
  });

  it("never moves confirmed backwards", () => {
    const state = cursor({ confirmed: 7, observed: 9, gaps: [9] });
    const lowered = confirmThrough(state, 3);
    expect(lowered.confirmed).toBe(7);
    expect(pendingGaps(lowered)).toEqual([9]);
    expect(confirmThrough(state, 7)).toBe(state);
  });

  it("ignores a watermark that makes no difference", () => {
    const state = cursor({ confirmed: 5, observed: 9, gaps: [] });
    expect(confirmThrough(state, 5)).toBe(state);
    expect(confirmThrough(state, 1)).toBe(state);
  });

  it("accepts live events that follow the watermark", () => {
    const confirmed = confirmThrough(emptyCursor("sess-a", 3), 4);
    const { state, gap } = observeSeq(confirmed, 5);
    expect(gap).toBe(false);
    expect(state.confirmed).toBe(5);
  });
});

describe("voice cursor session binding", () => {
  it("rebinds and resets when the remote session changes", () => {
    const state = cursor({ confirmed: 6, observed: 8, gaps: [8] });
    const { state: rebound, gap } = observeSeq(state, 2, "sess-b");
    expect(rebound).toEqual(emptyCursor("sess-b", 3));
    expect(gap).toBe(false);
    expect(fetchAfter(rebound)).toBe(0);
  });

  it("rebinds with the epoch that comes along with the new session", () => {
    const { state } = observeSeq(cursor({ confirmed: 6 }), 1, "sess-b", 4);
    expect(state).toEqual(emptyCursor("sess-b", 4));
  });

  it("resets when the epoch changes inside the same session", () => {
    const { state } = observeSeq(cursor({ confirmed: 6, observed: 6 }), 1, "sess-a", 4);
    expect(state).toEqual(emptyCursor("sess-a", 4));
  });

  it("does not reset when the caller passes the bound session and epoch", () => {
    const first = observeSeq(emptyCursor("sess-a", 3), 1).state;
    const { state, gap } = observeSeq(first, 2, "sess-a", 3);
    expect(gap).toBe(false);
    expect(state.confirmed).toBe(2);
  });

  it("keeps the current binding when the caller passes no session at all", () => {
    const { state } = observeSeq(cursor({ confirmed: 2 }), 3);
    expect(state.sessionId).toBe("sess-a");
    expect(state.sessionEpoch).toBe(3);
    expect(state.confirmed).toBe(3);
  });
});

describe("voice cursor input hygiene", () => {
  it("rejects a non-finite seq instead of poisoning the cursor", () => {
    const state = cursor({ confirmed: 2, observed: 2 });
    for (const bad of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
      const result = observeSeq(state, bad);
      expect(result.gap).toBe(false);
      expect(result.state).toBe(state);
    }
    expect(pendingGaps(state)).toEqual([]);
  });

  it("ignores a non-finite watermark", () => {
    const state = cursor({ confirmed: 2, observed: 2 });
    expect(confirmThrough(state, Number.NaN)).toBe(state);
    expect(confirmThrough(state, Number.POSITIVE_INFINITY)).toBe(state);
  });

  it("never mutates the state it is given", () => {
    const state = cursor({ confirmed: 1, observed: 1, gaps: [] });
    const snapshot = JSON.parse(JSON.stringify(state)) as CursorState;
    observeSeq(state, 5);
    observeSeq(state, 3);
    confirmThrough(state, 9);
    pendingGaps(state).push(999);
    expect(state).toEqual(snapshot);
  });

  it("hands out a copy of the gap list", () => {
    const state = observeAll(emptyCursor("sess-a", 3), [1, 4]);
    const gaps = pendingGaps(state);
    gaps.push(99);
    expect(pendingGaps(state)).toEqual([4]);
  });
});
