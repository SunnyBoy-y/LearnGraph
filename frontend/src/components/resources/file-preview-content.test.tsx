import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { downloadFile } from "@/api/files";
import { FilePreviewDialog } from "./file-preview-dialog";
import { FilePreviewContent } from "./file-preview-content";

vi.mock("@/api/files", () => ({ downloadFile: vi.fn(), getFile: vi.fn(), readFileRange: vi.fn(), fileStreamUrl: vi.fn(), uploadFile: vi.fn() }));
const textBlob = (text: string) => new Blob([text], { type: "text/plain" });

describe("shared file previews", () => {
  it("opens B after closing A, fetching and displaying B's own content", async () => {
    vi.mocked(downloadFile).mockImplementation(async (id) => textBlob(`content ${id}`));
    const props = { open: true, fileId: "A", fileSize: 32, filename: "A.txt", onOpenChange: vi.fn() };
    const { rerender } = render(<FilePreviewDialog {...props} />);
    await screen.findByText("content A");
    rerender(<FilePreviewDialog {...props} open={false} />);
    rerender(<FilePreviewDialog {...props} fileId="B" filename="B.txt" />);
    expect(screen.queryByText("content A")).toBeNull();
    await screen.findByText("content B");
    expect(vi.mocked(downloadFile).mock.calls.map(([id]) => id)).toEqual(["A", "B"]);
  });

  it("cancels a pending A and ignores its late response after switching to B", async () => {
    let resolveA!: (blob: Blob) => void;
    let signalA: AbortSignal | undefined;
    vi.mocked(downloadFile).mockImplementation((id, options) => {
      if (id === "B") return Promise.resolve(textBlob("B wins"));
      signalA = options?.signal;
      return new Promise((resolve) => { resolveA = resolve; });
    });
    const { rerender } = render(<FilePreviewContent fileId="A" fileSize={32} filename="A.txt" />);
    rerender(<FilePreviewContent fileId="B" fileSize={32} filename="B.txt" />);
    await screen.findByText("B wins");
    expect(signalA?.aborted).toBe(true);
    await act(async () => resolveA(textBlob("stale A")));
    expect(screen.queryByText("stale A")).toBeNull();
    expect(screen.getByText("B wins")).toBeInTheDocument();
  });

  it("clears an old error when the source changes and accepts local unsent files", async () => {
    vi.mocked(downloadFile).mockRejectedValue(new Error("source missing"));
    const { rerender } = render(<FilePreviewContent fileId="missing" fileSize={32} filename="missing.txt" />);
    await screen.findByRole("alert");
    rerender(<FilePreviewContent blob={textBlob("local content")} filename="local.txt" />);
    await screen.findByText("local content");
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });
});
