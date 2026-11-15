import { createRef, useState } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PromptInput, PromptInputTextarea } from "@/components/ai-elements/prompt-input";

function Harness({ initial = "", submit = vi.fn(), inputRef = createRef<HTMLTextAreaElement>() }) {
  const [text, setText] = useState(initial);
  return <PromptInput onSubmit={submit}>
    <PromptInputTextarea className="chat-composer__textarea" aria-label="输入消息" role="combobox"
      value={text} ref={inputRef} onChange={event => setText(event.currentTarget.value)} />
    <button type="submit">发送</button>
    <button type="button" onClick={() => setText("#### 恢复的草稿")}>恢复草稿</button>
  </PromptInput>;
}

function input() {
  return screen.getByRole("combobox", { name: "输入消息" }) as HTMLTextAreaElement;
}

describe("plain text composer without Markdown preview", () => {
  it("keeps Markdown syntax visible as plain text with no rich editor or preview", () => {
    const draft = "#### 标题\n\n**重点**\n- 条目\n`代码`";
    const view = render(<Harness initial={draft} />);
    expect(input().tagName).toBe("TEXTAREA");
    expect(input()).toBeVisible();
    expect(input()).toHaveValue(draft);
    expect(screen.getAllByRole("combobox", { name: "输入消息" })).toHaveLength(1);
    expect(screen.queryByRole("heading", { name: "标题" })).toBeNull();
    expect(view.container.querySelector('[contenteditable], [data-composer-rich-input], .chat-composer__markdown-preview')).toBeNull();
    expect(screen.queryByText("预览", { exact: true })).toBeNull();
  });

  it("preserves native focus and selection when entering Markdown", () => {
    const ref = createRef<HTMLTextAreaElement>();
    render(<Harness inputRef={ref} />);
    const original = input();
    act(() => original.focus());
    fireEvent.change(original, { target: { value: "#### 标题" } });
    act(() => ref.current?.setSelectionRange(5, 7));
    expect(input()).toBe(original);
    expect(ref.current).toBe(original);
    expect(input()).toHaveFocus();
    expect(input().selectionStart).toBe(5);
    expect(input().selectionEnd).toBe(7);
  });

  it("restores and submits raw Markdown without transforming it", async () => {
    const submit = vi.fn();
    render(<Harness submit={submit} />);
    fireEvent.click(screen.getByRole("button", { name: "恢复草稿" }));
    expect(input()).toHaveValue("#### 恢复的草稿");
    fireEvent.keyDown(input(), { key: "Enter", code: "Enter" });
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][0].text).toBe("#### 恢复的草稿");
  });
});
