import { useEffect, useState } from "react";
import {
  Download,
  Eye,
  FileCode2,
  LoaderCircle,
  Maximize2,
  Minimize2,
  ShieldCheck,
} from "lucide-react";

import { downloadFile } from "@/api/files";
import {
  FilePreviewCanvas,
} from "@/components/resources/file-preview";
import { resolveFilePreviewKind } from "@/lib/file-preview";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

function formatSize(sizeBytes: unknown): string {
  if (typeof sizeBytes !== "number" || !Number.isFinite(sizeBytes)) return "";
  if (sizeBytes < 1024) return `${Math.max(0, Math.round(sizeBytes))} B`;
  if (sizeBytes < 1024 * 1024) return `${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
}

export function SandboxFileArtifact({ data }: { data: Record<string, unknown> }) {
  const [downloading, setDownloading] = useState(false);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [previewBlob, setPreviewBlob] = useState<Blob>();

  const title =
    typeof data.title === "string"
      ? data.title
      : typeof data.path === "string"
        ? data.path
        : "沙箱产物";
  const fileId = typeof data.file_id === "string" ? data.file_id : "";
  const size = formatSize(data.size_bytes);
  const mime = typeof data.mime_type === "string" ? data.mime_type : "";
  const path = typeof data.path === "string" ? data.path : "";
  const sha =
    typeof data.sha256 === "string"
      ? data.sha256
      : typeof data.blob_sha256 === "string"
        ? data.blob_sha256
        : "";
  const previewKind = resolveFilePreviewKind(path || title, mime);
  const canPreview = Boolean(fileId) && previewKind !== "unsupported";

  useEffect(() => {
    if (!viewerOpen || !fileId || !canPreview || previewBlob) return;
    let cancelled = false;
    setLoadingPreview(true);
    setPreviewError("");
    void downloadFile(fileId)
      .then((blob) => {
        if (!cancelled) setPreviewBlob(blob);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setPreviewError(
            reason instanceof Error
              ? reason.message
              : "无法加载文件内容，请稍后重试或直接下载。",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingPreview(false);
      });
    return () => {
      cancelled = true;
    };
  }, [canPreview, fileId, previewBlob, viewerOpen]);

  useEffect(() => {
    if (!viewerOpen) setIsFullscreen(false);
  }, [viewerOpen]);

  useEffect(() => {
    if (!viewerOpen || !isFullscreen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      setIsFullscreen(false);
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [isFullscreen, viewerOpen]);

  async function onDownload() {
    if (!fileId || downloading) return;
    setDownloading(true);
    try {
      const blob = await downloadFile(fileId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = title;
      anchor.click();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <>
      <section className="rounded-xl border bg-card p-4" aria-label={title}>
        <div className="flex items-start gap-3">
          <span className="mt-0.5 rounded-lg border p-2 text-primary">
            <FileCode2 className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <strong className="truncate">{title}</strong>
              <Badge variant="secondary">沙箱产物</Badge>
            </div>
            <p className="mt-1 truncate text-xs text-muted-foreground" title={`${path} ${mime}`}>
              {path || "outputs/*"}
              {size ? ` · ${size}` : ""}
              {mime ? ` · ${mime}` : ""}
            </p>
            {sha ? (
              <p className="mt-1 flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
                <ShieldCheck className="size-3" />
                {sha.slice(0, 16)}…
              </p>
            ) : null}
          </div>
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
            {canPreview ? (
              <Button aria-label={`预览 ${title}`} onClick={() => setViewerOpen(true)} size="sm" variant="outline">
                <Eye className="size-4" />
                预览
              </Button>
            ) : null}
            <Button aria-label={`下载 ${title}`} disabled={!fileId || downloading} onClick={() => void onDownload()} size="sm">
              {downloading ? <LoaderCircle className="size-4 animate-spin" /> : <Download className="size-4" />}
              下载
            </Button>
          </div>
        </div>
      </section>

      <Dialog open={viewerOpen} onOpenChange={setViewerOpen}>
        <DialogContent
          className={cn(
            "flex max-h-[min(92svh,58rem)] w-full max-w-[calc(100%-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-5xl",
            isFullscreen && "top-0 left-0 h-svh max-h-svh w-screen max-w-none translate-x-0 translate-y-0 rounded-none sm:max-w-none",
          )}
          showCloseButton
        >
          <Button
            aria-label={isFullscreen ? "退出全屏" : "全屏预览"}
            className="absolute top-2 right-12 z-30"
            onClick={() => setIsFullscreen((value) => !value)}
            size="icon-sm"
            type="button"
            variant="ghost"
          >
            {isFullscreen ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
          </Button>
          <DialogHeader className="border-b px-5 py-4 pr-24">
            <DialogTitle className="truncate pr-2">{title}</DialogTitle>
            <DialogDescription className="truncate">
              {path || "outputs/*"}{size ? ` · ${size}` : ""}{mime ? ` · ${mime}` : ""}
            </DialogDescription>
          </DialogHeader>
          <div className="min-h-0 flex-1 overflow-auto bg-muted/15">
            {loadingPreview ? (
              <div className="flex min-h-[32rem] items-center justify-center gap-2 text-sm text-muted-foreground" role="status">
                <LoaderCircle className="size-4 animate-spin" />
                正在加载预览…
              </div>
            ) : previewError ? (
              <div className="grid min-h-[24rem] place-items-center p-8 text-sm text-destructive" role="alert">{previewError}</div>
            ) : previewBlob ? (
              <FilePreviewCanvas
                blob={previewBlob}
                className={isFullscreen ? "min-h-[calc(100svh-8rem)]" : "min-h-[32rem]"}
                filename={path || title}
                mimeType={mime || previewBlob.type}
              />
            ) : null}
          </div>
          <div className="flex flex-col-reverse gap-2 border-t bg-background px-5 py-3 sm:flex-row sm:justify-end">
            <Button disabled={!fileId || downloading} onClick={() => void onDownload()} size="sm" variant="outline">
              {downloading ? <LoaderCircle className="size-4 animate-spin" /> : <Download className="size-4" />}
              下载
            </Button>
            <Button onClick={() => setViewerOpen(false)} size="sm">关闭</Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
