import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StreamUI } from "@/components/chat/stream-ui";
import { authStore } from "@/api/auth-store";

vi.mock("@/components/chat/message-part-renderer", () => ({TextWithCitations: ({content}: {content: string}) => <p>{content}</p>}));

const chart = {protocol: "suip/0.1", block_id: "growth", component: "chart", state: "completed", revision: 2,
  props: {title: "增长", interaction: "explore", columns: [{key: "v", label: "用户数"}], data_kind: "example"},
  rows: [{id: "may", label: "5月", values: {v: 1200}}]};

describe("document native UI", () => {
  it("binds embedded chart actions to the parent message and selected document block", async () => {
    const onAction = vi.fn();
    render(<StreamUI partId="parent-report" onAction={onAction} data={{...chart, revision: 7, component: "document", rows: [], blocks: [
      {id: "trend", type: "chart", visual: {props: chart.props, rows: chart.rows}},
    ]}} />);
    fireEvent.click(await screen.findByRole("button", {name: "柱状", exact: true}));
    fireEvent.click(screen.getByRole("button", {name: "让 AI 解释", exact: true}));
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({componentId: "parent-report", event: "document.block.explain",
      payload: expect.objectContaining({revision: 7, block_id: "trend", view_state: expect.objectContaining({mode: "bar"})})}));
  });
  it("does not replay uncommitted numbers from an older persisted preview", () => {
    const {rerender} = render(<StreamUI partId="numeric-preview" data={{...chart, state: "streaming", preview: {tool_call_id: "pending", sequence: 1, base: null}}} />);
    expect(screen.queryByRole("img", {name: "增长"})).toBeNull();
    expect(screen.getByText("等待数据填充…")).toBeInTheDocument();
    rerender(<StreamUI partId="numeric-preview" data={{...chart, rows: [{id: "may", label: "5月", values: {v: 999}}], state: "streaming", preview: {tool_call_id: "pending", sequence: 2, base: chart}}} />);
    expect(screen.getByRole("button", {name: "5月: 用户数 1,200"})).toBeInTheDocument();
    expect(screen.queryByRole("button", {name: "5月: 用户数 999"})).toBeNull();
  });
  it("renders an anchored table of contents and embedded metric provenance", async () => {
    render(<StreamUI partId="doc-toc" data={{...chart, component: "document", rows: [], props: {title: "报告", density: "editorial", toc: true}, blocks: [
      {id: "h1", type: "heading", level: 2, text: "观察"},
      {id: "m", type: "metric", visual: {props: {title: "关键数值", source_note: "用户提供", data_kind: "example"}, items: [{id: "active", label: "月活", value: 3180}]}},
      {id: "h2", type: "heading", level: 2, text: "建议"},
    ]}} />);
    const nav = await screen.findByRole("navigation", {name: "文档目录"});
    expect(nav).toHaveTextContent("2 个章节");
    fireEvent.click(screen.getByText("目录"));
    const anchor = screen.getByRole("link", {name: "观察"}).getAttribute("href")!;
    expect(document.getElementById(anchor.slice(1))).toHaveTextContent("观察");
    fireEvent.click(screen.getByRole("link", {name: "建议"}));
    expect(screen.getByRole("link", {name: "建议"})).toHaveAttribute("aria-current", "location");
    expect(screen.getByRole("link", {name: "观察"})).not.toHaveAttribute("aria-current");
    expect(screen.getByText("3,180")).toBeInTheDocument();
    expect(screen.getByText(/用户提供/)).toBeInTheDocument();
    expect(screen.getByRole("button", {name: "导出 Markdown"})).toBeEnabled();
  });

  it("restores validated preferences from disk and sends the current view with an explain action", async () => {
    localStorage.setItem("learngraph.genui.preferences.v1", JSON.stringify({"guest:local:disk-chart:chart": {mode: "bar", limit: 3, hidden: []}}));
    const onAction = vi.fn();
    render(<StreamUI partId="disk-chart" data={chart} onAction={onAction} />);
    expect(screen.getByRole("button", {name: "柱状"})).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", {name: "让 AI 解释"}));
    expect(onAction.mock.calls[0][0].payload.view_state).toEqual({mode: "bar", limit: 3, hidden: []});
    fireEvent.click(screen.getByRole("button", {name: "折线"}));
    const saved = JSON.parse(localStorage.getItem("learngraph.genui.preferences.v1")!);
    expect(saved["guest:local:disk-chart:chart"].mode).toBe("line");
  });

  it("ignores corrupt saved state and separates workspaces", () => {
    authStore.setWorkspaceId("other");
    localStorage.setItem("learngraph.genui.preferences.v1", JSON.stringify({"guest:local:isolated-chart:chart": {mode: "bar"}, "guest:other:isolated-chart:chart": {mode: "eval", limit: -100, hidden: "oops"}}));
    render(<StreamUI partId="isolated-chart" data={chart} />);
    expect(screen.getByRole("button", {name: "折线"})).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("img", {name: "增长"})).toBeInTheDocument();
  });
});
