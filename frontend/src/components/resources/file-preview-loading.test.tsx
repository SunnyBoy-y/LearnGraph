import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { downloadFile, fileStreamUrl, getFile, readFileRange, uploadFile } from "@/api/files";
import { FilePreviewContent } from "./file-preview-content";

vi.mock("@/api/files", () => ({ downloadFile: vi.fn(), getFile: vi.fn(), readFileRange: vi.fn(), fileStreamUrl: vi.fn(), uploadFile: vi.fn() }));
vi.mock("./file-preview", () => ({ FilePreviewCanvas: (props: { filename: string; source?: unknown; mediaUrl?: string }) => <div data-testid="reader" data-seekable={!!props.source} data-media={props.mediaUrl}>{props.filename}</div> }));

describe("bounded shared preview loading", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(readFileRange).mockResolvedValue({ size: 100, data: new Uint8Array(100) });
  });

  it.each(["report.doc", "table.xls", "slides.ppt", "large.docx", "large.xlsx", "large.pptx"])("requests a seekable PDF for %s without buffering the source", async (filename) => {
    render(<FilePreviewContent fileId="office" filename={filename} fileSize={32 * 1024 * 1024} />);
    const reader = await screen.findByTestId("reader");
    expect(reader).toHaveTextContent(`${filename}.pdf`);
    expect(reader).toHaveAttribute("data-seekable", "true");
    expect(readFileRange).toHaveBeenCalledWith("office", 0, 65536, expect.anything(), true);
    expect(downloadFile).not.toHaveBeenCalled();
  });

  it("keeps large CSV as seekable text instead of sending it to an Office converter", async () => {
    render(<FilePreviewContent fileId="csv" filename="large.csv" fileSize={200 * 1024 * 1024} />);
    expect(await screen.findByTestId("reader")).toHaveTextContent("large.csv.txt");
    expect(readFileRange).toHaveBeenCalledWith("csv", 0, 65536, expect.anything(), false);
    expect(downloadFile).not.toHaveBeenCalled();
  });

  it("streams remote media through a scoped URL", async () => {
    vi.mocked(fileStreamUrl).mockResolvedValue("/files/audio/stream?ticket=scoped");
    render(<FilePreviewContent fileId="audio" filename="large.mp3" />);
    expect(await screen.findByTestId("reader")).toHaveAttribute("data-media", "/files/audio/stream?ticket=scoped");
    expect(downloadFile).not.toHaveBeenCalled();
    expect(getFile).not.toHaveBeenCalled();
  });

  it("only uploads a local legacy document on click and aborts on close", async () => {
    let signal: AbortSignal | undefined;
    vi.mocked(uploadFile).mockImplementation((_file, options) => { signal = options?.signal; return new Promise(() => {}); });
    const { unmount } = render(<FilePreviewContent filename="local.doc" blob={new Blob(["legacy"])} />);
    const button = await screen.findByRole("button", { name: "上传并阅读" });
    expect(uploadFile).not.toHaveBeenCalled();
    fireEvent.click(button);
    await waitFor(() => expect(uploadFile).toHaveBeenCalledOnce());
    unmount();
    expect(signal?.aborted).toBe(true);
  });
});
