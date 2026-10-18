import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LazyStreamdown } from "@/components/ai-elements/lazy-streamdown";
import { PreviewableMarkdownPre } from "./html-code-preview";

// Exercise the real Markdown/pre/plugin dispatch. Browser smoke checks cover
// Mermaid's actual SVG layout, which jsdom cannot measure.
vi.mock("@streamdown/mermaid", () => ({
  mermaid: {
    name: "mermaid", type: "diagram", language: "mermaid",
    getMermaid: () => ({
      render: async () => ({ svg: '<svg xmlns="http://www.w3.org/2000/svg"><text>Rendered diagram</text></svg>' }),
    }),
  },
}));

beforeEach(() => {
  vi.stubGlobal("IntersectionObserver", class {
    constructor(private callback: IntersectionObserverCallback) {}
    observe(target: Element) {
      this.callback([{ isIntersecting: true, target } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
    }
    unobserve() {}
    disconnect() {}
    takeRecords() { return []; }
  });
});
afterEach(() => vi.unstubAllGlobals());

const components = { pre: PreviewableMarkdownPre };

// The first render imports the real Markdown/plugin modules and Mermaid's
// renderer chunk. A clean CI worker can exceed waitFor's default one second.
const diagramWaitOptions = { timeout: 5000 };

describe("Mermaid Markdown dispatch", () => {
  it("renders fenced Mermaid through the custom chat pre renderer", async () => {
    const view = render(
      <LazyStreamdown codeHighlight="plain" components={components}>
        {'Before\n\n```mermaid\ngraph LR\n A[Start] --> B[End]\n```\n\nAfter'}
      </LazyStreamdown>,
    );
    await waitFor(() => expect(view.container.querySelector('[data-streamdown="mermaid"] svg')).toBeInTheDocument(), diagramWaitOptions);
    expect(screen.getByText("Before")).toBeInTheDocument();
    expect(screen.getByText("After")).toBeInTheDocument();
    expect(view.container.querySelector('[data-streamdown="mermaid-block-actions"]')).toBeInTheDocument();
    expect(view.container.querySelector('[data-streamdown="inline-code"]')).toBeNull();
  }, 10000);

  it("loads diagram support when a later streamed update introduces a fence", async () => {
    const view = render(<LazyStreamdown codeHighlight="plain" components={components}>Intro</LazyStreamdown>);
    await screen.findByText("Intro");
    view.rerender(
      <LazyStreamdown codeHighlight="plain" components={components}>
        {'Intro\n\n~~~mermaid\ngraph TD\n A --> B\n~~~'}
      </LazyStreamdown>,
    );
    await waitFor(() => expect(view.container.querySelector('[data-streamdown="mermaid"] svg')).toBeInTheDocument(), diagramWaitOptions);
  }, 10000);

  it("preserves ordinary fenced code toolbars instead of treating fences as inline code", async () => {
    const view = render(
      <LazyStreamdown codeHighlight="plain" components={components}>
        {'```javascript\nconsole.log("hello");\n```'}
      </LazyStreamdown>,
    );
    await waitFor(() => expect(view.container.querySelector('[data-streamdown="code-block-header"]')).toBeInTheDocument());
    expect(view.container.querySelector('[data-streamdown="code-block-body"]')).toHaveTextContent('console.log("hello");');
  });
});
