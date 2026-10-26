import "@/test/pdfjs-polyfills";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test/render";
import { MessagePartRenderer, TextWithCitations } from "./message-part-renderer";
import type { MessagePart } from "@/types/sessions";

vi.mock("@/features/auth/auth-context-value", () => ({ useAuth: () => ({ workspaceId: "ws-test" }) }));

describe("document nodes reuse the product Markdown pipeline", () => {
  it("renders already-delimited math nodes with KaTeX instead of nested math errors", async () => {
    const {container} = renderWithProviders(<MessagePartRenderer part={{id:"math-regression",type:"ui",status:"completed",data:{
      protocol:"suip/0.1",component:"document",block_id:"formula-regression",revision:1,state:"completed",props:{title:"公式"},
      blocks:[{id:"formula",type:"math",text:String.raw`$$t_{\text{catch}} = \frac{d}{v_{\text{me}}-v_{\text{question}}}$$`}],
    }}} />);
    await waitFor(() => expect(container.querySelector(".katex")).not.toBeNull());
    expect(container.querySelector(".katex-error")).toBeNull();
    expect(container.querySelector("annotation")?.textContent).not.toContain("$$");
  });
  it("resolves source references inside layouts with the existing citation component", async () => {
    const ui: MessagePart = {id:"document-citation",type:"ui",status:"completed",data:{
      protocol:"suip/0.1",component:"document",block_id:"citations",revision:1,state:"completed",props:{title:"说明"},
      blocks:[{id:"section",type:"section"},{id:"paragraph",parent_id:"section",type:"paragraph",text:"结论来自资料[ref_1]。"}],
    }};
    const sources: MessagePart = {id:"sources",type:"source_list",status:"completed",data:{results:[{index:1,title:"Research",url:"https://example.com/paper",site_name:"Example publisher"}]}};
    renderWithProviders(<MessagePartRenderer part={ui} siblingParts={[ui,sources]} />);
    expect(await screen.findByRole("button",{name:/Example publisher/})).toHaveAttribute("data-citation-ids","1");
  });

  it("uses the existing link presentation in document paragraphs", async () => {
    renderWithProviders(<TextWithCitations safeDocument content="参阅[官方文档](https://example.com/docs)。" />);
    const link = await screen.findByRole("link",{name:/官方文档/});
    expect(link.querySelector(".message-link-arrow")).not.toBeNull();
    expect(link).toHaveAttribute("rel","noopener noreferrer");
  });

  it("does not load inline model image URLs or mount HTML code previews", async () => {
    const {container} = renderWithProviders(<TextWithCitations safeDocument content={'<img src="https://evil.example/raw.png" onerror="alert(1)" />\n\n![替代文字](https://evil.example/model.png)\n\n```html\n<script>alert(1)</script>\n```'} />);
    await screen.findByText("替代文字");
    expect(container.querySelector('img[src*="evil.example"]')).toBeNull();
    expect(container.querySelector("iframe")).toBeNull();
    expect(screen.queryByRole("button",{name:/预览/})).toBeNull();
  });

  it("stops pending resource indicators when a saved message is no longer generating", async () => {
    const ui: MessagePart = {id:"document-interrupted",type:"ui",status:"completed",data:{
      protocol:"suip/0.1",component:"document",block_id:"stopped",revision:1,state:"completed",props:{title:"说明"},
      blocks:[{id:"photo",type:"image",query:"Saturn",alt:"土星图片",resource_state:"pending"}],
    }};
    renderWithProviders(<MessagePartRenderer part={ui} />);
    expect(await screen.findByText("土星图片")).toBeInTheDocument();
    expect(screen.queryByText("正在查找相关图片…")).toBeNull();
  });
});
