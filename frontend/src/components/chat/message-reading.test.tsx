import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MessageReading } from "./message-reading";

function Answer({ count, length = 0 }: { count: number; length?: number }) {
  return <MessageReading process={<div className="message-text-block"><h2>思考阶段</h2></div>}>
    <div className="message-answer-segment answer-flow">
      <div className="message-text-block"><h2>第一节</h2><p>{"正文".repeat(length)}</p></div>
      {count > 1 && <h2 data-outline-heading>第二节</h2>}
      {count > 2 && <div className="message-text-block"><h3>子章节</h3></div>}
      <div hidden><h2 data-outline-heading>未显示的标签页</h2></div>
      <h3>普通卡片标题</h3>
    </div>
  </MessageReading>;
}

describe("message outline", () => {
  it("adds an initially collapsed outline as rendered sections arrive without remounting the answer", async () => {
    const { rerender } = render(<Answer count={1} />);
    const first = screen.getByRole("heading", { name: "第一节" });
    expect(screen.queryByRole("navigation", { name: "回复目录" })).not.toBeInTheDocument();
    rerender(<Answer count={3} />);
    const trigger = await screen.findByRole("button", { name: "目录" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("heading", { name: "第一节" })).toBe(first);
    fireEvent.click(trigger);
    const nav = within(screen.getByRole("navigation", { name: "回复目录" }));
    expect(nav.getAllByRole("listitem").map(item => item.textContent)).toEqual(["第一节", "第二节", "子章节"]);
    expect(nav.getByRole("button", { name: "子章节" }).parentElement?.style.getPropertyValue("--outline-depth")).toBe("1");
  });

  it("skips a short outline but shows a substantial two-section reply", async () => {
    const { rerender } = render(<Answer count={2} length={10} />);
    expect(screen.queryByRole("button", { name: "目录" })).not.toBeInTheDocument();
    rerender(<Answer count={2} length={300} />);
    expect(await screen.findByRole("button", { name: "目录" })).toBeInTheDocument();
  });

  it("jumps within the owning conversation, dismisses on selection or outside click, and restores focus on Escape", async () => {
    const { container } = render(<div data-conversation-scroll><Answer count={3} /><button>目录之外</button></div>);
    const scrollTo = vi.fn();
    Object.defineProperty(container.firstElementChild, "scrollTo", { value: scrollTo });
    const trigger = await screen.findByRole("button", { name: "目录" });
    fireEvent.click(trigger);
    const target = screen.getByRole("heading", { name: "第二节" });
    const entry = screen.getByRole("button", { name: "第二节" });
    entry.focus();
    fireEvent.keyDown(entry, { key: "Escape" });
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(trigger);
    fireEvent.pointerDown(screen.getByRole("button", { name: "目录之外" }));
    await waitFor(() => expect(trigger).toHaveAttribute("aria-expanded", "false"));
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: "第二节" }));
    expect(scrollTo).toHaveBeenCalledOnce();
    expect(target).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });
});
