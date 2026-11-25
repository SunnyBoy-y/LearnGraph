import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { listDocumentRevisions, listFiles, parseFile } from "@/api";
import { renderWithProviders } from "@/test/render";
import type { FileRecord } from "@/types/files";
import { SourcesPage } from "./resource-pages";

vi.mock("@/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api")>()),
  listFiles: vi.fn(),
  getFileStorageSummary: vi.fn(async () => ({ total_bytes: 10240, file_count: 21 })),
  listFileParserCapabilities: vi.fn(async () => []),
  listSourceRecords: vi.fn(async () => []),
  listFileChunks: vi.fn(async () => []),
  listFileReferences: vi.fn(async () => []),
  listDocumentRevisions: vi.fn(async () => []),
  parseFile: vi.fn(),
}));
vi.mock("@/hooks/use-file-stream-url", () => ({
  useFileStreamUrl: (fileId?: string) => ({ source: fileId ? `/preview/${fileId}` : "", failed: false }),
}));

function file(n: number, overrides: Partial<FileRecord> = {}): FileRecord {
  return {
    id: `${n}`, workspace_id: "ws", original_name: `notes-${n}.md`,
    created_at: new Date(Date.UTC(2026, 10, n)).toISOString(),
    mime_type: "text/markdown", size_bytes: n * 100, sha256: "x",
    storage_status: "stored", parse_status: "pending", parse_capability: "built_in",
    parser_name: null, parser_version: null, error_message: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(listFiles).mockResolvedValue([
    file(1, { original_name: "large.zip", size_bytes: 8192, parse_capability: "attachment_only" }),
    file(2, { original_name: "diagram.svg", mime_type: "image/svg+xml", parse_capability: "attachment_only" }),
    file(3, { parse_status: "failed", error_message: "解析器未能读取此文档" }),
    file(4, { parse_status: "processing" }),
    file(5, { parse_status: "indexed" }),
    ...Array.from({ length: 16 }, (_, i) => file(i + 6)),
  ]);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

const mount = () => renderWithProviders(<SourcesPage />);

describe("file library", () => {
  it("starts in a paginated list and sorts across the entire collection", async () => {
    const user = userEvent.setup();
    mount();
    const table = await screen.findByRole("table");
    expect(screen.getByRole("button", { name: "列表视图" })).toHaveAttribute("aria-pressed", "true");
    expect(within(table).getAllByRole("row")).toHaveLength(21);
    expect(screen.getByText("第 1–20 项 · 共 21 项")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "large.zip" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByText("第 21–21 项 · 共 21 项")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "排序：最近上传" }));
    await user.click(screen.getByRole("menuitem", { name: "大小从大到小" }));
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("large.zip");
  });

  it("keeps index counts disjoint and combines them with filename search", async () => {
    mount();
    await screen.findByRole("table");
    expect(screen.getByRole("button", { name: /全部\s*21/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /未建索引\s*16/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /解析中\s*1/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /仅附件\s*2/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "搜索资料" }), { target: { value: "DIAGRAM" } });
    expect(screen.getByText("第 1–1 项 · 共 1 项")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "diagram.svg" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "large.zip" })).not.toBeInTheDocument();
  });

  it("shows previews only in grid mode and keeps the selection across views", async () => {
    mount();
    await screen.findByRole("table");
    fireEvent.click(screen.getByRole("button", { name: /仅附件\s*2/ }));
    expect(document.querySelector("img[src='/preview/2']")).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: "选择 diagram.svg" }));
    expect(screen.getByRole("button", { name: "批量下载" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "批量建索引" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "网格视图" }));
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(document.querySelector("img[src='/preview/2']")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "选择 diagram.svg" })).toBeChecked();
  });

  it("opens details on demand and allows retrying a failed file", async () => {
    vi.mocked(parseFile).mockRejectedValueOnce(new Error("重试未完成"));
    mount();
    expect(listDocumentRevisions).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "解析失败：查看索引详情" }));
    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getByText(/解析器未能读取此文档/)).toBeInTheDocument();
    expect(await dialog.findByText("0 段")).toBeInTheDocument();
    expect(listDocumentRevisions).toHaveBeenCalledWith("3");
    fireEvent.click(dialog.getByRole("button", { name: "重新解析" }));
    await waitFor(() => expect(parseFile).toHaveBeenCalledWith("3"));
    expect(await dialog.findByRole("button", { name: "重新解析" })).toBeEnabled();
  });
});
