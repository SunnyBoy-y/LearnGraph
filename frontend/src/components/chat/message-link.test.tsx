import "@/test/pdfjs-polyfills";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MessageResponse } from "@/components/ai-elements/message";
import { IncrementalMarkdown } from "@/components/ai-elements/incremental-markdown";
import { MessageLink } from "./message-link";
import { MarkdownOrderedList, MarkdownUnorderedList } from "./markdown-search-results";
import { MessagePartRenderer } from "./message-part-renderer";
import { renderWithProviders } from "@/test/render";
import type { MessagePart } from "@/types/sessions";

vi.mock("@/features/auth/auth-context-value", () => ({ useAuth: () => ({ workspaceId: "ws-test" }) }));

const components = { a: MessageLink, ol: MarkdownOrderedList, ul: MarkdownUnorderedList };
const resources = `1. **React 中文官方文档** https://zh-hans.react.dev/learn

   > 组件、Hooks、状态管理与界面渲染基础

2. [React API Reference](https://react.dev/reference/react) — React Hooks、组件及 API 参考

3. [shadcn/ui 官方文档](https://ui.shadcn.com/docs)

   可定制组件库、主题与 UI 设计`;

afterEach(() => vi.useRealTimers());

describe("message links", () => {
  it("previews on click, copies the full destination and closes with Escape", async () => {
    const copy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: copy } });
    render(<MessageLink href="https://example.com/docs#section">官方文档</MessageLink>);
    const link = screen.getByRole("link", { name: "官方文档" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link.querySelector(".message-link-arrow")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "预览链接：官方文档" }));
    const card = await screen.findByRole("dialog", { name: "链接预览" });
    expect(card.closest(".message-link-group")).toBeNull();
    expect(card.querySelector("img")).toHaveAttribute("src", "https://example.com/favicon.ico");
    fireEvent.click(screen.getByRole("button", { name: "复制链接" }));
    await screen.findByRole("button", { name: "已复制链接" });
    expect(copy).toHaveBeenCalledWith("https://example.com/docs#section");
    fireEvent.keyDown(card, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps hover previews interactive while moving to the card", () => {
    vi.useFakeTimers();
    render(<MessageLink href="https://example.com/docs">文档</MessageLink>);
    const group = screen.getByRole("link").parentElement!;
    fireEvent.pointerEnter(group, { pointerType: "mouse" });
    act(() => vi.advanceTimersByTime(139));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    const card = screen.getByRole("dialog");
    fireEvent.click(screen.getByRole("button", { name: "预览链接：文档" }));
    expect(card).toBeVisible();
    fireEvent.pointerLeave(group, { pointerType: "mouse" });
    act(() => vi.advanceTimersByTime(150));
    fireEvent.pointerEnter(card, { pointerType: "mouse" });
    act(() => vi.advanceTimersByTime(250));
    expect(card).toBeVisible();
    fireEvent.pointerLeave(card, { pointerType: "mouse" });
    act(() => vi.advanceTimersByTime(200));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("falls back when the site icon fails and leaves internal and unsafe URLs plain", async () => {
    const { rerender } = render(<MessageLink href="https://example.com/docs">文档</MessageLink>);
    fireEvent.click(screen.getByRole("button"));
    const card = await screen.findByRole("dialog");
    fireEvent.error(card.querySelector("img")!);
    expect(card.querySelector("img")).toBeNull();
    expect(card.querySelector(".message-link-site-icon svg")).toBeInTheDocument();
    rerender(<MessageLink href="/w/workspace/documents/file">本地文档</MessageLink>);
    expect(screen.getByRole("link")).not.toHaveAttribute("target");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    for (const href of ["javascript:alert(1)", "data:text/html,unsafe", "//evil.example/path", "streamdown:incomplete-link"]) {
      rerender(<MessageLink href={href}>不可跳转</MessageLink>);
      expect(screen.queryByRole("link")).not.toBeInTheDocument();
    }
  });

  it("renders existing Markdown resource lists as cards with titles and summaries", async () => {
    render(<MessageResponse components={components}>{resources}</MessageResponse>);
    const card = await screen.findByRole("region", { name: "相关参考资料" });
    expect(card.querySelectorAll("li")).toHaveLength(3);
    expect(screen.getByRole("link", { name: "React 中文官方文档" })).toHaveAttribute("href", "https://zh-hans.react.dev/learn");
    expect(screen.getByText("组件、Hooks、状态管理与界面渲染基础")).toBeVisible();
    expect(screen.getByText("React Hooks、组件及 API 参考")).toBeVisible();
    expect(screen.getByText("共 3 个链接")).toBeVisible();
    expect(screen.getAllByRole("button", { name: /^预览链接/ })).toHaveLength(3);
  });

  it("preserves procedural and nested lists, tasks, code, and ordinary linked prose", async () => {
    render(<MessageResponse components={components}>{`1. 打开 [文档](https://example.com/docs) 并完成配置
2. 运行测试
   - 保留嵌套项

- [ ] 检查 [官方文档](https://example.com)
- [x] 完成

正文中的 [React 文档](https://react.dev) 仍然是行内链接。

\`\`\`text
1. [代码中的链接](https://example.com)
2. 不生成卡片
\`\`\``}</MessageResponse>);
    await screen.findByRole("link", { name: "React 文档" });
    expect(screen.queryByRole("region", { name: "相关参考资料" })).not.toBeInTheDocument();
    expect(screen.getByText("保留嵌套项")).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    expect(screen.getByText(/代码中的链接/)).toBeInTheDocument();
  });

  it("uses the same cards and previews during incremental streaming", async () => {
    render(<IncrementalMarkdown text={`${resources}\n\n后续正文。\n\n更多正文。`} components={components} codeHighlight="plain" />);
    await screen.findByRole("region", { name: "相关参考资料" });
    fireEvent.click(screen.getByRole("button", { name: "预览链接：shadcn/ui 官方文档" }));
    expect(await screen.findByRole("dialog", { name: "链接预览" })).toHaveTextContent("https://ui.shadcn.com/docs");
  });

  it("connects the real assistant renderer without replacing citation pills", async () => {
    const part: MessagePart = {
      id: "answer", type: "text", status: "completed", content: `参考[ref_1]。\n\n${resources}`,
    };
    const sources: MessagePart = {
      id: "sources", type: "source_list", status: "completed",
      data: { results: [{ index: 1, title: "来源标题", url: "https://example.com/source", site_name: "引用来源" }] },
    };
    const { rerender } = renderWithProviders(<MessagePartRenderer part={part} siblingParts={[part, sources]} />);
    await screen.findByRole("region", { name: "相关参考资料" });
    expect(screen.getByRole("button", { name: "查看引用来源：引用来源" })).toBeInTheDocument();
    const streamingPart = { ...part, content: `${part.content}\n\n补充说明。` };
    rerender(<MessagePartRenderer part={streamingPart} siblingParts={[streamingPart, sources]} streaming />);
    await screen.findByRole("button", { name: "预览链接：React 中文官方文档" });
    expect(screen.getByRole("button", { name: "查看引用来源：引用来源" })).toBeInTheDocument();
  });
});
