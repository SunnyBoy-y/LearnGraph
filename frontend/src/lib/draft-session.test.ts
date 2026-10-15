import { describe, expect, it } from "vitest";

import {
  SESSION_TITLE_FALLBACK_MAX_CHARS,
  fallbackSessionTitle,
  isQuestionFallbackTitle,
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
