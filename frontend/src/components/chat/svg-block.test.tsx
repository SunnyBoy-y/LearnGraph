import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SVGBlock } from "./svg-block";
import { UnifiedArtifactCard } from "./unified-artifact-card";
import { groupPartsForDisplay } from "@/features/chat/chat-message-parts";
import { reconcileArtifacts, resolveArtifactMeta } from "@/features/chat/response-composer";
import type { MessagePart } from "@/types/sessions";

const viewBox = "0 0 320 180";
const group = { id: "group", tag: "g", attrs: {}, text: "" };
const box = { id: "box", parent_id: "group", tag: "rect", attrs: { width: "80", height: "40" }, text: "" };

it("keeps the canvas and existing DOM nodes while complete children arrive", () => {
  const { container, rerender } = render(<SVGBlock label="流程" viewBox={viewBox} nodes={[group, box]} />);
  const canvas = container.querySelector("svg");
  const rect = container.querySelector("rect");
  expect(rect?.parentElement?.getAttribute("data-svg-node")).toBe("group");
  const label = { id: "label", parent_id: "group", tag: "text", attrs: { y: "20" }, text: "开始 <下一步>" };
  rerender(<SVGBlock label="流程" viewBox={viewBox} nodes={[{ ...group }, { ...box }, label]} />);
  expect(container.querySelector("svg")).toBe(canvas);
  expect(container.querySelector("rect")).toBe(rect);
  expect(container.querySelector("text")?.textContent).toBe(label.text);
});

it("drops unsafe attributes, foreign elements, invalid parents and duplicate IDs", () => {
  const { container } = render(<SVGBlock label="流程" viewBox={viewBox} nodes={[
    group, box, { ...box, attrs: { width: "5" } },
    { ...box, id: "unsafe", attrs: { onload: "invalid" } },
    { ...box, id: "foreign", tag: "foreignObject" },
    { ...box, id: "orphan", parent_id: "missing" },
  ]} />);
  expect(container.querySelectorAll("[data-svg-node]")).toHaveLength(2);
  expect(container.querySelector("rect")).toHaveAttribute("width", "80");
});

const part = (id: string, type: MessagePart["type"], sequence: number, data: MessagePart["data"] = {}, content = ""): MessagePart =>
  ({ id, type, status: "completed", sequence, data, content });

it("preserves live and stored diagram placement between adjacent Markdown", () => {
  const diagram = part("diagram", "svg", 3, { block_id: "s123_flow", layout: "block", state: "complete", view_box: viewBox });
  const text = part("answer", "text", 0, { kind: "final_answer" }, "图前\n[[artifact:block_id:s123_flow]]\n图后");
  const live = groupPartsForDisplay([text, diagram], { live: true }).flatMap(s => s.parts);
  expect(live.map(p => p.type)).toEqual(["text", "svg", "text"]);
  const past = groupPartsForDisplay([{ ...text, content: "图前" }, { ...diagram, sequence: 1 }, part("after", "text", 2, { kind: "final_answer" }, "图后")]).flatMap(s => s.parts);
  expect(past.map(p => p.type)).toEqual(["text", "svg", "text"]);
});

describe("historical SVG delivery", () => {
  // Reduced fixture from the two representations of outputs/svg-demo.svg.
  const markup = (width: number, file: boolean) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180"${file ? ' width="320" height="180"' : ""}><rect width="${width}" height="40"/><text${file ? ' font-family="sans-serif"' : ""}>开始</text></svg>`;
  const sandbox = part("file", "sandbox_artifact", 4, { title: "流程.svg", path: "outputs/svg-demo.svg", file_id: "file-1", mime_type: "image/svg+xml", size_bytes: 123 });
  const writer = part("write", "tool_call", 1, { tool_name: "sandbox_write_file", input: { path: "outputs/svg-demo.svg", content: markup(80, true) } });
  const card = (w: number) => part("card", "magic_card", 5, { title: "流程图", preview_html: `<main>${markup(w, false)}</main>` });
  it("merges duplicate visual sources into the file-backed card only once", () => {
    const { parts } = reconcileArtifacts([writer, sandbox, card(80)]);
    expect(parts.map(p => p.id)).toEqual(["write", "file"]);
    expect(parts[1].data).toMatchObject({ file_id: "file-1", size_bytes: 123, svg_markup: expect.stringContaining("<rect") });
  });
  it("does not merge different geometry or an unknown file preview", () => {
    expect(reconcileArtifacts([writer, sandbox, card(81)]).parts).toHaveLength(3);
    expect(reconcileArtifacts([sandbox, card(80)]).parts).toHaveLength(2);
  });
});

