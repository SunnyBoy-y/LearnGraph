import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SVGBlock } from "./svg-block";
import { groupPartsForDisplay } from "@/features/chat/chat-message-parts";
import { reconcileArtifacts } from "@/features/chat/response-composer";
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
