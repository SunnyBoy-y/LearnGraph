import { useEffect, useState } from "react";
import { CircleAlert, Download, LoaderCircle, X } from "lucide-react";

import { downloadFile } from "@/api/files";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import type { MessagePart } from "@/types/sessions";

/**
 * True when a sandbox part is a published image file artifact. Only these get
 * the inline embedded preview treatment; every other sandbox output keeps the
 * card-style artifact renderer (预览 / 下载 buttons).
 */
export function isSandboxImageArtifactPart(part: MessagePart): boolean {
  const data = part.data;
  if (!data || typeof data !== "object") return false;
  const isFileArtifact =
    part.type === "sandbox_artifact" ||
    (part.type === "sandbox" &&
      (data.kind === "file" || typeof data.file_id === "string"));
  if (!isFileArtifact) return false;
  const mime = typeof data.mime_type === "string" ? data.mime_type : "";
  return mime.trim().toLowerCase().startsWith("image/");
}

function formatSize(sizeBytes: unknown): string {
  if (typeof sizeBytes !== "number" || !Number.isFinite(sizeBytes)) return "";
  if (sizeBytes < 1024) return `${Math.max(0, Math.round(sizeBytes))} B`;
  if (sizeBytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
  }
  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
}

export type SandboxImageLightboxState = {
  index: number;
  source: string;
  title: string;
};

function SandboxImageLightbox({
  onDownload,
  onOpenChange,
  open,
  source,
  title,
}: {
  onDownload: () => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  source: string;
  title: string;
}) {
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent
        aria-describedby={undefined}
        className="chat-image-lightbox"
        showCloseButton={false}
      >
        <DialogTitle className="sr-only">查看图片产物</DialogTitle>
        <img alt={title} className="chat-image-lightbox__image" src={source} />
        <div className="chat-image-lightbox__toolbar">
          <button onClick={onDownload} type="button">
            <Download className="size-4" />
            下载图片
          </button>
          <DialogClose asChild>
            <button type="button">
              <X className="size-4" />
              关闭
            </button>
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function SandboxImageArtifact({
  index = 0,
  onOpenLightbox,
  part,
}: {
  index?: number;
  onOpenLightbox?: (state: SandboxImageLightboxState) => void;
  part: MessagePart;
}) {
  const data = part.data ?? {};
  const fileId = typeof data.file_id === "string" ? data.file_id : "";
  const title =
    typeof data.title === "string" && data.title.trim() ? data.title : "图片产物";
  const path = typeof data.path === "string" ? data.path : "";
  const size = formatSize(data.size_bytes);
  const [source, setSource] = useState("");
  const [loading, setLoading] = useState(Boolean(fileId));
  const [failed, setFailed] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);

  useEffect(() => {
    if (!fileId) {
      setSource("");
      setLoading(false);
      setFailed(true);
      return;
    }
    let cancelled = false;
    let objectUrl = "";
    setSource("");
    setLoading(true);
    setFailed(false);
    void downloadFile(fileId)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [fileId, part.id]);

  const download = () => {
    if (!source) return;
    const anchor = document.createElement("a");
    anchor.href = source;
    anchor.download = title;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  };

  const openZoom = () => {
    if (!source) return;
    if (onOpenLightbox) {
      onOpenLightbox({ index, source, title });
    } else {
      setLightboxOpen(true);
    }
  };

  const label = path || title;

  return (
    <figure className="sandbox-image-strip__item" aria-label={label}>
      <button
        aria-label={`放大查看 ${label}`}
        className="sandbox-image-strip__thumb-button"
        disabled={!source}
        onClick={openZoom}
        title={label}
        type="button"
      >
        {loading ? (
          <span className="sandbox-image-strip__state" role="status">
            <LoaderCircle className="size-4 animate-spin" />
            加载中
          </span>
        ) : failed || !source ? (
          <span className="sandbox-image-strip__state" role="alert">
            <CircleAlert className="size-4" />
            预览不可用
          </span>
        ) : (
          <img
            alt={title}
            className="sandbox-image-strip__thumb"
            src={source}
          />
        )}
      </button>
      <figcaption className="sandbox-image-strip__caption">
        <span title={label}>{label}</span>
        {size ? <span className="sandbox-image-strip__size">{size}</span> : null}
        {source ? (
          <button
            aria-label={`下载 ${label}`}
            onClick={download}
            title="下载"
            type="button"
          >
            <Download className="size-3.5" />
          </button>
        ) : null}
      </figcaption>
      {!onOpenLightbox ? (
        <SandboxImageLightbox
          onDownload={download}
          onOpenChange={setLightboxOpen}
          open={lightboxOpen}
          source={source}
          title={title}
        />
      ) : null}
    </figure>
  );
}

/**
 * Inline embedded preview for adjacent sandbox image artifacts.
 *
 * Images sit side by side; when the row overflows the message width a
 * horizontal scrollbar appears. Clicking any preview opens the shared zoom
 * lightbox. Only strictly adjacent image parts are grouped together — text or
 * cards in between split the stream into separate strips.
 */
export function SandboxImageStrip({ parts }: { parts: MessagePart[] }) {
  const [lightbox, setLightbox] = useState<SandboxImageLightboxState | null>(
    null,
  );

  const downloadLightbox = () => {
    if (!lightbox) return;
    const anchor = document.createElement("a");
    anchor.href = lightbox.source;
    anchor.download = lightbox.title;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  };

  return (
    <section
      aria-label={`${parts.length} 张沙箱图片产物`}
      className="sandbox-image-strip"
    >
      {parts.map((part, index) => (
        <SandboxImageArtifact
          index={index}
          key={part.id}
          onOpenLightbox={setLightbox}
          part={part}
        />
      ))}
      {lightbox ? (
        <SandboxImageLightbox
          onDownload={downloadLightbox}
          onOpenChange={(open) => {
            if (!open) setLightbox(null);
          }}
          open
          source={lightbox.source}
          title={lightbox.title}
        />
      ) : null}
    </section>
  );
}