describe("inline vs artifact rich content rendering", () => {
  const safeSvg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 180"><rect width="80" height="40" fill="#dbeafe"/></svg>';

  it("renders native SVG parts in inline mode by default with floating actions and no file-viewer header", () => {
    const svgPart = part("svg-1", "svg", 1, {
      block_id: "s1_arch",
      title: "架构示意图",
      view_box: viewBox,
      nodes: [group, box],
      svg_markup: safeSvg,
      state: "complete",
    });
    const { container } = render(<UnifiedArtifactCard part={svgPart} />);
    const root = container.querySelector("section.unified-artifact-card");
    expect(root).toHaveAttribute("data-display-mode", "inline");
    expect(root).toHaveClass("unified-artifact-card--inline");
    expect(container.querySelector(".unified-artifact-card__header")).toBeNull();
    expect(container.querySelector(".unified-artifact-card__floating-actions")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看源码 架构示意图" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全屏查看 架构示意图" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载 架构示意图" })).toBeInTheDocument();

    // Toggling source view reveals raw SVG and switches action button to 预览.
    fireEvent.click(screen.getByRole("button", { name: "查看源码 架构示意图" }));
    expect(container.querySelector("pre.unified-artifact-card__source--inline")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "预览 架构示意图" })).toBeInTheDocument();
  });

  it("keeps the exact same outer container and SVG element from streaming to completion", () => {
    const streamingPart: MessagePart = {
      id: "svg-stream",
      type: "svg",
      status: "streaming",
      sequence: 1,
      content: "流式图",
      data: { block_id: "s_live", title: "流式图", view_box: viewBox, nodes: [group, box], state: "streaming" },
    };
    const { container, rerender } = render(<UnifiedArtifactCard part={streamingPart} />);
    const outerBefore = container.querySelector("section.unified-artifact-card");
    const svgBefore = container.querySelector("svg.unified-artifact-card__native-svg") as SVGSVGElement | null;
    expect(outerBefore).toHaveAttribute("data-display-mode", "inline");
    expect(svgBefore?.style.aspectRatio).toBe("320 / 180");

    const completedPart: MessagePart = {
      ...streamingPart,
      status: "completed",
      data: { ...streamingPart.data, state: "complete", svg_markup: safeSvg },
    };
    rerender(<UnifiedArtifactCard part={completedPart} />);
    expect(container.querySelector("section.unified-artifact-card")).toBe(outerBefore);
    expect(container.querySelector("svg.unified-artifact-card__native-svg")).toBe(svgBefore);
  });

  it("preserves artifact mode for file deliverables and honors explicit display_mode overrides regardless of MIME type", () => {
    const fileSvg = part("file-svg", "sandbox_artifact", 1, {
      title: "deliverable.svg",
      path: "outputs/deliverable.svg",
      file_id: "file-svg-1",
      mime_type: "image/svg+xml",
      size_bytes: 512,
      svg_markup: safeSvg,
    });
    const { container: artContainer } = render(<UnifiedArtifactCard part={fileSvg} />);
    expect(artContainer.querySelector("section.unified-artifact-card")).toHaveAttribute("data-display-mode", "artifact");
    expect(artContainer.querySelector(".unified-artifact-card__header")).toBeInTheDocument();
    expect(artContainer.querySelector(".unified-artifact-card__floating-actions")).toBeNull();

    // Explicit inline override on a file-backed SVG switches to inline presentation.
    const inlineFileSvg = part("file-svg-inline", "sandbox_artifact", 2, {
      ...fileSvg.data,
      display_mode: "inline",
    });
    expect(resolveArtifactMeta(inlineFileSvg).displayMode).toBe("inline");
    const { container: inlineContainer } = render(<UnifiedArtifactCard part={inlineFileSvg} />);
    expect(inlineContainer.querySelector("section.unified-artifact-card")).toHaveAttribute("data-display-mode", "inline");
    expect(inlineContainer.querySelector(".unified-artifact-card__header")).toBeNull();

    // Explicit artifact override on a native :::svg renders the full artifact viewer header.
    const artifactNativeSvg = part("svg-art", "svg", 3, {
      block_id: "s_art",
      title: "独立产物图",
      view_box: viewBox,
      nodes: [group, box],
      svg_markup: safeSvg,
      display_mode: "artifact",
      state: "complete",
    });
    expect(resolveArtifactMeta(artifactNativeSvg).displayMode).toBe("artifact");
    const { container: nativeArtContainer } = render(<UnifiedArtifactCard part={artifactNativeSvg} />);
    expect(nativeArtContainer.querySelector("section.unified-artifact-card")).toHaveAttribute("data-display-mode", "artifact");
    expect(nativeArtContainer.querySelector(".unified-artifact-card__header")).toBeInTheDocument();
  });
});
