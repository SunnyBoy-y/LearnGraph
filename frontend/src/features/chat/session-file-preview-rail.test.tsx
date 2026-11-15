import "@/test/pdfjs-polyfills";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { listSessionFiles } from "@/api";
import { renderWithProviders } from "@/test/render";
import type { SessionFile } from "@/types/files";
import { SessionFilePreviewRail } from "./right-rail-panels";
import { sessionFilePath } from "@/lib/session-files";

vi.mock("@/api", () => ({ listSessionFiles: vi.fn(), fileStreamUrl: vi.fn() }));
vi.mock("@/components/resources/file-preview-content", () => ({
  FilePreviewContent: ({ filename, fileId }: { filename: string; fileId: string }) => <div data-testid="file-preview" data-file-id={fileId}>{filename} 内容</div>,
}));

function file(filename: string, path: string | null = null, fileId: string | null = filename): SessionFile {
  return { file_id: fileId, filename, path, mime_type: "text/plain", size_bytes: 120, origin: "session_workspace", relation: null, source: null, message_id: null, is_image: false, storage_status: "stored", prompt_summary: null, created_at: null };
}
const fixtures = [file("index.html", "dist/index.html"), file("index.html", "src/index.html", "source-index"), file("package.json"), file("shot.mjs", "tools/shot.mjs", null)];

beforeEach(() => { vi.mocked(listSessionFiles).mockResolvedValue(fixtures); });

describe("session file directory and preview", () => {
  it("starts with a directory, opens a full-rail preview and returns to the same expanded folder", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SessionFilePreviewRail workspaceId="ws" sessionId="one" />);
    await screen.findByRole("navigation", { name: "文件目录" });
    expect(screen.queryByTestId("file-preview")).not.toBeInTheDocument();
    const folder = screen.getByText("dist").closest("details")!;
    expect(folder).not.toHaveAttribute("open");
    await user.click(within(folder).getByText("dist"));
    expect(folder).toHaveAttribute("open");
    const entry = screen.getByRole("button", { name: "预览 dist/index.html" });
    await user.click(entry);
    expect(await screen.findByTestId("file-preview")).toHaveAttribute("data-file-id", "index.html");
    expect(screen.getByLabelText("文件列表")).not.toBeVisible();
    expect(screen.queryByRole("button", { name: "刷新会话文件" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "关闭预览，返回文件列表" }));
    expect(screen.queryByTestId("file-preview")).not.toBeInTheDocument();
    expect(folder).toHaveAttribute("open");
    await waitFor(() => expect(entry).toHaveFocus());
    await user.click(screen.getByText("src"));
    await user.click(screen.getByRole("button", { name: "预览 src/index.html" }));
    expect(screen.getByTestId("file-preview")).toHaveAttribute("data-file-id", "source-index");
    fireEvent.keyDown(screen.getByRole("button", { name: "关闭预览，返回文件列表" }), { key: "Escape" });
    expect(screen.queryByTestId("file-preview")).not.toBeInTheDocument();
  });

  it("keeps a closed preview closed when files refresh", async () => {
    const user = userEvent.setup();
    const { queryClient } = renderWithProviders(<SessionFilePreviewRail workspaceId="ws" sessionId="one" />);
    await user.click(await screen.findByRole("button", { name: "预览 package.json" }));
    await user.click(screen.getByRole("button", { name: "关闭预览，返回文件列表" }));
    await user.click(screen.getByRole("button", { name: "刷新会话文件" }));
    await waitFor(() => expect(listSessionFiles).toHaveBeenCalledTimes(2));
    await act(async () => { queryClient.setQueryData(["workspaces", "ws", "sessions", "one", "files"], [...fixtures, file("new.txt")]); });
    expect(screen.queryByTestId("file-preview")).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "预览 new.txt" })).toBeVisible();
  });

  it("does not carry a selected file into another session", async () => {
    const user = userEvent.setup();
    const view = renderWithProviders(<SessionFilePreviewRail workspaceId="ws" sessionId="one" />);
    await user.click(await screen.findByRole("button", { name: "预览 package.json" }));
    view.rerender(<SessionFilePreviewRail workspaceId="ws" sessionId="two" />);
    await screen.findByRole("navigation", { name: "文件目录" });
    expect(screen.queryByTestId("file-preview")).not.toBeInTheDocument();
  });

  it("returns to the list if a selected file disappears", async () => {
    const user = userEvent.setup();
    const { queryClient } = renderWithProviders(<SessionFilePreviewRail workspaceId="ws" sessionId="one" />);
    await user.click(await screen.findByRole("button", { name: "预览 package.json" }));
    await act(async () => { queryClient.setQueryData(["workspaces", "ws", "sessions", "one", "files"], [file("new.txt")]); });
    await waitFor(() => expect(screen.queryByTestId("file-preview")).not.toBeInTheDocument());
    expect(await screen.findByRole("button", { name: "预览 new.txt" })).toBeVisible();
  });

  it("allows closing workspace-only entries and disables unavailable downloads", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SessionFilePreviewRail workspaceId="ws" sessionId="one" />);
    await user.click(await screen.findByText("tools"));
    await user.click(screen.getByRole("button", { name: "预览 tools/shot.mjs" }));
    expect(screen.getByRole("button", { name: "下载文件" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "关闭预览，返回文件列表" }));
    expect(screen.getByRole("navigation", { name: "文件目录" })).toBeVisible();
  });

  it("keeps a cached preview closable after a background refresh fails", async () => {
    const user = userEvent.setup();
    const { queryClient } = renderWithProviders(<SessionFilePreviewRail workspaceId="ws" sessionId="one" />);
    await user.click(await screen.findByRole("button", { name: "预览 package.json" }));
    vi.mocked(listSessionFiles).mockRejectedValueOnce(new Error("offline"));
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ["workspaces", "ws", "sessions", "one", "files"] }); });
    expect(screen.getByTestId("file-preview")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "关闭预览，返回文件列表" }));
    expect(screen.getByRole("navigation", { name: "文件目录" })).toBeVisible();
  });

  it("does not fetch files for a new session", () => {
    renderWithProviders(<SessionFilePreviewRail workspaceId="ws" sessionId="new" />);
    expect(screen.getByText("新会话还没有文件")).toBeVisible();
    expect(listSessionFiles).not.toHaveBeenCalled();
  });

  it("normalizes directory separators without inventing folders for attachments", () => {
    expect(sessionFilePath(file("a.txt", "src\\nested\\a.txt"))).toBe("src/nested/a.txt");
    expect(sessionFilePath(file("a.txt", "./src//a.txt"))).toBe("src/a.txt");
    expect(sessionFilePath(file("a.txt"))).toBe("a.txt");
  });
});
