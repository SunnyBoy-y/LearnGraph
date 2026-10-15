import { describe, expect, it } from "vitest";

import {
  emptyScope,
  noteScopeGeneration,
  noteScopeSession,
  noteScopeTurn,
  scopeOpensTurn,
  scopeVerdict,
  type ScopeState,
  type VoiceScope,
} from "./voice-scope";

const state = (overrides: Partial<ScopeState> = {}): ScopeState => ({
  ...emptyScope("sess-a", 3),
  ...overrides,
});

const incoming = (overrides: Partial<VoiceScope> = {}): VoiceScope => ({
  sessionId: "sess-a",
  sessionEpoch: 3,
  turnId: "turn-1",
  generationId: null,
  ...overrides,
});

describe("voice scope state", () => {
  it("starts empty and unattributed: nothing may be rendered yet", () => {
    const scope = emptyScope();
    expect(scope).toEqual({
      sessionId: "",
      sessionEpoch: 0,
      turnId: null,
      generationId: null,
      activeTurnId: null,
      legacy: false,
    });
    expect(scopeVerdict(scope, incoming({ sessionId: "", sessionEpoch: 0, turnId: null }))).toBe(
      "unattributed",
    );
  });

  it("takes the session binding from the arguments", () => {
    expect(emptyScope("sess-b", 7)).toEqual({
      sessionId: "sess-b",
      sessionEpoch: 7,
      turnId: null,
      generationId: null,
      activeTurnId: null,
      legacy: false,
    });
  });

  it("keeps the same reference while the session binding is unchanged", () => {
    const scope = state({ activeTurnId: "turn-1", turnId: "turn-1" });
    expect(noteScopeSession(scope, "sess-a", 3)).toBe(scope);
  });

  it("clears the turn when the session id changes", () => {
    const scope = state({ activeTurnId: "turn-1", turnId: "turn-1", generationId: 5 });
    expect(noteScopeSession(scope, "sess-b", 3)).toEqual({
      sessionId: "sess-b",
      sessionEpoch: 3,
      turnId: null,
      generationId: null,
      activeTurnId: null,
      legacy: false,
    });
  });

  it("clears activeTurnId when the epoch changes (reconnect / model switch)", () => {
    const scope = state({ activeTurnId: "turn-1", turnId: "turn-1", generationId: 5 });
    expect(noteScopeSession(scope, "sess-a", 4)).toEqual({
      sessionId: "sess-a",
      sessionEpoch: 4,
      turnId: null,
      generationId: null,
      activeTurnId: null,
      legacy: false,
    });
  });

  it("clears the generation on reset so a new session's generation 1 is not stale", () => {
    const scope = state({ generationId: 5 });
    const reset = noteScopeSession(scope, "sess-a", 4);
    expect(reset.generationId).toBeNull();
    expect(
      scopeVerdict(reset, incoming({ turnId: "turn-new", generationId: 1, sessionEpoch: 4 })),
    ).toBe("accept");
  });

  it("keeps the legacy fact across a reset", () => {
    const scope = state({ legacy: true });
    expect(noteScopeSession(scope, "sess-b", 9).legacy).toBe(true);
  });

  it("records and clears the active turn", () => {
    const opened = noteScopeTurn(state(), "turn-1");
    expect(opened.activeTurnId).toBe("turn-1");
    expect(opened.turnId).toBe("turn-1");
    expect(noteScopeTurn(opened, "turn-1")).toBe(opened);
    expect(noteScopeTurn(opened, null)).toMatchObject({ activeTurnId: null, turnId: null });
  });

  it("advances the generation and never lets a late event push it back", () => {
    const first = noteScopeGeneration(state(), 2);
    expect(first.generationId).toBe(2);
    expect(noteScopeGeneration(first, 2)).toBe(first);
    expect(noteScopeGeneration(first, 1).generationId).toBe(2);
    expect(noteScopeGeneration(first, 3).generationId).toBe(3);
  });

  it("leaves the generation alone when the event carries none", () => {
    const scope = noteScopeGeneration(state(), 4);
    expect(noteScopeGeneration(scope, null)).toBe(scope);
    expect(noteScopeGeneration(state(), null).generationId).toBeNull();
  });

  it("treats generation 0 as a real generation", () => {
    const scope = noteScopeGeneration(state(), 0);
    expect(scope.generationId).toBe(0);
    expect(scopeVerdict(scope, incoming({ generationId: 0 }))).toBe("accept");
    expect(scopeVerdict(scope, incoming({ generationId: null }))).toBe("accept");
  });
});

