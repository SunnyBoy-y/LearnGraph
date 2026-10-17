import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StreamUI } from "./stream-ui";
import { parseUISnapshot, visibleStreamUIText } from "@/lib/stream-ui";
import { groupPartsForDisplay } from "@/features/chat/chat-message-parts";

function snapshot(component = "chart") {
  return {
    protocol: "suip/0.1",
    block_id: "demo",
    component,
    revision: 0,
    state: "streaming",
    props: {
      title: "项目活跃度",
      columns: [
        { key: "commits", label: "提交量" },
        { key: "issues", label: "Issue" },
      ],
      source_note: "虚构示例数据",
    },
    rows: [],
    nodes: [],
    edges: [],
    items: [],
  };
}

describe("stream UI", () => {
  it("retains chart choices when final-answer placement remounts the block", async () => {
    const user = userEvent.setup();
    function Answer({ settled }: { settled: boolean }) {
      const block = (
        <StreamUI
          partId="settlement-regression"
          data={{
            ...snapshot(),
            revision: settled ? 3 : 1,
            state: settled ? "completed" : "streaming",
          }}
        />
      );
      return settled ? (
        <section key="final-answer">{block}</section>
      ) : (
        <aside key="live-answer">{block}</aside>
      );
    }
    const { rerender } = render(<Answer settled={false} />);
    await user.click(screen.getByRole("button", { name: "柱状" }));
    await user.click(
      screen.getByRole("button", { name: "Issue", exact: true }),
    );
    rerender(<Answer settled />);
    expect(screen.getByRole("button", { name: "柱状" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByRole("button", { name: "Issue", exact: true }),
    ).toHaveAttribute("aria-pressed", "false");
  });
  it("hides protocol placement markers while their tokens are arriving", () => {
    for (const suffix of [
      "[[",
      "[[art",
      "[[artifact:blo",
      "[[artifact:block_id:demo",
      "[[artifact:block_id:demo|layout=block]]",
    ])
      expect(visibleStreamUIText(`分析：${suffix}`, true)).toBe("分析：");
    expect(
      visibleStreamUIText("前文[[artifact:block_id:demo]]后文", false),
    ).toBe("前文后文");
    expect(visibleStreamUIText("普通文字 [[参考资料]]", true)).toBe(
      "普通文字 [[参考资料]]",
    );
  });
  it("renders the skeleton before data and preserves local controls across snapshots", async () => {
    const user = userEvent.setup();
    const first = snapshot();
    const { rerender } = render(<StreamUI data={first} partId="part1" />);
    expect(screen.getByText("等待数据填充…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "柱状" }));
    await user.click(
      screen.getByRole("button", { name: "Issue", exact: true }),
    );
    rerender(
      <StreamUI
        data={{
          ...first,
          revision: 1,
          rows: [
            { id: "jan", label: "1月", values: { commits: 38, issues: 12 } },
          ],
        }}
        partId="part1"
      />,
    );
    expect(screen.getByRole("button", { name: "柱状" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(
      screen.getByRole("button", { name: "Issue", exact: true }),
    ).toHaveAttribute("aria-pressed", "false");
    fireEvent.focus(screen.getByRole("button", { name: "1月: 提交量 38" }));
    expect(screen.getByText("1月 · 提交量 38")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "让 AI 解释" })).toBeDisabled();
  });

  it("selects graph nodes locally and emits a revision-bound explain action only on request", async () => {
    const user = userEvent.setup(),
      onAction = vi.fn();
    const data = {
      ...snapshot("graph"),
      state: "completed",
      revision: 3,
      nodes: [{ id: "ming", label: "小明", description: "项目负责人" }],
    };
    render(<StreamUI data={data} partId="graph-part" onAction={onAction} />);
    fireEvent.keyDown(screen.getByRole("button", { name: "小明" }), {
      key: "Enter",
    });
    expect(screen.getByText("项目负责人")).toBeInTheDocument();
    expect(onAction).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "让 AI 解释此节点" }));
    expect(onAction).toHaveBeenCalledWith({
      componentId: "graph-part",
      componentType: "stream_ui",
      event: "graph.node.explain",
      payload: { revision: 3, node_id: "ming" },
    });
  });

  it("filters tables and sorts numbers numerically", async () => {
    const user = userEvent.setup();
    render(
      <StreamUI
        partId="t"
        data={{
          ...snapshot("table"),
          state: "completed",
          rows: [
            { id: "a", label: "Alpha", values: { commits: 20 } },
            { id: "b", label: "Beta", values: { commits: 3 } },
          ],
        }}
      />,
    );
    await user.click(screen.getByRole("button", { name: /提交量/ }));
    expect(screen.getAllByRole("row")[1]).toHaveTextContent("Beta");
    await user.type(screen.getByRole("textbox", { name: "筛选表格" }), "Alpha");
    expect(screen.queryByText("Beta")).not.toBeInTheDocument();
  });

  it("renders timeline details, metrics, removed blocks, and invalid payloads safely", () => {
    const { rerender } = render(
      <StreamUI
        partId="p"
        data={{
          ...snapshot("timeline"),
          items: [
            { id: "i", label: "发布", date: "10月", description: "公开演示" },
          ],
        }}
      />,
    );
    fireEvent.click(screen.getByText("发布"));
    expect(screen.getByText("公开演示")).toBeInTheDocument();
    rerender(
      <StreamUI
        partId="p"
        data={{
          ...snapshot("metric"),
          items: [{ id: "i", label: "学习天数", value: 42 }],
        }}
      />,
    );
    expect(screen.getByText("42")).toBeInTheDocument();
    rerender(
      <StreamUI partId="p" data={{ ...snapshot(), state: "removed" }} />,
    );
    expect(screen.queryByLabelText("项目活跃度")).toBeNull();
    rerender(
      <StreamUI
        partId="p"
        data={{
          ...snapshot(),
          component: "html",
          html: "<script>alert(1)</script>",
        }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("数据无效");
    expect(document.querySelector("script")).toBeNull();
  });

  it("reports failed action requests and keeps controls available for retry", async () => {
    const user = userEvent.setup();
    const onAction = vi.fn().mockRejectedValue(new Error("版本过期，请刷新"));
    render(
      <StreamUI
        partId="p"
        data={{ ...snapshot(), state: "completed" }}
        onAction={onAction}
      />,
    );
    await user.click(screen.getByRole("button", { name: "让 AI 解释" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("版本过期");
    expect(screen.getByRole("button", { name: "让 AI 解释" })).toBeEnabled();
  });

  it("rejects duplicate ids, dangling edges, nonfinite chart values, and prototype keys", () => {
    expect(
      parseUISnapshot({ ...snapshot(), rows: [{ id: "a" }, { id: "a" }] }),
    ).toBeNull();
    expect(
      parseUISnapshot({
        ...snapshot("graph"),
        edges: [{ id: "e", source: "x", target: "y" }],
      }),
    ).toBeNull();
    expect(
      parseUISnapshot({
        ...snapshot(),
        rows: [{ id: "a", values: { commits: Infinity } }],
      }),
    ).toBeNull();
    expect(
      parseUISnapshot({
        ...snapshot(),
        props: { title: "X", onClick: "alert(1)" },
      }),
    ).toBeNull();
    expect(
      parseUISnapshot({
        ...snapshot("graph"),
        nodes: [{ id: "__proto__", label: "X" }],
      }),
    ).toBeNull();
  });

  it("keeps narration and UI in the answer flow, outside the thinking chain", () => {
    const parts = groupPartsForDisplay(
      [
        {
          id: "text",
          type: "text",
          status: "completed",
          sequence: 1,
          content: "先看关系图",
          data: { kind: "plan_narration" },
        },
        { id: "tool", type: "tool_call", status: "completed", sequence: 2 },
        {
          id: "ui",
          type: "ui",
          status: "streaming",
          sequence: 3,
          data: snapshot("graph"),
        },
      ],
      { live: true },
    );
    expect(
      parts
        .find((group) => group.kind === "parts")
        ?.parts.map((part) => part.id),
    ).toEqual(["text", "ui"]);
  });
});
