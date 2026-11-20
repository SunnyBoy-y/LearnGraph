import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StreamUI } from "./stream-ui";

describe("graph canvas stream and replay", () => {
  afterEach(() => {
    Reflect.deleteProperty(document, "fullscreenElement");
    Reflect.deleteProperty(document, "exitFullscreen");
    vi.restoreAllMocks();
  });

  it("expands only the graph, preserves controls across stream updates, and tracks browser exits", async () => {
    const user = userEvent.setup();
    const data = {protocol:"suip/0.1",component:"graph",block_id:"fullscreen",revision:0,state:"streaming",props:{title:"架构图",description:"参考架构说明",interaction:"explore"},nodes:[],edges:[]};
    let current: Element | null = null;
    Object.defineProperty(document, "fullscreenElement", {configurable:true, get:() => current});
    const request = vi.fn(async function (this: HTMLElement) {
      current = this;
      document.dispatchEvent(new Event("fullscreenchange"));
    });
    const exit = vi.fn(async () => {
      current = null;
      document.dispatchEvent(new Event("fullscreenchange"));
    });
    Object.defineProperty(document, "exitFullscreen", {configurable:true, value:exit});
    try {
      const {container, rerender} = render(<StreamUI data={data} partId="fullscreen" />);
      Object.defineProperty(container.querySelector(".stream-ui__graph"), "requestFullscreen", {value:request});
      expect(screen.queryByText("参考架构说明")).not.toBeInTheDocument();
      fireEvent.change(screen.getByLabelText("图谱缩放"), {target:{value:"2"}});
      await user.click(screen.getByRole("button", {name:"全屏查看"}));
      expect(request).toHaveBeenCalledOnce();
      expect(current).toBe(container.querySelector(".stream-ui__graph"));
      expect(screen.getByRole("button", {name:"退出全屏"})).toHaveAttribute("aria-pressed", "true");
      fireEvent.change(screen.getByLabelText("画布底色"), {target:{value:"warm"}});
      rerender(<StreamUI data={{...data,revision:1,state:"completed",nodes:[{id:"root",label:"数据分析"}]}} partId="fullscreen" />);
      expect(screen.getByRole("button", {name:"数据分析"})).toBeInTheDocument();
      expect(current).toHaveAttribute("data-background", "warm");
      expect(screen.getByLabelText("图谱缩放")).toHaveValue("2");
      await user.click(screen.getByRole("button", {name:"退出全屏"}));
      expect(exit).toHaveBeenCalledOnce();
      expect(screen.getByRole("button", {name:"全屏查看"})).toHaveAttribute("aria-pressed", "false");
      await user.click(screen.getByRole("button", {name:"全屏查看"}));
      // Escape and browser chrome exit fullscreen through this same event.
      current = null;
      fireEvent(document, new Event("fullscreenchange"));
      expect(screen.getByRole("button", {name:"全屏查看"})).toHaveAttribute("aria-pressed", "false");
      expect(screen.getByLabelText("图谱缩放")).toHaveValue("2");
    } finally {
      Reflect.deleteProperty(document, "fullscreenElement");
    }
  });

  it("keeps the graph usable when the browser rejects fullscreen", async () => {
    const request = vi.fn().mockRejectedValue(new Error("Permission denied"));
    const {container} = render(<StreamUI data={{protocol:"suip/0.1",component:"graph",block_id:"fullscreen-denied",revision:0,state:"completed",props:{title:"架构图",interaction:"explore"},nodes:[],edges:[]}} partId="fullscreen-denied" />);
    Object.defineProperty(container.querySelector(".stream-ui__graph"), "requestFullscreen", {value:request});
    fireEvent.click(screen.getByRole("button", {name:"全屏查看"}));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("无法切换全屏"));
    expect(screen.getByRole("button", {name:"全屏查看"})).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByLabelText("图谱缩放")).toBeEnabled();
  });

  it("uses the generated background before nodes arrive and keeps user controls on stream updates", () => {
    const base = {protocol:"suip/0.1",component:"graph",block_id:"canvas-regression",revision:0,state:"streaming",props:{title:"关系图",background:"warm",interaction:"explore"},nodes:[],edges:[]};
    const {container, rerender, unmount} = render(<StreamUI data={base} partId="canvas-regression" />);
    expect(container.querySelector("[data-background]")).toHaveAttribute("data-background", "warm");
    expect(screen.getByText("正在生成节点…")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("画布底色"), {target:{value:"dark"}});
    fireEvent.change(screen.getByLabelText("图谱缩放"), {target:{value:"2"}});
    const next = {...base,revision:1,state:"completed",nodes:[{id:"a",label:"根节点"},{id:"b",label:"分支节点"}],edges:[{id:"ab",source:"a",target:"b"}]};
    rerender(<StreamUI data={next} partId="canvas-regression" />);
    expect(container.querySelector("[data-background]")).toHaveAttribute("data-background", "dark");
    expect(screen.getByLabelText("图谱缩放")).toHaveValue("2");
    expect(container.querySelector("[data-graph-camera]")?.getAttribute("transform")).toContain("scale(2)");
    fireEvent.click(screen.getByRole("button", {name:"自动布局 / 适应画布"}));
    expect(screen.getByLabelText("图谱缩放")).toHaveValue("1");
    fireEvent.change(screen.getByLabelText("画布布局"), {target:{value:"mindmap"}});
    expect(container.querySelector("[data-layout]")).toHaveAttribute("data-layout", "mindmap");
    expect(container.querySelector('[data-graph-node="a"]')).toHaveAttribute("data-root", "true");
    unmount();
    const replay = render(<StreamUI data={next} partId="canvas-regression" />);
    expect(replay.container.querySelector("[data-background]")).toHaveAttribute("data-background", "dark");
    expect(screen.getByRole("button", {name:"根节点"})).toBeInTheDocument();
    expect(screen.getByLabelText("画布布局")).toHaveValue("mindmap");
  });
});
