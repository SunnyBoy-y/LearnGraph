import "@/test/pdfjs-polyfills";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MessagePartRenderer } from "./message-part-renderer";
import { renderWithProviders } from "@/test/render";
import type { MessagePart } from "@/types/sessions";

vi.mock("@/features/auth/auth-context-value", () => ({ useAuth: () => ({ workspaceId: "ws-test" }) }));
const part = (content: string): MessagePart => ({ id: "checklist", type: "text", status: "completed", content });

describe("Markdown task checkboxes", () => {
  it("keeps initial marks and lets users independently check and uncheck items", async () => {
    const action = vi.fn();
    renderWithProviders(<MessagePartRenderer part={part("- [x] 增长率排序\n- [ ] 折半填空")} onAction={action} />);
    const marked = await screen.findByRole("checkbox", { name: "增长率排序" });
    const empty = screen.getByRole("checkbox", { name: "折半填空" });
    expect(marked).toBeEnabled();
    expect(marked).toBeChecked();
    expect(empty).not.toBeChecked();
    fireEvent.click(marked);
    expect(marked).not.toBeChecked();
    fireEvent.click(empty);
    expect(empty).toBeChecked();
    expect(marked).not.toBeChecked();
    fireEvent.click(empty);
    expect(empty).not.toBeChecked();
    expect(action).not.toHaveBeenCalled();
  });

  it("supports Space and preserves user choices when more text streams in", async () => {
    const user = userEvent.setup();
    const content = "- [x] 完成阅读\n- [ ] 完成练习\n\n后续说明。";
    const { rerender } = renderWithProviders(<MessagePartRenderer part={part(content)} streaming />);
    const checkbox = await screen.findByRole("checkbox", { name: "完成阅读" });
    checkbox.focus();
    await user.keyboard(" ");
    expect(checkbox).not.toBeChecked();
    rerender(<MessagePartRenderer part={part(content + "\n\n更多正文。")} streaming />);
    expect(await screen.findByRole("checkbox", { name: "完成阅读" })).not.toBeChecked();
  });

  it("renders clickable checkboxes in long incremental Markdown and nested lists", async () => {
    const content = "复杂度说明。\n\n".repeat(1000) + "- [x] 外层任务\n  - [ ] 子任务\n- [ ] 第二项\n\n后续说明。\n\n末尾。";
    renderWithProviders(<MessagePartRenderer part={part(content)} streaming />);
    const child = await screen.findByRole("checkbox", { name: "子任务" });
    fireEvent.click(child);
    expect(child).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "第二项" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "外层任务" })).toBeChecked();
  });
});
