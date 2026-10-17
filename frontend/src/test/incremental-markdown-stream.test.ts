import { describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { render } from "@testing-library/react";
import {
  IncrementalMarkdown,
  IncrementalMarkdownParser,
} from "@/components/ai-elements/incremental-markdown";

/** Records what the block renderer did, so tests can tell re-render from remount. */
const streams = vi.hoisted(() => ({
  mounts: [] as string[],
  renders: [] as Array<{ src: string; codeHighlight: unknown }>,
}));

// The real LazyStreamdown lazy-loads streamdown + shiki; these tests only care
// about which blocks render, whether React reuses their instances (a remount
// means a dropped parse/key), and which highlight mode they were rendered in.
vi.mock("@/components/ai-elements/lazy-streamdown", async () => {
  const { createElement: h, useRef } = await import("react");
  return {
    LazyStreamdown: (props: { children?: string; codeHighlight?: unknown }) => {
      const mounted = useRef(false);
      const src = props.children ?? "";
      if (!mounted.current) {
        mounted.current = true;
        streams.mounts.push(src);
      }
      streams.renders.push({ src, codeHighlight: props.codeHighlight });
      return h("div", { "data-block": src }, src);
    },
  };
});

/**
 * Describe the rendered block partition: leaf blocks report their source slice,
 * container elements (list/blockquote) report their tag and recurse. Everything
 * downstream is derived from the split, so equal descriptions mean equal splits.
 */
function describeBlocks(container: HTMLElement): unknown[] {
  return Array.from(container.children).map((child) => {
    const block = child.getAttribute("data-block");
    if (block !== null) return ["block", block];
    return [child.tagName, describeBlocks(child as HTMLElement)];
  });
}

function resetStreams(): void {
  streams.mounts.length = 0;
  streams.renders.length = 0;
}

const DOC_1 = "## Title\n\nFirst paragraph stays.\n\nSecond paragraph.\n\nThird paragraph";
const DOC_2 = "## Title\n\nFirst paragraph stays.\n\nSecond paragraph.\n\nThird paragraph here";

describe("IncrementalMarkdown streaming", () => {
  it("renders a closed Mermaid fence while later prose is still streaming", () => {
    resetStreams();
    const epoch = {};
    const source = "```mermaid\ngraph LR\n A --> B";
    const view = render(createElement(IncrementalMarkdown, { text: source, epoch }));
    expect(streams.renders).toHaveLength(0);
    view.rerender(createElement(IncrementalMarkdown, { text: `${source}\n\`\`\`\n\nMore text`, epoch }));
    expect(streams.renders.some(({ src }) => src === `${source}\n\`\`\``)).toBe(true);
    const renders = streams.renders.filter(({ src }) => src.startsWith("```mermaid")).length;
    view.rerender(createElement(IncrementalMarkdown, { text: `${source}\n\`\`\`\n\nMore text arriving`, epoch }));
    expect(streams.renders.filter(({ src }) => src.startsWith("```mermaid"))).toHaveLength(renders);
  });

  it("keeps block instances when a full part replacement is still an append", () => {
    resetStreams();
    const epoch = {};
    const view = render(createElement(IncrementalMarkdown, { text: DOC_1, epoch }));
    view.rerender(createElement(IncrementalMarkdown, { text: DOC_2, epoch }));
    const mountsBefore = streams.mounts.length;
    expect(mountsBefore).toBeGreaterThan(1);
    expect(streams.mounts.filter((src) => src.startsWith("## Title")).length).toBe(1);

    // part.completed re-sends the whole content on a fresh epoch; the bytes are
    // still an append, so the frozen prefix and every element must survive.
    view.rerender(
      createElement(IncrementalMarkdown, { text: `${DOC_2} and more text`, epoch: {} }),
    );

    expect(streams.mounts.length).toBe(mountsBefore);
    expect(streams.mounts.filter((src) => src.startsWith("## Title")).length).toBe(1);
    expect(view.container.textContent).toContain("Third paragraph here and more text");
  });

  it("re-renders a rewrite in place when its leading blocks are unchanged", () => {
    resetStreams();
    const before = "## Title\n\nFirst paragraph stays.\n\nSecond paragraph.\n\nThird paragraph one";
    const after = "## Title\n\nFirst paragraph stays.\n\nSecond paragraph.\n\nThird paragraph two";
    const view = render(createElement(IncrementalMarkdown, { text: before, epoch: {} }));
    const mountsBefore = streams.mounts.length;
    expect(mountsBefore).toBeGreaterThan(1);

    // A real rewrite resets the parser (no shared prefix), but React keys are
    // source offsets: the untouched leading blocks update instead of remounting.
    view.rerender(createElement(IncrementalMarkdown, { text: after, epoch: {} }));

    expect(view.container.textContent).toContain("Third paragraph two");
    expect(streams.mounts.length).toBe(mountsBefore);
  });

  it("keeps the parse state when the code highlight mode flips", () => {
    resetStreams();
    const epoch = {};
    const view = render(
      createElement(IncrementalMarkdown, { text: DOC_1, epoch, codeHighlight: "plain" }),
    );
    view.rerender(createElement(IncrementalMarkdown, { text: DOC_2, epoch, codeHighlight: "plain" }));
    const mountsBefore = streams.mounts.length;
    const rendersBefore = streams.renders.length;

    view.rerender(createElement(IncrementalMarkdown, { text: DOC_2, epoch, codeHighlight: "shiki" }));

    const flipped = streams.renders.slice(rendersBefore);
    expect(flipped.length).toBeGreaterThan(0);
    expect(flipped.every((entry) => entry.codeHighlight === "shiki")).toBe(true);
    // Re-rendered in the new mode, but reused: switching modes must not reparse
    // the document (no generation bump) nor remount its blocks.
    expect(streams.mounts.length).toBe(mountsBefore);
  });

});

describe("IncrementalMarkdownParser stream epochs", () => {
  it("keeps append state within an epoch and resets on a rewrite", () => {
    const parser = new IncrementalMarkdownParser();
    const epoch = {};
    parser.update("alpha one\n\nbeta two\n\ngamma three\n\ndelta four", epoch);
    const streamed = parser.update("alpha one\n\nbeta two\n\ngamma three\n\ndelta four five", epoch);
    expect(streamed.frozen.length).toBeGreaterThan(0);

    const completed = parser.update("alpha one\n\nbeta two\n\ngamma three\n\ndelta four five six", epoch);
    expect(completed.generation).toBe(streamed.generation);
    expect(completed.frozen[0]).toBe(streamed.frozen[0]);

    const rewritten = parser.update("something else entirely", epoch);
    expect(rewritten.generation).toBeGreaterThan(completed.generation);
    expect(rewritten.frozen).toHaveLength(0);
  });
});
