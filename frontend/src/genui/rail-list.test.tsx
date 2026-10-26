import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StreamUI } from "@/components/chat/stream-ui";
import { parseUISnapshot } from "@/lib/stream-ui";
import { documentMarkdown } from "./document-export";
import { editorialListBlocks } from "@/test/fixtures/editorial-lists";

vi.mock("@/components/chat/message-part-renderer", () => ({TextWithCitations: ({content}: {content: string}) => <p>{content}</p>}));
const data = (blocks: unknown[] = editorialListBlocks) => ({protocol: "suip/0.1", block_id: "editorial", component: "document", revision: 1, state: "streaming", props: {title: "编辑式回答"}, blocks});

describe("editorial rail lists in native messages", () => {
  it("renders three semantic list variants with static icons and accessible statuses", async () => {
    const {container} = render(<StreamUI partId="rail-all" data={data()} />);
    expect(await screen.findByRole("heading", {name: "极速模式：单次 LLM 调用"})).toBeInTheDocument();
    expect(screen.getByText("适合普通问答、技术解释、简单比较。")).toBeInTheDocument();
    expect(container.querySelector('[data-variant="feature_list"] svg')).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector('[data-variant="step_list"] ol')).toBeInTheDocument();
    expect(screen.getByRole("img", {name: "已完成"})).toBeInTheDocument();
    expect(screen.getByRole("img", {name: "进行中"})).toBeInTheDocument();
    expect(container.querySelectorAll(".genui-rail-list")).toHaveLength(4);
  });

  it("keeps completed rows mounted while the next description grows and later rows arrive", async () => {
    const first = editorialListBlocks.slice(0, 2);
    const {container, rerender} = render(<StreamUI partId="rail-stream" data={data(first)} />);
    await screen.findByRole("heading", {name: "极速模式：单次 LLM 调用"});
    const firstRow = container.querySelector('[data-item-id="fast"]');
    const partial = {...editorialListBlocks[2], text: "第二条正在生成", state: "streaming"};
    rerender(<StreamUI partId="rail-stream" data={data([...first, partial])} />);
    expect(await screen.findByText("第二条正在生成")).toBeInTheDocument();
    expect(container.querySelector('[data-item-id="fast"]')).toBe(firstRow);
    const secondRow = container.querySelector('[data-item-id="agent"]');
    rerender(<StreamUI partId="rail-stream" data={{...data(editorialListBlocks), revision: 2, state: "completed"}} />);
    expect(await screen.findByText("适合复杂研究、实时数据、长文和规划。")).toBeInTheDocument();
    expect(container.querySelector('[data-item-id="agent"]')).toBe(secondRow);
  });

  it("keeps interrupted text readable and stops describing it as still generating", async () => {
    const blocks = [editorialListBlocks[0], {...editorialListBlocks[1], state: "streaming"}];
    const {container} = render(<StreamUI partId="rail-stop" failed settled data={data(blocks)} />);
    expect(await screen.findByText(/生成已中断，保留已接收内容/)).toBeInTheDocument();
    expect(container.querySelector('[data-item-id="fast"]')).toHaveAttribute("aria-busy", "false");
    expect(screen.getByText("适合普通问答、技术解释、简单比较。")).toBeInTheDocument();
  });

  it("exports the same explanation, detail, step order and execution state as readable Markdown", () => {
    const snapshot = parseUISnapshot(data())!;
    expect(snapshot).not.toBeNull();
    const text = documentMarkdown(snapshot);
    expect(text).toContain("- **极速模式：单次 LLM 调用**");
    expect(text).toContain("1. **建立组件注册表**");
    expect(text).toContain("3. **保留交互与阅读状态**");
    expect(text).toContain("- [已完成] **检索已完成**");
    expect(text).toContain("模型直接流式输出文字");
    expect(text).not.toContain("brain-circuit");
  });

  it.each([
    [{id: "list", type: "feature_list"}, {id: "x", parent_id: "list", type: "rail_item", label: "标题", icon: "https://evil.example/icon.svg"}],
    [{id: "list", type: "status_list"}, {id: "x", parent_id: "list", type: "rail_item", label: "标题", item_status: "execute"}],
    [{id: "x", type: "rail_item", label: "无父列表"}],
    [{id: "list", type: "feature_list"}, {id: "x", parent_id: "list", type: "paragraph", text: "非法子类型"}],
    [{id: "list", type: "feature_list"}, {id: "x", parent_id: "list", type: "rail_item", label: "标题", style: {color: "red"}}],
    [{id: "list", type: "feature_list"}, ...Array.from({length: 13}, (_, i) => ({id: `i${i}`, parent_id: "list", type: "rail_item", label: "项目"}))],
  ])("rejects unsupported icons, states, nesting, styling and item counts", blocks => {
    expect(parseUISnapshot(data(blocks))).toBeNull();
  });
});
