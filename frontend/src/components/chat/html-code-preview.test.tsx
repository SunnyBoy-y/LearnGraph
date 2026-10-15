import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MessageResponse } from "@/components/ai-elements/message";
import { IncrementalMarkdown } from "@/components/ai-elements/incremental-markdown";

import {
  HtmlCodePreview,
  isHtmlCodeLanguage,
  type MarkdownPreProps,
  PreviewableMarkdownPre,
} from "./html-code-preview";

describe("HTML code preview", () => {
  it("recognizes HTML fence aliases only", () => {
    expect(isHtmlCodeLanguage("html")).toBe(true);
    expect(isHtmlCodeLanguage("LANGUAGE-XHTML")).toBe(true);
    expect(isHtmlCodeLanguage("javascript")).toBe(false);
    expect(isHtmlCodeLanguage(undefined)).toBe(false);
  });

  it("runs source only after the frontend preview button is clicked", () => {
    render(
      <HtmlCodePreview code="<h1>Hello</h1>" language="html">
        <pre>
          <code>{"<h1>Hello</h1>"}</code>
        </pre>
      </HtmlCodePreview>,
    );

    expect(screen.queryByTitle("HTML 预览")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "预览运行 HTML" }));

    const iframe = screen.getByTitle("HTML 预览");
    expect(iframe).toHaveAttribute("sandbox", "allow-scripts");
    expect(iframe).toHaveAttribute("srcdoc");
    expect(iframe.getAttribute("srcdoc")).toContain("<h1>Hello</h1>");

    fireEvent.click(screen.getByRole("button", { name: "关闭预览 HTML" }));
    expect(screen.queryByTitle("HTML 预览")).toBeNull();
  });

  it("adds the button to HTML markdown pre nodes without changing other languages", () => {
    const { rerender } = render(
      <PreviewableMarkdownPre
        className="language-html"
        node={{ lang: "html", value: "<p>preview</p>" }}
      >
        <code className="language-html">{"<p>preview</p>"}</code>
      </PreviewableMarkdownPre>,
    );
    expect(screen.getByRole("button", { name: "预览运行 HTML" })).toBeInTheDocument();

    rerender(
      <PreviewableMarkdownPre
        className="language-javascript"
        node={{ lang: "javascript", value: "alert(1)" }}
      >
        <code className="language-javascript">alert(1)</code>
      </PreviewableMarkdownPre>,
    );
    expect(screen.queryByRole("button", { name: /预览运行/u })).toBeNull();
  });

  it("connects the settled markdown renderer to the HTML preview", async () => {
    render(
      <MessageResponse
        components={{
          pre: (props: MarkdownPreProps) => <PreviewableMarkdownPre {...props} />,
        }}
      >
        {"```html\n<div>from markdown</div>\n```"}
      </MessageResponse>,
    );

    const button = await screen.findByRole("button", { name: "预览运行 HTML" });
    expect(document.querySelector('[data-streamdown="code-block"]')).toBeInTheDocument();
    expect(document.querySelector('[data-streamdown="code-block-header"]')).toBeInTheDocument();
    expect(document.querySelector('[data-streamdown="code-block-body"]')?.textContent).toContain(
      "<div>from markdown</div>",
    );
    fireEvent.click(button);
    await waitFor(() => {
      expect(screen.getByTitle("HTML 预览")).toBeInTheDocument();
    });
    expect(document.querySelector(".html-code-preview__source")).toBeNull();
    expect(screen.getByTitle("HTML 预览").getAttribute("srcdoc")).toContain(
      "<div>from markdown</div>",
    );
  });

  it("keeps the streaming markdown renderer on the same preview path", () => {
    render(
      <IncrementalMarkdown
        text={"```html\n<button>streamed</button>\n```"}
      />,
    );

    expect(screen.getByRole("button", { name: "预览运行 HTML" })).toBeInTheDocument();
  });
});
