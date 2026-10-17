import "@/test/pdfjs-polyfills";
import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test/render";
import { MessagePartRenderer } from "./message-part-renderer";
import type { MessagePart } from "@/types/sessions";

vi.mock("@/features/auth/auth-context-value", () => ({
  useAuth: () => ({ workspaceId: "ws-test" }),
}));

const textPart: MessagePart = {
  id: "text", type: "text", status: "completed", content: "结论[ref_1][ref_3]。代码 `array[1]`。",
};
const sourcePart: MessagePart = {
  id: "sources", type: "source_list", status: "completed", data: { results: [
    { index: 1, title: "Original title", url: "https://one.example/paper", site_name: "First source" },
    { index: 2, title: "Original title", url: "https://one.example/paper" },
    { index: 3, title: "Third title", url: "https://three.example/paper", site_name: "Third source" },
  ] },
};

describe("citation Markdown integration", () => {
  it("keeps original indexes despite duplicate source-list entries", async () => {
    renderWithProviders(<MessagePartRenderer part={textPart} siblingParts={[textPart, sourcePart]} />);
    const trigger = await screen.findByRole("button", { name: /First source，共 2/ });
    expect(trigger).toHaveAttribute("data-citation-ids", "1,3");
    expect(screen.getByText("array[1]")).toBeInTheDocument();
    fireEvent.click(trigger);
    expect(await screen.findByRole("link", { name: /Third source/ })).toHaveAttribute("href", "https://three.example/paper");
    expect(screen.getAllByRole("link")).toHaveLength(2);
  });

  it("refreshes source metadata arriving after the text without changing the answer", async () => {
    const { rerender } = renderWithProviders(<MessagePartRenderer part={textPart} siblingParts={[textPart, sourcePart]} />);
    fireEvent.click(await screen.findByRole("button", { name: /First source/ }));
    const existingCard = await screen.findByRole("dialog");
    const updated: MessagePart = { ...sourcePart, data: { results: [
      { index: 1, title: "Updated title", url: "https://one.example/paper", site_name: "Updated publisher" },
      sourcePart.data!.results instanceof Array ? sourcePart.data!.results[2] : null,
    ] } };
    rerender(<MessagePartRenderer part={textPart} siblingParts={[textPart, updated]} streaming />);
    await screen.findByRole("button", { name: /Updated publisher/ });
    expect(await screen.findByRole("link", { name: /Updated title/ })).toHaveAttribute("href", "https://one.example/paper");
    expect(screen.getByRole("dialog")).toBe(existingCard);
    expect(screen.getByText("array[1]")).toBeInTheDocument();
  });

  it("preserves document locator and chunk navigation in the source card", async () => {
    const fileId = "12345678-1234-1234-1234-123456789012";
    const part: MessagePart = { ...textPart, content: `依据（依据文件：${fileId}，位置：paragraph:7）` };
    const docs: MessagePart = { ...sourcePart, data: { results: [
      { file_id: fileId, filename: "课程讲义.pdf", locator: "paragraph:7", chunk_id: "chunk-7" },
    ] } };
    renderWithProviders(<MessagePartRenderer part={part} siblingParts={[part, docs]} />);
    fireEvent.click(await screen.findByRole("button", { name: /课程讲义/ }));
    const link = await screen.findByRole("link", { name: /课程讲义/ });
    expect(link).toHaveAttribute("href", `/w/ws-test/documents/${fileId}?chunk=chunk-7&locator=paragraph%3A7`);
  });
});
