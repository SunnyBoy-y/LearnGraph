import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StreamUI } from "./stream-ui";

describe("graph canvas stream and replay", () => {
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
