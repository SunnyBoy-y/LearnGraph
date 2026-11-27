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

/** 索引状态现在收在统一筛选菜单里。 */
async function applyStatusFilter(
  user: ReturnType<typeof userEvent.setup>,
  label: RegExp,
) {
  await user.click(screen.getByRole("button", { name: "筛选资料" }));
  const menu = within(await screen.findByRole("menu"));
  await user.click(menu.getByRole("menuitem", { name: label }));
}

describe("file library", () => {
  it("restores the URL page and size, validates jumps, and resets on search", async () => {
    vi.mocked(listFiles).mockResolvedValueOnce(Array.from({ length: 85 }, (_, i) => file(i + 1)));
    const user = userEvent.setup();
    renderWithProviders(<SourcesPage />, { initialEntries: ["/w/ws/sources?page=2&pageSize=40"] });
    expect(await screen.findByText("第 41–80 项 · 共 85 项")).toBeInTheDocument();
    const pagination = within(screen.getByRole("navigation", { name: "分页" }));
    expect(pagination.getByRole("button", { name: "第 2 页" })).toHaveAttribute("aria-current", "page");
    const jump = pagination.getByRole("textbox", { name: "跳转页码" });
    await user.type(jump, "4{Enter}");
    expect(pagination.getByRole("alert")).toHaveTextContent("请输入 1–3 的整数页码");
    await user.clear(jump);
    await user.type(jump, "3{Enter}");
    expect(screen.getByText("第 81–85 项 · 共 85 项")).toBeInTheDocument();
    await user.selectOptions(pagination.getByRole("combobox"), "60");
    expect(screen.getByText("第 1–60 项 · 共 85 项")).toBeInTheDocument();
    await user.click(pagination.getByRole("button", { name: "第 2 页" }));
    expect(screen.getByText("第 61–85 项 · 共 85 项")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索资料" }), { target: { value: "notes" } });
    expect(screen.getByText("第 1–60 项 · 共 85 项")).toBeInTheDocument();
    expect(listFiles).toHaveBeenCalledTimes(1);
  });

  it("starts in a paginated grid and sorts across the entire collection", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("button", { name: "查看 notes-21.md" });
    expect(screen.getByRole("button", { name: "网格视图" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("article")).toHaveLength(20);
    expect(screen.getByText("第 1–20 项 · 共 21 项")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "large.zip" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByText("第 21–21 项 · 共 21 项")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "排序：最近上传" }));
    await user.click(screen.getByRole("menuitem", { name: "大小从大到小" }));
    expect(screen.getAllByRole("article")[0]).toHaveTextContent("large.zip");
  });

  it("keeps index counts inside the filter menu and combines them with filename search", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("button", { name: "查看 notes-21.md" });
    // 一级导航只留文件来源；索引状态不再常驻成第二层导航。
    expect(screen.getByRole("navigation", { name: "文件来源" })).toBeInTheDocument();
    const toolbar = within(document.querySelector(".file-library-toolbar") as HTMLElement);
    expect(toolbar.getByRole("button", { name: "筛选资料" })).toBeInTheDocument();
    expect(toolbar.queryByRole("button", { name: /可问答/ })).not.toBeInTheDocument();
    expect(toolbar.queryByRole("button", { name: /未建索引/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "其他状态" })).not.toBeInTheDocument();
    await applyStatusFilter(user, /仅附件\s*2/);
    // 统计口径仍然互斥：可问答 1、仅附件 2、未建索引 16。
    await user.click(screen.getByRole("button", { name: "筛选资料" }));
    const menu = within(await screen.findByRole("menu"));
    expect(menu.getByRole("menuitem", { name: /可问答\s*1/ })).toBeInTheDocument();
    expect(menu.getByRole("menuitem", { name: /仅附件\s*2/ })).toBeInTheDocument();
    expect(menu.getByRole("menuitem", { name: /未建索引\s*16/ })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    // 用过的筛选条件才显示成可移除的标签。
    expect(screen.getByRole("button", { name: "移除筛选 状态：仅附件" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索资料" }), { target: { value: "DIAGRAM" } });
    expect(screen.getByText("第 1–1 项 · 共 1 项")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "diagram.svg" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "large.zip" })).not.toBeInTheDocument();
    // 清除筛选后回到全部资料，标签随之消失。
    await user.click(screen.getByRole("button", { name: "清除筛选" }));
    expect(screen.queryByRole("button", { name: /移除筛选/ })).not.toBeInTheDocument();
    expect(screen.getByText("第 1–20 项 · 共 21 项")).toBeInTheDocument();
  });

  it("shows previews only in grid mode and keeps the selection across views", async () => {
    const user = userEvent.setup();
    mount();
    await screen.findByRole("button", { name: "查看 notes-21.md" });
    await applyStatusFilter(user, /仅附件\s*2/);
    expect(document.querySelector("img[src='/preview/2']")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "选择 diagram.svg" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "多选" }));
    await user.click(screen.getByRole("checkbox", { name: "选择 diagram.svg" }));
    expect(screen.getByRole("button", { name: "批量下载" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "批量建索引" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "列表视图" }));
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(document.querySelector("img[src='/preview/2']")).toBeNull();
    expect(screen.getByRole("checkbox", { name: "选择 diagram.svg" })).toBeChecked();
    await user.click(screen.getByRole("checkbox", { name: "全选当前页" }));
    expect(screen.getByText("已选 2 项")).toBeInTheDocument();
  });

  it("combines origin with index state and opens a non-modal file inspector", async () => {
    vi.mocked(listFiles).mockResolvedValueOnce([
      file(1, { original_name: "archive.zip", parse_capability: "attachment_only", origin: "conversation", source_session_id: "chat", source_session_title: "来源对话" }),
      file(2, { origin: "conversation", parse_status: "indexed" }),
      file(3, { origin: "generated", parse_capability: "attachment_only" }),
    ]);
    const user = userEvent.setup();
    mount();
    await screen.findByRole("button", { name: "查看 archive.zip" });
    const originNav = within(screen.getByRole("navigation", { name: "文件来源" }));
    await user.click(originNav.getByRole("button", { name: /会话附件\s*2/ }));
    expect(originNav.getByRole("button", { name: /全部文件\s*3/ })).toBeInTheDocument();
    await applyStatusFilter(user, /仅附件\s*1/);
    const trigger = screen.getByRole("button", { name: "查看 archive.zip" });
    await user.click(trigger);
    const inspector = within(screen.getByRole("complementary", { name: "文件详情" }));
    expect(inspector.getByRole("heading", { name: "archive.zip" })).toBeInTheDocument();
    expect(inspector.getByRole("button", { name: /来源对话/ })).toBeInTheDocument();
    expect(inspector.getByRole("button", { name: "围绕此文件提问" })).toBeEnabled();
    expect(inspector.getByRole("button", { name: "下载" })).toBeInTheDocument();
    // 覆盖式抽屉：列表与筛选工具栏仍在原位，没有被挤压或卸载。
    expect(screen.getByRole("textbox", { name: "搜索资料" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("complementary", { name: "文件详情" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
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
