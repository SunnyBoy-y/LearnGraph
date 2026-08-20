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
  Video,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { downloadFile, listSessionFiles } from "@/api/files";
import { FilePreviewCanvas } from "@/components/resources/file-preview";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { documentHref } from "@/lib/document-citations";
import { resolveFilePreviewKind } from "@/lib/file-preview";
import { downloadViaNative, toAbsoluteApiUrl } from "@/lib/native-download";
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
  const [src, setSrc] = useState("");
  const [failed, setFailed] = useState(false);
  const isImage =
    (file.is_image || (file.mime_type ?? "").toLowerCase().startsWith("image/")) &&
    Boolean(file.file_id);
  const { Icon, label } = fileKindLabel(file);

  useEffect(() => {
    if (!isImage || !file.file_id) return;
    let objectUrl = "";
    let cancelled = false;
    void downloadFile(file.file_id)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [file.file_id, isImage]);

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
        <ul className="grid gap-1 p-2">
          {list.map((file) => {
            const { label } = fileKindLabel(file);
            const originLabel = ORIGIN_LABELS[file.origin] ?? file.origin;
            const href =
              file.file_id && workspaceId
                ? documentHref(workspaceId, file.file_id)
                : "";
            return (
              <li key={file.file_id ?? file.path ?? file.filename}>
                <div
                  className="group flex cursor-pointer items-center gap-2 rounded-lg border p-2 transition-colors hover:bg-muted/60"
                  onClick={() => setPreviewFile(file)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setPreviewFile(file);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                  title={`预览 ${file.filename}`}
                >
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
                  <div className="flex flex-none flex-col gap-1 opacity-0 transition-opacity group-hover:opacity-100">
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
                            if (
                              file.file_id &&
                              downloadViaNative(
                                toAbsoluteApiUrl(`/api/v1/files/${encodeURIComponent(file.file_id)}/content`),
                                file.filename,
                              )
                            ) {
                              return;
                            }
                            void downloadFile(file.file_id!).then((blob) => {
                              const url = URL.createObjectURL(blob);
                              const anchor = document.createElement("a");
                              anchor.href = url;
                              anchor.download = file.filename;
                              anchor.click();
                              URL.revokeObjectURL(url);
                            });
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
  const [blob, setBlob] = useState<Blob | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const open = Boolean(file);
  const isImage =
    Boolean(file) &&
    (file!.is_image || (file!.mime_type ?? "").toLowerCase().startsWith("image/"));
  const previewKind = file
    ? resolveFilePreviewKind(file.filename, file.mime_type)
    : "unsupported";
  const canPreview = Boolean(file?.file_id && !isImage && previewKind !== "unsupported");

  useEffect(() => {
    if (!open || !file?.file_id || isImage || !canPreview || blob) return;
    let cancelled = false;
    setLoading(true);
    setFailed(false);
    void downloadFile(file.file_id)
      .then((next) => {
        if (!cancelled) setBlob(next);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [blob, canPreview, file?.file_id, isImage, open]);

  // 图片预览：单独拉取 blob 显示大图。
  const [imageSrc, setImageSrc] = useState("");
  useEffect(() => {
    if (!open || !file?.file_id || !isImage) return;
    let objectUrl = "";
    let cancelled = false;
    setImageSrc("");
    setLoading(true);
    setFailed(false);
    void downloadFile(file.file_id)
      .then((next) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(next);
        setImageSrc(objectUrl);
      })
      .catch(() => setFailed(true))
      .finally(() => setLoading(false));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [file?.file_id, isImage, open]);

  const filename = file?.filename ?? "";
  const originLabel = file ? (ORIGIN_LABELS[file.origin] ?? file.origin) : "";

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent
        className="flex max-h-[min(92svh,58rem)] w-full max-w-[calc(100%-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-5xl"
        showCloseButton
      >
        {file ? (
          <>
            <DialogHeader className="shrink-0 border-b px-5 py-4 pr-12">
              <DialogTitle className="truncate">{filename}</DialogTitle>
              <DialogDescription className="truncate">
                {originLabel}
                {file.path ? ` · ${file.path}` : ""} · {formatFileSize(file.size_bytes)}
              </DialogDescription>
            </DialogHeader>
            <div className="min-h-0 flex-1 overflow-auto bg-muted/15">
              {isImage ? (
                loading ? (
                  <div className="grid min-h-[24rem] place-items-center gap-2 text-sm text-muted-foreground" role="status">
                    <LoaderCircle className="size-4 animate-spin" />
                    正在加载图片…
                  </div>
                ) : imageSrc ? (
                  <div className="grid min-h-[24rem] place-items-center p-6">
                    <img
                      alt={filename}
                      className="max-h-[72svh] w-auto max-w-full rounded-lg object-contain shadow-lg"
                      src={imageSrc}
                    />
                  </div>
                ) : (
                  <div className="grid min-h-[24rem] place-items-center p-8 text-sm text-destructive" role="alert">
                    图片加载失败，请下载后查看。
                  </div>
                )
              ) : canPreview ? (
                loading ? (
                  <div className="grid min-h-[24rem] place-items-center gap-2 text-sm text-muted-foreground" role="status">
                    <LoaderCircle className="size-4 animate-spin" />
                    正在加载预览…
                  </div>
                ) : failed ? (
                  <div className="grid min-h-[24rem] place-items-center p-8 text-sm text-destructive" role="alert">
                    预览加载失败，请下载后使用本地应用查看。
                  </div>
                ) : blob ? (
                  <FilePreviewCanvas
                    blob={blob}
                    className="min-h-[32rem]"
                    filename={filename}
                    mimeType={file.mime_type}
                  />
                ) : null
              ) : (
                <div className="grid min-h-[24rem] place-items-center gap-3 p-8 text-center text-sm text-muted-foreground">
                  <FolderOpen className="size-8 opacity-50" />
                  <div>
                    {file.file_id
                      ? "该类型暂不支持浏览器内预览；请下载后使用本地应用查看。"
                      : "该文件仅存在于会话工作区（无独立下载文件），无法在浏览器中预览。"}
                  </div>
                  {file.file_id ? (
                    <Button
                      onClick={() => {
                        if (
                          file.file_id &&
                          downloadViaNative(
                            toAbsoluteApiUrl(`/api/v1/files/${encodeURIComponent(file.file_id)}/content`),
                            filename,
                          )
                        ) {
                          return;
                        }
                        void downloadFile(file.file_id!).then((next) => {
                          const url = URL.createObjectURL(next);
                          const anchor = document.createElement("a");
                          anchor.href = url;
                          anchor.download = filename;
                          anchor.click();
                          URL.revokeObjectURL(url);
                        });
                      }}
                      size="sm"
                      type="button"
                      variant="outline"
                    >
                      <Download className="size-4" />
                      下载
                    </Button>
                  ) : null}
                </div>
              )}
            </div>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
