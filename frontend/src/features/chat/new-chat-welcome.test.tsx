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

  it("renders concise display_text while sending the full prompt content on click", () => {
    const onSelect = vi.fn();
    const separated = [
      {
        id: "sep-1",
        display_text: "HashMap 在什么情况下会从红黑树退化为链表？",
        content: "针对 HashMap 树化后节点减少的退化阈值，带我做一组删除操作的推演练习，理清树化与退化的边界条件。",
      },
      { id: "sep-2", content: "画图解释 HashMap 的哈希冲突" },
    ];
    render(<NewChatSuggestions prompts={separated} disabled={false} onSelect={onSelect} />);

    const first = screen.getByRole("button", { name: "HashMap 在什么情况下会从红黑树退化为链表？" });
    fireEvent.click(first);
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(separated[0].content);

    const second = screen.getByRole("button", { name: "画图解释 HashMap 的哈希冲突" });
    expect(second).toBeEnabled();
  });

  it("hides without unmounting or shifting layout when hidden is true", () => {
    const { container } = render(
      <NewChatSuggestions prompts={prompts} disabled={false} hidden onRefresh={vi.fn()} onSelect={vi.fn()} />,
    );
    const section = container.querySelector(".new-chat-suggestions");
    expect(section).toHaveClass("is-hidden");
    expect(section).toHaveAttribute("aria-hidden", "true");
    for (const button of container.querySelectorAll("button")) {
      expect(button).toHaveAttribute("tabindex", "-1");
    }
  });
});
