import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  SESSION_TITLE_FALLBACK_MAX_CHARS,
  fallbackSessionTitle,
  isQuestionFallbackTitle,
  clearDraftSessionId,
  draftSessionScope,
  getDraftSessionCreationKey,
  getOrCreateDraftSession,
  setDraftSessionId,
} from "./draft-session";

describe("question-derived session titles", () => {
  it("normalizes whitespace and keeps the fixed character limit", () => {
    const question = "  如何\n设计一个可扩展的学习图谱，并避免重复节点？还有背景  ";
    const title = fallbackSessionTitle(question);

    expect(title).toBe("如何 设计一个可扩展的学习图谱，并避免重复节点？");
    expect(title).toHaveLength(SESSION_TITLE_FALLBACK_MAX_CHARS);
    expect(isQuestionFallbackTitle(title, question)).toBe(true);
  });

  it("does not turn blank input into a title", () => {
    expect(fallbackSessionTitle(" \n\t ")).toBe("");
    expect(isQuestionFallbackTitle("新会话", "")).toBe(false);
  });

  it("does not split an emoji at the fixed-width boundary", () => {
    const title = fallbackSessionTitle("😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀");
    expect([...title]).toHaveLength(SESSION_TITLE_FALLBACK_MAX_CHARS);
    expect(title.endsWith("😀")).toBe(true);
  });
});

describe("first-interaction session creation", () => {
  beforeEach(() => clearDraftSessionId());

  it("shares concurrent creation requests for the same folder", async () => {
    let resolve!: (session: { id: string }) => void;
    const factory = vi.fn(() => new Promise<{ id: string }>((done) => { resolve = done; }));
    const scope = draftSessionScope("workspace", "folder");
    const first = getOrCreateDraftSession(scope, factory);
    const repeated = getOrCreateDraftSession(scope, factory);
    expect(factory).toHaveBeenCalledTimes(1);
    expect(repeated).toBe(first);
    resolve({ id: "session" });
    await expect(repeated).resolves.toEqual({ id: "session" });
  });

  it("keeps folder identities separate and rotates the creation key after sending", () => {
    const scope = draftSessionScope("workspace", "folder");
    const original = getDraftSessionCreationKey(scope);
    expect(getDraftSessionCreationKey(scope)).toBe(original);
    expect(getDraftSessionCreationKey(draftSessionScope("workspace", "other"))).not.toBe(original);
    setDraftSessionId("session", "folder");
    clearDraftSessionId("session");
    expect(getDraftSessionCreationKey(scope)).not.toBe(original);
  });

  it("allows retry after a failed creation without changing its idempotency key", async () => {
    const scope = draftSessionScope("workspace", "folder");
    const key = getDraftSessionCreationKey(scope);
    await expect(getOrCreateDraftSession(scope, () => Promise.reject(new Error("offline")))).rejects.toThrow("offline");
    const retry = vi.fn(async () => ({ id: "session" }));
    await expect(getOrCreateDraftSession(scope, retry)).resolves.toEqual({ id: "session" });
    expect(retry).toHaveBeenCalledTimes(1);
    expect(getDraftSessionCreationKey(scope)).toBe(key);
  });
});
