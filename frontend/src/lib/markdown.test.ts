import { describe, expect, it } from "vitest";
import { mathMarkdown, normalizeLatexDelimiters } from "./markdown";

describe("typed math", () => {
  it.each([String.raw`x=\frac{d}{v}`, String.raw`$$x=\frac{d}{v}$$`, String.raw`\[x=\frac{d}{v}\]`, String.raw`$x=\frac{d}{v}$`, String.raw`$$\[x=\frac{d}{v}\]$$`])("wraps %s exactly once", text => {
    expect(mathMarkdown(text)).toBe("$$\n" + String.raw`x=\frac{d}{v}` + "\n$$");
  });
  it("preserves code and existing prose math normalization", () => {
    expect(normalizeLatexDelimiters("```js\nconst x = '\\(x\\)'\n```\n\\(x\\)")).toBe("```js\nconst x = '\\(x\\)'\n```\n$x$");
    expect(mathMarkdown("$x$", true)).toBe("$x$");
  });
});
