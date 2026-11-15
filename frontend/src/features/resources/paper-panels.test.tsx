import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import { ApiError } from "@/api/client";
import { readPaper, savePaper, searchPapers } from "@/api/papers";
import { PaperSearchPanel, PaperSavedBody } from "./paper-panels";
import type { Paper, PaperReadResponse } from "@/types/papers";
import type { SourceRecord } from "@/types/workflow";

vi.mock("@/api/papers", () => ({
  searchPapers: vi.fn(), readPaper: vi.fn(), savePaper: vi.fn(), getPaperBibtex: vi.fn(),
}));
vi.mock("@/components/resources/source-association-dialog", () => ({
  SourceAssociationDialog: ({ source }: { source: SourceRecord }) => <div role="dialog">关联来源 {source.id}</div>,
}));

const paper: Paper = {
  provider: "arxiv", paper_id: "2401.12345v2", title: "A real paper", authors: ["Test Author"], year: 2024,
  published_at: "2024-01-22", venue: "", doi: null, url: "https://arxiv.org/abs/2401.12345v2", abstract: "An abstract, not the body",
  pdf_url: "https://arxiv.org/pdf/2401.12345v2", open_access: true, publication_type: "preprint", cited_by_count: null,
  fetched_at: "2026-11-12T00:00:00Z", evidence_kind: "abstract", page_read: false,
};
const reading: PaperReadResponse = { paper, url: "https://cdn.example/paper.pdf", content_hash: "a".repeat(64),
  excerpts: [{ locator: "page:3", content: "Exact original page three" }], evidence_kind: "full_text", page_read: true,
  truncated: true, notice: "Only returned excerpts have been read" };
const source: SourceRecord = { id: "source-1", workspace_id: "ws", provider_id: "papers:arxiv", source_url: paper.url,
  final_url: paper.url, title: paper.title, content: paper.abstract, content_hash: "h", content_type: "text/plain",
  authorized_domain: "arxiv.org", cache_status: "abstract", metadata_json: { kind: "paper", page_read: false, evidence_kind: "abstract" },
  created_at: "2026-11-12T00:00:00Z" };

beforeEach(() => {
  vi.mocked(searchPapers).mockResolvedValue({ query: "attention", provider: "arxiv", results: [paper], notice: "Metadata is not body evidence" });
  vi.mocked(readPaper).mockResolvedValue(reading);
  vi.mocked(savePaper).mockResolvedValue(source);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

async function search() {
  fireEvent.change(screen.getByLabelText("论文关键词"), { target: { value: "attention" } });
  fireEvent.click(screen.getByRole("button", { name: "搜索论文" }));
  await screen.findByText("A real paper");
}

describe("paper discovery, reading and collection", () => {
  it("keeps discovery, reading and saving as separate explicit actions", async () => {
    renderWithProviders(<PaperSearchPanel workspaceId="ws" />);
    await search();
    expect(screen.getByText("仅摘要")).toBeInTheDocument();
    expect(readPaper).not.toHaveBeenCalled();
    expect(savePaper).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "读取公开正文" }));
    expect(await screen.findByText("Exact original page three")).toBeInTheDocument();
    expect(screen.getByText("已读正文片段")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "第 3 页" })).toHaveAttribute("href", "https://cdn.example/paper.pdf#page=3");
    expect(savePaper).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "读取并收藏正文片段" }));
    await waitFor(() => expect(savePaper).toHaveBeenCalledWith({ provider: "arxiv", paper_id: paper.paper_id, include_full_text: true, authorized_domains: ["arxiv.org"] }));
    fireEvent.click(await screen.findByRole("button", { name: "关联知识节点" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("关联来源 source-1");
  });

  it("can save only metadata without downloading a PDF", async () => {
    renderWithProviders(<PaperSearchPanel workspaceId="ws" />);
    await search();
    fireEvent.click(screen.getByRole("button", { name: "收藏论文" }));
    await waitFor(() => expect(savePaper).toHaveBeenCalledWith(expect.objectContaining({ include_full_text: false })));
    expect(readPaper).not.toHaveBeenCalled();
  });

  it("authorizes a redirect for the current read, not an earlier failed save", async () => {
    vi.mocked(savePaper).mockRejectedValueOnce(new Error("Previous save failed"));
    vi.mocked(readPaper).mockRejectedValueOnce(new ApiError({ status: 403, code: "paper_redirect_authorization_required",
      message: "Redirect needs permission", details: { hostname: "cdn.example" }, method: "POST", url: "/papers/read" }));
    renderWithProviders(<PaperSearchPanel workspaceId="ws" />);
    await search();
    fireEvent.click(screen.getByRole("button", { name: "收藏论文" }));
    await screen.findByText("Previous save failed");
    fireEvent.click(screen.getByRole("button", { name: "读取公开正文" }));
    fireEvent.click(await screen.findByRole("button", { name: "授权此域名并重试" }));
    await screen.findByText("Exact original page three");
    expect(readPaper).toHaveBeenLastCalledWith({ provider: "arxiv", paper_id: paper.paper_id, authorized_domains: ["arxiv.org", "cdn.example"] });
    expect(savePaper).toHaveBeenCalledTimes(1);
  });

  it("rejects reversed years without sending a search", () => {
    renderWithProviders(<PaperSearchPanel workspaceId="ws" />);
    fireEvent.change(screen.getByLabelText("论文关键词"), { target: { value: "attention" } });
    fireEvent.change(screen.getByLabelText("起始年份"), { target: { value: "2025" } });
    fireEvent.change(screen.getByLabelText("结束年份"), { target: { value: "2024" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索论文" }));
    expect(screen.getByRole("alert")).toHaveTextContent("起始年份不能晚于结束年份");
    expect(searchPapers).not.toHaveBeenCalled();
  });

  it("preserves persisted locators for saved body citations", () => {
    const saved = { ...source, final_url: reading.url, content: "body", metadata_json: {
      kind: "paper", page_read: true, excerpts: reading.excerpts, document_sha256: reading.content_hash,
    } };
    renderWithProviders(<PaperSavedBody source={saved} />);
    expect(screen.getByText("Exact original page three")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "第 3 页" })).toHaveAttribute("href", "https://cdn.example/paper.pdf#page=3");
  });
});
