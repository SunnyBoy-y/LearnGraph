import "@/test/pdfjs-polyfills";
import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test/render";
import { visibleStreamUIText } from "@/lib/stream-ui";
import { MessagePartRenderer } from "./message-part-renderer";

vi.mock("@/features/auth/auth-context-value", () => ({
  useAuth: () => ({ workspaceId: "ws-test" }),
}));

const markers = [
  "[[artifact:card_inst_6f265bd7c31c]]",
  "[[artifact:file_id:file-1]]",
  "[[artifact:bundle_id:bundle-1|layout=side]]",
  "[[artifact:block_id:chart-1|layout=block]]",
  "[[ ARTIFACT : card_inst_1 ]]",
];

describe("artifact placement marker visibility", () => {
  it.each(markers)("hides %s in settled and streaming answers", (marker) => {
    expect(visibleStreamUIText(`前文${marker}后文`, false)).toBe("前文后文");
    // Check every token boundary, including a single closing bracket.
    for (let end = 2; end <= marker.length; end++) {
      expect(visibleStreamUIText(`前文${marker.slice(0, end)}`, true)).toBe("前文");
    }
  });

  it("preserves ordinary bracketed text and surrounding Markdown", () => {
    const content = "**已更新**\n\n[[参考资料]] [artifact:示例]\n\n- 头盔\n- 风镜";
    expect(visibleStreamUIText(content, true)).toBe(content);
    expect(visibleStreamUIText(content, false)).toBe(content);
  });

  it("filters historical text without needing a UI sibling and keeps surrounding content", async () => {
    const { container } = renderWithProviders(<MessagePartRenderer part={{
      id: "answer", type: "text", status: "completed",
      content: "已更新完成。\n\n[[artifact:card_inst_6f265bd7c31c]]\n\n**这次改动的内容：**\n\n- 头盔\n- 风镜",
    }} />);
    expect(await screen.findByText("这次改动的内容：")).toBeVisible();
    expect(screen.getByText("头盔")).toBeVisible();
    expect(container.textContent).not.toContain("[[artifact:");
  });

  it("does not leak partial references before the artifact part arrives", async () => {
    const { container } = renderWithProviders(<MessagePartRenderer streaming part={{
      id: "answer", type: "text", status: "streaming",
      content_delta: "已更新完成。[[artifact:card_inst_6f265bd7c31c]",
    }} />);
    await waitFor(() => expect(screen.getByText("已更新完成。")).toBeVisible());
    expect(container.textContent).not.toContain("artifact:");
  });
});