describe("voice scope verdict", () => {
  it("rejects an event from another session even when the turn id matches", () => {
    const scope = noteScopeTurn(state(), "turn-1");
    expect(scopeVerdict(scope, incoming({ sessionId: "sess-b" }))).toBe("stale");
  });

  it("only compares a non-empty session id", () => {
    const scope = noteScopeTurn(state(), "turn-1");
    expect(scopeVerdict(scope, incoming({ sessionId: "" }))).toBe("accept");
    expect(scopeVerdict(scope, incoming({ sessionId: "sess-a" }))).toBe("accept");
  });

  it("rejects an event from another session even when the session id is unset locally", () => {
    expect(scopeVerdict(emptyScope(), incoming({ sessionId: "sess-a", sessionEpoch: 0 }))).toBe(
      "stale",
    );
  });

  it("rejects an event from another epoch", () => {
    const scope = noteScopeTurn(state(), "turn-1");
    expect(scopeVerdict(scope, incoming({ sessionEpoch: 2 }))).toBe("stale");
    expect(scopeVerdict(scope, incoming({ sessionEpoch: 4 }))).toBe("stale");
  });

  it("rejects a late event from an ended generation", () => {
    const scope = noteScopeTurn(noteScopeGeneration(state(), 4), "turn-1");
    expect(scopeVerdict(scope, incoming({ generationId: 3 }))).toBe("stale");
    expect(scopeVerdict(scope, incoming({ generationId: 4 }))).toBe("accept");
    expect(scopeVerdict(scope, incoming({ generationId: 5, turnId: null }))).toBe("unattributed");
  });

  it("does not compare generations it cannot see", () => {
    const noLocal = noteScopeTurn(state(), "turn-1");
    expect(scopeVerdict(noLocal, incoming({ generationId: 1 }))).toBe("accept");
    const local = noteScopeTurn(noteScopeGeneration(state(), 4), "turn-1");
    expect(scopeVerdict(local, incoming({ generationId: null }))).toBe("accept");
  });

  it("calls a turn-less event unattributed unless the backend has no durable events", () => {
    expect(scopeVerdict(state(), incoming({ turnId: null }))).toBe("unattributed");
    expect(scopeVerdict(state({ legacy: true }), incoming({ turnId: null }))).toBe("accept");
  });

  it("accepts the first turn: no active turn yet means this event opens it", () => {
    expect(scopeVerdict(state(), incoming({ turnId: "turn-7" }))).toBe("accept");
  });

  it("accepts the active turn and rejects any other turn", () => {
    const scope = noteScopeTurn(state(), "turn-1");
    expect(scopeVerdict(scope, incoming({ turnId: "turn-1" }))).toBe("accept");
    expect(scopeVerdict(scope, incoming({ turnId: "turn-2" }))).toBe("stale");
  });

  it("keeps a stale event of the previous turn away from the current turn", () => {
    const scope = noteScopeTurn(state(), "turn-2");
    const late = incoming({ turnId: "turn-1", generationId: 4 });
    expect(scopeVerdict(scope, late)).toBe("stale");
    expect(scopeOpensTurn(scope, late)).toBe(false);
  });
});

describe("voice scope turn opening", () => {
  it("lets any event open a turn when none is active", () => {
    expect(scopeOpensTurn(state(), incoming({ turnId: "turn-1" }))).toBe(true);
    expect(scopeOpensTurn(state(), incoming({ turnId: null }))).toBe(true);
  });

  it("lets only the active turn keep writing to it", () => {
    const scope = noteScopeTurn(state(), "turn-1");
    expect(scopeOpensTurn(scope, incoming({ turnId: "turn-1" }))).toBe(true);
    expect(scopeOpensTurn(scope, incoming({ turnId: "turn-2" }))).toBe(false);
  });
});
