import { cleanup, screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { listDocumentConceptBranches, listDocumentRevisions, listFileChunks, listFiles } from "@/api/files";
import { listGoals } from "@/api/goals";
import { listProviders } from "@/api/providers";
import { renderWithProviders } from "@/test/render";
import type { FileRecord } from "@/types/files";

import { DocumentLearningPage } from "./document-learning-page";

vi.mock("@/api/files", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/files")>()),
  listFiles: vi.fn(async () => [] as FileRecord[]),
  listFileChunks: vi.fn(async () => []),
  listDocumentRevisions: vi.fn(async () => []),
  listDocumentConceptBranches: vi.fn(async () => []),
}));
vi.mock("@/api/goals", () => ({ listGoals: vi.fn(async () => []) }));
vi.mock("@/api/providers", () => ({ listProviders: vi.fn(async () => []) }));
vi.mock("@/hooks/use-file-stream-url", () => ({
  useFileStreamUrl: () => ({ source: "blob:preview", failed: false, loading: false }),
}));
// 对话区有自己的测试；这里只验证工作台按能力装配了哪些区域。
vi.mock("./document-chat-panel", () => ({
  DocumentChatPanel: ({ suggestions }: { suggestions?: string[] }) => (
    <div data-testid="document-chat" data-suggestions={suggestions?.join("|") ?? ""} />
  ),
}));

const imageFile: FileRecord = {
  id: "file-image",
  workspace_id: "ws",
  original_name: "image (44).png",
  created_at: new Date(Date.UTC(2026, 10, 25, 7, 7)).toISOString(),
  mime_type: "image/png",
  size_bytes: 232_346,
  sha256: "x",
  storage_status: "stored",
  parse_status: "pending",
  parse_capability: "attachment_only",
  parser_name: null,
  parser_version: null,
};

function renderLearningPage() {
  return renderWithProviders(
    <Routes>
      <Route element={<DocumentLearningPage />} path="/w/:workspaceId/documents/:fileId" />
    </Routes>,
    { initialEntries: ["/w/ws/documents/file-image"] },
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("document learning workbench", () => {
  it("keeps document-only concepts out of an image workbench", async () => {
    vi.mocked(listFiles).mockResolvedValue([imageFile]);
    vi.mocked(listFileChunks).mockResolvedValue([]);
    vi.mocked(listDocumentRevisions).mockResolvedValue([]);
    vi.mocked(listDocumentConceptBranches).mockResolvedValue([]);
    vi.mocked(listGoals).mockResolvedValue([]);
    vi.mocked(listProviders).mockResolvedValue([]);

    renderLearningPage();

    expect(await screen.findByRole("heading", { name: "image (44).png" })).toBeInTheDocument();
    // 图片没有文档版本、章节目录和定位，相关入口一律不渲染。
    expect(screen.queryByText(/尚未建立文档版本/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /显示目录/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /定位/ })).not.toBeInTheDocument();
    // 也不再把「发送给模型解析」和 Provider 技术说明摆在主界面上。
    expect(screen.queryByRole("button", { name: /发送给模型解析/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/识图 Provider/)).not.toBeInTheDocument();
    // 图片查看区提供轻量查看工具，对话区拿到针对图片的提问建议。
    expect(await screen.findByRole("toolbar", { name: "图片查看工具" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "适应窗口" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /图片/ })).toBeInTheDocument();
    expect(screen.getByTestId("document-chat")).toHaveAttribute("data-suggestions", "解释图中内容|分析图中的结构与关系|提取图中的文字");
  });
});
