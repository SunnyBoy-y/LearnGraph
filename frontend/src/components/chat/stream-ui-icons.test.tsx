import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StreamUI } from "./stream-ui";
import { parseUISnapshot } from "@/lib/stream-ui";
import { documentMarkdown } from "@/genui/document-export";

vi.mock("./message-part-renderer", () => ({TextWithCitations: ({content}: {content: string}) => <p>{content}</p>}));
const doc = (blocks: unknown[], revision = 0) => ({protocol: "suip/0.1", block_id: "icons", component: "document", revision,
  state: "streaming", props: {title: "Hidden document title", icon: "fluent:rocket"}, blocks});

function expectLocalIcons(container: HTMLElement) {
  const icons = container.querySelectorAll<HTMLImageElement>("img.answer-icon");
  expect(icons.length).toBeGreaterThan(0);
  for (const icon of icons) expect(icon.getAttribute("src")).toMatch(/^\/fluent-emoji\/.*\.webp$/);
}

describe("Stream UI AnswerIcon integration", () => {
  it.each(["timeline", "metric"])("renders %s title and item icons without changing labels", component => {
    const {container} = render(<StreamUI partId={`icons-${component}`} data={{protocol: "suip/0.1", block_id: "visual", component,
      state: "completed", revision: 0, props: {title: "Visual title", icon: "fluent:rocket"},
      items: [{id: "first", label: "Fluent item", icon: "fluent:direct-hit", value: 23},
        {id: "legacy", label: "Legacy item", icon: "brain-circuit", value: 4}]}} />);
    expect(screen.getByRole("heading", {name: "Visual title"}).querySelector("img.answer-icon")).not.toBeNull();
    expect(screen.getByText("Fluent item").querySelector("img.answer-icon")).not.toBeNull();
    expect(screen.getByText("Legacy item").querySelector("svg")).not.toBeNull();
    expectLocalIcons(container);
  });

  it("renders document surface, heading, paragraph, layout and accessible standalone icons", async () => {
    const {container} = render(<StreamUI partId="icons-document" data={doc([
      {id: "head", type: "heading", text: "Heading", icon: "fluent:brain"},
      {id: "para", type: "paragraph", text: "Paragraph", icon: "fluent:light-bulb"},
      {id: "layout", type: "section", label: "Section", icon: "zap"},
      {id: "standalone", type: "icon", icon: "fluent:direct-hit", alt: "Accessible target", label: "Unused fallback"},
      {id: "label-only", type: "icon", icon: "book-open", label: "Legacy book"},
      {id: "quote", type: "quote", text: "Quotation", icon: "fluent:rocket"},
    ])} />);
    expect(await screen.findByRole("heading", {name: "Heading"})).toContainElement(container.querySelector('[id$="-head"] img'));
    expect(screen.getByText("Paragraph").closest(".rich-doc__icon-copy")?.querySelector("img")).not.toBeNull();
    expect(screen.getByRole("heading", {name: "Section"}).querySelector("svg")).not.toBeNull();
    expect(screen.getByRole("img", {name: "Accessible target"})).toHaveClass("answer-icon--large");
    expect(screen.getByRole("img", {name: "Legacy book"})).toHaveClass("answer-icon--large");
    expect(screen.getByRole("img", {name: "Hidden document title"})).toBeInTheDocument();
    expect(screen.queryByRole("heading", {name: "Hidden document title"})).toBeNull();
    expect(screen.queryByText("Unused fallback")).toBeNull();
    expect(screen.getByText("Quotation").closest(".rich-doc__node")?.querySelector(".rich-doc__node-icon img")).not.toBeNull();
    expectLocalIcons(container);
  });

  it("keeps numbered/status markers and puts explicit icons beside their titles", async () => {
    const blocks = [
      {id: "features", type: "feature_list", label: "Features", icon: "fluent:rocket"},
      {id: "feature", type: "rail_item", parent_id: "features", label: "Feature", icon: "fluent:brain"},
      {id: "steps", type: "step_list"},
      {id: "step", type: "rail_item", parent_id: "steps", label: "Step", icon: "fluent:direct-hit"},
      {id: "statuses", type: "status_list"},
      {id: "status", type: "rail_item", parent_id: "statuses", label: "Status", item_status: "completed", icon: "zap"},
    ];
    const {container, rerender} = render(<StreamUI partId="icons-rails" data={doc(blocks)} />);
    expect(await screen.findByRole("heading", {name: "Step"})).toBeInTheDocument();
    expect(container.querySelector('[data-item-id="step"] .genui-rail-list__number')).toHaveTextContent("1");
    expect(screen.getByRole("heading", {name: "Step"}).querySelector("img")).not.toBeNull();
    expect(screen.getByRole("img", {name: "已完成"})).toBeInTheDocument();
    expect(screen.getByRole("heading", {name: "Status"}).querySelector("svg")).not.toBeNull();
    const feature = container.querySelector('[data-item-id="feature"]');
    rerender(<StreamUI partId="icons-rails" data={doc(blocks.map(node => node.id === "feature" ? {...node, icon: "fluent:rocket", text: "Patched"} : node), 1)} />);
    expect(await screen.findByText("Patched")).toBeInTheDocument();
    expect(container.querySelector('[data-item-id="feature"]')).toBe(feature);
    expectLocalIcons(container);
  });

  it("retains selected tabs, accordion and simulation state across icon patches and remounts", async () => {
    const blocks = [{id: "tabs", type: "tabs"},
      {id: "one", parent_id: "tabs", type: "paragraph", label: "First", text: "First body", icon: "fluent:brain"},
      {id: "two", parent_id: "tabs", type: "paragraph", label: "Second", text: "Second body", icon: "zap"},
      {id: "accordion", type: "accordion"}, {id: "panel", parent_id: "accordion", type: "paragraph", label: "Details", text: "Details body", icon: "fluent:rocket"},
      {id: "simulation", type: "simulation", engine: "chase"}];
    const {rerender} = render(<StreamUI partId="icons-state" data={doc(blocks)} />);
    const second = await screen.findByRole("tab", {name: "Second"});
    expect(second.querySelector("svg")).not.toBeNull();
    fireEvent.click(second);
    const summary = screen.getByText("Details", {selector: "summary"});
    expect(summary.querySelector("img")).not.toBeNull();
    const details = summary.closest("details")!;
    details.open = true; fireEvent(details, new Event("toggle"));
    fireEvent.change(screen.getByRole("slider", {name: "追赶速度"}), {target: {value: "3"}});
    fireEvent.click(screen.getByRole("button", {name: "播放"}));
    const patched = doc(blocks.map(node => ({...node, icon: "fluent:direct-hit"})), 1);
    rerender(<StreamUI partId="icons-state" data={patched} />);
    expect(await screen.findByRole("tab", {name: "Second"})).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Details", {selector: "summary"}).closest("details")).toHaveAttribute("open");
    expect(screen.getByRole("slider", {name: "追赶速度"})).toHaveValue("3");
    rerender(<section><StreamUI partId="icons-state" data={{...patched, state: "completed", revision: 2}} /></section>);
    expect(await screen.findByRole("tab", {name: "Second"})).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Details", {selector: "summary"}).closest("details")).toHaveAttribute("open");
    expect(screen.getByRole("slider", {name: "追赶速度"})).toHaveValue("3");
  });

  it("exports semantic Unicode for document, rail, standalone and embedded item icons", () => {
    const parsed = parseUISnapshot(doc([
      {id: "heading", type: "heading", text: "Heading", icon: "fluent:rocket"},
      {id: "paragraph", type: "paragraph", text: "Body", icon: "fluent:brain"},
      {id: "steps", type: "step_list"}, {id: "step", parent_id: "steps", type: "rail_item", label: "Step", icon: "fluent:direct-hit"},
      {id: "icon", type: "icon", icon: "fluent:direct-hit", alt: "Target"},
      {id: "legacy", type: "heading", text: "Legacy", icon: "zap"},
      {id: "metric", type: "metric", visual: {props: {title: "Metric", icon: "fluent:rocket"}, items: [{id: "item", label: "Item", icon: "fluent:brain", value: 42}]}},
    ]))!;
    expect(parsed).not.toBeNull();
    const markdown = documentMarkdown(parsed);
    for (const text of ["# 🚀 Hidden document title", "### 🚀 Heading", "🧠 Body", "1. **🎯 Step**", "🎯 Target", "### Legacy", "🚀 Metric", "🧠 Item"])
      expect(markdown).toContain(text);
    expect(markdown).not.toMatch(/fluent:|https?:|<img|zap/);
  });
});
