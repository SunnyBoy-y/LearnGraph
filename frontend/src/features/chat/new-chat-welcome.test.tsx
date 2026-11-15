import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NewChatSuggestions } from "./new-chat-welcome";

const prompts = [
  { id: "first", content: "解释循环数组如何从末尾绕回开头。" },
  { id: "second", content: "如何处理 HashMap 的哈希冲突？" },
  { id: "third", content: "什么时候将链表转换成红黑树？" },
];

// JSDOM has no viewport layout; browser QA checks the rendered geometry.
const css = readFileSync(resolve(import.meta.dirname, "new-chat-welcome.css"), "utf8");

function positioningRules() {
  const style = document.createElement("style");
  style.textContent = css;
  document.head.append(style);
  try {
    return Array.from(style.sheet!.cssRules);
  } finally {
    style.remove();
  }
}

describe("new chat suggestions", () => {
  it("keeps mobile suggestions in normal flow before the composer without changing desktop positioning", () => {
    const rules = positioningRules();
    const desktop = rules.find((rule) =>
      rule instanceof CSSStyleRule && rule.selectorText === ".new-chat-suggestions",
    ) as CSSStyleRule;
    const mobile = rules.find((rule) =>
      rule instanceof CSSMediaRule && rule.conditionText === "(max-width: 1024px)",
    ) as CSSMediaRule;
    const suggestions = Array.from(mobile.cssRules).find((rule) =>
      rule instanceof CSSStyleRule && rule.selectorText === ".is-new-chat-welcome .new-chat-suggestions",
    ) as CSSStyleRule;

    expect(desktop.style.position).toBe("absolute");
    expect(suggestions.style.position).toBe("static");
    expect(suggestions.style.order).toBe("-1");
    expect(suggestions.style.flex).toBe("0 0 auto");
  });

  it("keeps selection and refresh actions available", () => {
    const onRefresh = vi.fn();
    const onSelect = vi.fn();
    render(<NewChatSuggestions prompts={prompts} disabled={false} onRefresh={onRefresh} onSelect={onSelect} />);

    fireEvent.click(screen.getByRole("button", { name: prompts[0].content }));
    fireEvent.click(screen.getByRole("button", { name: "换一组推荐问题" }));

    expect(onSelect).toHaveBeenCalledExactlyOnceWith(prompts[0].content);
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it("reserves no space when no prompts are available", () => {
    const { container } = render(<NewChatSuggestions prompts={[]} disabled={false} pending onSelect={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
