import { useQuery } from "@tanstack/react-query";
import {
  Download,
  ExternalLink,
  FileArchive,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  Image as ImageIcon,
  LoaderCircle,
  SquareCheck,
  Video,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { fileStreamUrl, listSessionFiles } from "@/api/files";
import { useFileStreamUrl } from "@/hooks/use-file-stream-url";
import { FilePreviewDialog } from "@/components/resources/file-preview-dialog";
import { ClearableSearchInput } from "@/components/shared/clearable-search-input";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { documentHref } from "@/lib/document-citations";
import { useListSelection } from "@/lib/list-selection";
import { downloadViaNative, toAbsoluteApiUrl } from "@/lib/native-download";
import { toast } from "@/lib/notifications";
import type { SessionFile } from "@/types/files";

const ORIGIN_LABELS: Record<string, string> = {
  user_attachment: "用户附件",
  generated_image: "生成的图",
  external_download: "外部下载",
  agent_workspace_file: "Agent 写入",
  session_workspace: "会话工作区",
};

type BadgeVariant = "link" | "default" | "secondary" | "destructive" | "outline" | "ghost";

const ORIGIN_TONES: Record<string, BadgeVariant> = {
  user_attachment: "secondary",
  generated_image: "default",
  external_download: "outline",
  agent_workspace_file: "secondary",
  session_workspace: "outline",
};

function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

function fileKindLabel(file: SessionFile): {
  Icon: typeof FileText;
  label: string;
} {
  const name = file.filename.toLowerCase();
  const mime = (file.mime_type ?? "").toLowerCase();
  if (mime.startsWith("image/")) return { Icon: ImageIcon, label: "图片" };
  if (mime.startsWith("video/")) return { Icon: Video, label: "视频" };
  if (["xls", "xlsx", "csv", "tsv"].some((ext) => name.endsWith(ext)))
    return { Icon: FileSpreadsheet, label: "表格" };
  if (["zip", "rar", "7z", "tar", "gz"].some((ext) => name.endsWith(ext)))
    return { Icon: FileArchive, label: "压缩包" };
  if (["pdf", "doc", "docx", "md", "txt", "rtf"].some((ext) => name.endsWith(ext)))
    return { Icon: FileText, label: "文档" };
  return { Icon: FileText, label: "文件" };
}

function SessionFileThumb({ file }: { file: SessionFile }) {
  const isImage =
    (file.is_image || (file.mime_type ?? "").toLowerCase().startsWith("image/")) &&
    Boolean(file.file_id);
  const { Icon, label } = fileKindLabel(file);

  const { source: src, failed } = useFileStreamUrl(isImage ? file.file_id ?? undefined : undefined);

  if (isImage && src) {
    return (
      <img
        alt={file.filename}
        className="size-8 flex-none rounded-md border object-cover"
        src={src}
      />
    );
  }
  if (isImage && !failed) {
    return (
      <span className="grid size-8 flex-none place-items-center rounded-md border bg-muted">
        <ImageIcon className="size-4 text-muted-foreground" />
      </span>
    );
  }
  return (
    <span className="grid size-8 flex-none place-items-center rounded-md border bg-muted">
      <Icon aria-label={label} className="size-4 text-muted-foreground" />
    </span>
  );
}

/** 会话关联文件内容列表：供对话页图谱书架（rail）第三个选项使用。 */
export function SessionFilesList({
  workspaceId,
  sessionId,
}: {
  workspaceId: string;
  sessionId?: string;
}) {
  const navigate = useNavigate();
  const enabled = Boolean(sessionId && sessionId !== "new");
  const files = useQuery({
    queryKey: ["workspaces", workspaceId, "sessions", sessionId, "files"],
    queryFn: () => listSessionFiles(sessionId!),
    enabled,
    staleTime: 10_000,
  });

  const list = files.data ?? [];
  const [previewFile, setPreviewFile] = useState<SessionFile | null>(null);
  const [search, setSearch] = useState("");
  const [downloadingBatch, setDownloadingBatch] = useState(false);

  const fileKey = (file: SessionFile) => file.file_id ?? file.path ?? file.filename;

  const filteredList = useMemo(() => {
    const list = files.data ?? [];
    const q = search.trim().toLocaleLowerCase();
    if (!q) return list;
    return list.filter((file) =>
      `${file.filename} ${file.path ?? ""}`.toLocaleLowerCase().includes(q),
    );
  }, [files.data, search]);

  const visibleKeys = useMemo(() => filteredList.map(fileKey), [filteredList]);
  const selection = useListSelection(visibleKeys);
  const selectedFiles = useMemo(
    () => (files.data ?? []).filter((file) => selection.selectedIds.has(fileKey(file))),
    [files.data, selection.selectedIds],
  );
  const downloadableSelected = useMemo(
    () => selectedFiles.filter((file) => Boolean(file.file_id)),
    [selectedFiles],
  );

  async function downloadSingle(file: SessionFile) {
    if (!file.file_id) return;
    if (
      downloadViaNative(
        toAbsoluteApiUrl(`/api/v1/files/${encodeURIComponent(file.file_id)}/content`),
        file.filename,
      )
    ) {
      return;
    }
    const url = await fileStreamUrl(file.file_id);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = file.filename;
    anchor.click();
  }

  async function downloadSelected() {
    if (!downloadableSelected.length || downloadingBatch) return;
    setDownloadingBatch(true);
    let ok = 0;
    try {
      for (const file of downloadableSelected) {
        try {
          await downloadSingle(file);
          ok += 1;
        } catch {
          // continue downloading remaining selected files
        }
      }
      if (ok === downloadableSelected.length) toast.success(`已触发下载 ${ok} 个文件`);
      else if (ok > 0) toast.warning(`已触发下载 ${ok} 个文件，${downloadableSelected.length - ok} 个失败`);
      else toast.error("批量下载失败");
    } finally {
      setDownloadingBatch(false);
    }
  }

  if (!enabled) {
    return (
      <div className="grid place-items-center px-4 py-10 text-center text-xs text-muted-foreground">
        <FolderOpen className="mb-2 size-5 opacity-60" />
        当前没有可展示的会话文件。
      </div>
    );
  }

  return (
    <div
      aria-label="会话文件"
      className="chat-session-files h-full min-h-0 overflow-y-auto"
    >
      {files.isPending ? (
        <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
          <LoaderCircle className="size-3.5 animate-spin" />
          加载中…
        </div>
      ) : files.isError ? (
        <p className="px-4 py-8 text-center text-xs text-destructive" role="alert">
          会话文件加载失败
        </p>
      ) : list.length === 0 ? (
        <div className="px-4 py-8 text-center text-xs text-muted-foreground">
          <ImageIcon className="mx-auto mb-2 size-5 opacity-60" />
          当前会话还没有关联文件。
          <br />
          <span className="opacity-70">
            引用资料、生成图片或让 Agent 下载文件后会显示在这里。
          </span>
        </div>
      ) : (
        <div className="space-y-2 p-2">
          <div className="flex items-center gap-1.5">
            <ClearableSearchInput
              aria-label="搜索会话文件"
              containerClassName="min-w-0 flex-1"
              inputClassName="h-7 text-xs"
              onChange={setSearch}
              placeholder="搜索文件名或路径…"
              value={search}
            />
            <Button
              aria-label={selection.multiSelect ? "退出多选" : "多选会话文件"}
              aria-pressed={selection.multiSelect}
              onClick={selection.toggleMultiSelect}
              size="icon-xs"
              title={selection.multiSelect ? "退出多选 (Esc)" : "多选"}
              type="button"
              variant={selection.multiSelect ? "secondary" : "ghost"}
            >
              <SquareCheck className="size-3.5" />
            </Button>
          </div>

          {selection.multiSelect && filteredList.length > 0 ? (
            <div
              aria-label="会话文件批量操作"
              className="flex flex-wrap items-center gap-1.5 rounded-lg border bg-muted/30 px-2 py-1.5 text-[11px]"
              role="region"
            >
              <label className="flex items-center gap-1.5">
                <Checkbox
                  aria-label="全选当前文件"
                  checked={selection.visibleCheckboxState}
                  disabled={downloadingBatch}
                  onCheckedChange={(checked) => selection.toggleVisible(checked === true)}
                />
                全选
              </label>
              <span className="tabular-nums text-muted-foreground">
                已选 {selection.selectedCount}
              </span>
              <div className="ml-auto flex items-center gap-1">
                <Button
                  disabled={downloadingBatch || !downloadableSelected.length}
                  onClick={() => void downloadSelected()}
                  size="xs"
                  type="button"
                  variant="outline"
                >
                  <Download className="size-3" />
                  下载{downloadableSelected.length ? ` (${downloadableSelected.length})` : ""}
                </Button>
                {selection.selectedCount > 0 ? (
                  <Button disabled={downloadingBatch} onClick={selection.clear} size="xs" type="button" variant="ghost">
                    清空
                  </Button>
                ) : null}
              </div>
            </div>
          ) : null}

          {!filteredList.length ? (
            <p className="py-6 text-center text-xs text-muted-foreground">
              没有匹配「{search.trim()}」的文件。
            </p>
          ) : (
            <ul className="grid gap-1">
              {filteredList.map((file) => {
                const key = fileKey(file);
                const { label } = fileKindLabel(file);
                const originLabel = ORIGIN_LABELS[file.origin] ?? file.origin;
                const href =
                  file.file_id && workspaceId
                    ? documentHref(workspaceId, file.file_id)
                    : "";
                const selected = selection.multiSelect && selection.selectedIds.has(key);
                return (
                  <li key={key}>
                    <div
                      className={`group flex cursor-pointer items-center gap-2 rounded-lg border p-2 transition-colors hover:bg-muted/60 ${selected ? "border-ring bg-primary/5" : ""}`}
                      onClick={(event) => {
                        if (selection.multiSelect) {
                          selection.toggleItem(key, { shiftKey: event.shiftKey });
                          return;
                        }
                        setPreviewFile(file);
                      }}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          if (selection.multiSelect) selection.toggleItem(key);
                          else setPreviewFile(file);
                        }
                      }}
                      role="button"
                      tabIndex={0}
                      title={selection.multiSelect ? `勾选 ${file.filename}` : `预览 ${file.filename}`}
                    >
                      {selection.multiSelect ? (
                        <span onClick={(event) => event.stopPropagation()}>
                          <Checkbox
                            aria-label={`选择文件 ${file.filename}`}
                            disabled={downloadingBatch}
                            {...selection.checkboxProps(key)}
                          />
                        </span>
                      ) : null}
                      <SessionFileThumb file={file} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-xs font-medium" title={file.filename}>
                          {file.filename}
                        </p>
                        <p className="mt-0.5 flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
                          <span>{formatFileSize(file.size_bytes)}</span>
                          <span>·</span>
                          <span>{label}</span>
                          {file.path ? (
                            <span className="truncate font-mono" title={file.path}>
                              {file.path}
                            </span>
                          ) : null}
                        </p>
                        <div className="mt-1 flex items-center gap-1">
                          <Badge
                            variant={ORIGIN_TONES[file.origin] ?? "outline"}
                            className="px-1.5 py-0 text-[9px] font-normal"
                          >
                            {originLabel}
                          </Badge>
                        </div>
                      </div>
                      <div className="flex flex-none flex-col gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100">
                        {file.file_id ? (
                          <>
                            <Button
                              aria-label={`打开 ${file.filename}`}
                              className="size-6"
                              onClick={(event) => {
                                event.stopPropagation();
                                if (href) navigate(href);
                              }}
                              size="icon-xs"
                              type="button"
                              variant="ghost"
                            >
                              <ExternalLink className="size-3.5" />
                            </Button>
                            <Button
                              aria-label={`下载 ${file.filename}`}
                              className="size-6"
                              onClick={(event) => {
                                event.stopPropagation();
                                void downloadSingle(file);
                              }}
                              size="icon-xs"
                              type="button"
                              variant="ghost"
                            >
                              <Download className="size-3.5" />
                            </Button>
                          </>
                        ) : null}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
      <SessionFilePreviewDialog
        file={previewFile}
        onOpenChange={(open) => {
          if (!open) setPreviewFile(null);
        }}
      />
    </div>
  );
}

/**
 * 二级窗口预览：图片走灯箱式大图，文档类复用 FilePreviewCanvas 内置查看器
 * （pdf/word/ppt/xlsx/音频/视频/html/文本）。仅工作区条目（无 file_id）只能
 * 展示元信息，无法拉取内容。
 */
function SessionFilePreviewDialog({
  file,
  onOpenChange,
}: {
  file: SessionFile | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <FilePreviewDialog
      fileId={file?.file_id ?? undefined}
      fileSize={file?.size_bytes}
      filename={file?.filename ?? "文件预览"}
      mimeType={file?.mime_type}
      open={Boolean(file)}
      onOpenChange={onOpenChange}
      description={file ? `${ORIGIN_LABELS[file.origin] ?? file.origin} · ${formatFileSize(file.size_bytes)}${file.file_id ? "" : " · 仅存在于会话工作区，暂无可读取内容"}` : undefined}
    />
  );
}
