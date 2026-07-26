import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CircleAlert,
  Download,
  ImageIcon,
  LoaderCircle,
  Maximize2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { downloadFile } from "@/api/files";
import { MessagePartRenderer } from "@/components/chat/message-part-renderer";
import type { TrustedComponentAction } from "@/components/chat/trusted-component-renderer";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import type { MessagePart } from "@/types/sessions";

function safeImageSource(value: unknown) {
  if (typeof value !== "string") return "";
  if (value.startsWith("data:image/") || value.startsWith("/")) return value;
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol)
      ? parsed.toString()
      : "";
  } catch {
    return "";
  }
}

function positiveNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : undefined;
}

/**
 * Simulated generation progress: image providers report no true percentage,
 * so the bar advances with variable speed toward 99% and only reaches 100%
 * when the final image is actually delivered.
 */
function useSimulatedImageProgress(active: boolean, done: boolean) {
  const [progress, setProgress] = useState(0);
  useEffect(() => {
    if (done) {
      setProgress(100);
      return;
    }
    if (!active) {
      setProgress(0);
      return;
    }
    setProgress((current) => (current > 0 && current < 99 ? current : 2));
    const timer = window.setInterval(() => {
      setProgress((current) => {
        if (current >= 99) return 99;
        const remaining = 99 - current;
        // Ease toward 99 with jitter and occasional bursts so the motion
        // reads as "variable speed" rather than a fixed-rate fill.
        const burst = Math.random() < 0.12 ? remaining * 0.08 : 0;
        const step = Math.max(
          0.15,
          remaining * (0.012 + Math.random() * 0.045) + burst,
        );
        return Math.min(99, current + step);
      });
    }, 200);
    return () => window.clearInterval(timer);
  }, [active, done]);
  return progress;
}

function ChatImagePart({ part }: { part: MessagePart }) {
  const data = part.data;
  const directSource = safeImageSource(
    data?.preview_url ?? data?.src ?? data?.url,
  );
  const fileId = typeof data?.file_id === "string" ? data.file_id : "";
  const revision = String(
    data?.preview_revision ?? data?.revision ?? data?.updated_at ?? "initial",
  );
  const [downloadedSource, setDownloadedSource] = useState("");
  const [loadingRevision, setLoadingRevision] = useState(Boolean(fileId));
  const [downloadFailed, setDownloadFailed] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);

  useEffect(() => {
    if (!fileId) {
      setDownloadedSource("");
      setLoadingRevision(false);
      setDownloadFailed(false);
      return;
    }
    let cancelled = false;
    setLoadingRevision(true);
    setDownloadFailed(false);
    void downloadFile(fileId)
      .then((blob) => {
        if (cancelled) return;
        const nextObjectUrl = URL.createObjectURL(blob);
        setDownloadedSource(nextObjectUrl);
      })
      .catch(() => {
        if (!cancelled) {
          setDownloadedSource("");
          setDownloadFailed(true);
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingRevision(false);
      });
    return () => {
      cancelled = true;
    };
  }, [fileId, revision]);

  useEffect(
    () => () => {
      if (downloadedSource.startsWith("blob:"))
        URL.revokeObjectURL(downloadedSource);
    },
    [downloadedSource],
  );

  const width = positiveNumber(data?.width);
  const height = positiveNumber(data?.height);
  const aspectRatio = width && height ? `${width} / ${height}` : "1 / 1";
  const source = downloadedSource || directSource;
  const title =
    typeof data?.title === "string" ? data.title : "正在生成图片";
  const alt = typeof data?.alt === "string" ? data.alt : title;
  const isWorking =
    (part.status === "pending" ||
      part.status === "streaming" ||
      loadingRevision) &&
    !downloadFailed;
  const failed = part.status === "failed" || downloadFailed;
  const done = part.status === "completed" && Boolean(source) && !loadingRevision;
  const progress = useSimulatedImageProgress(isWorking && !failed, done);

  // Keep the overlay briefly after completion so the bar visibly hits 100%.
  const [showCompletion, setShowCompletion] = useState(false);
  const wasWorkingRef = useRef(false);
  useEffect(() => {
    if (done && wasWorkingRef.current) {
      setShowCompletion(true);
      const timer = window.setTimeout(() => setShowCompletion(false), 900);
      wasWorkingRef.current = false;
      return () => window.clearTimeout(timer);
    }
    if (isWorking) wasWorkingRef.current = true;
    return undefined;
  }, [done, isWorking]);

  const stateLabel = downloadFailed
    ? "图片预览加载失败"
    : part.status === "failed"
      ? "图片生成失败"
      : done
        ? "图片已生成"
        : source
          ? "正在优化预览"
          : "正在生成图片";
  const imageKey = useMemo(
    () => `${fileId || directSource}-${revision}`,
    [directSource, fileId, revision],
  );

  const mimeType = typeof data?.mime_type === "string" ? data.mime_type : "";
  const downloadName = `learngraph-image-${fileId || "generated"}.${
    mimeType.includes("jpeg") || mimeType.includes("jpg")
      ? "jpg"
      : mimeType.includes("webp")
        ? "webp"
        : "png"
  }`;
  const handleDownload = useCallback(async () => {
    if (!source) return;
    try {
      let href = source;
      let revoke = false;
      if (!source.startsWith("blob:") && !source.startsWith("data:")) {
        const response = await fetch(source);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        href = URL.createObjectURL(await response.blob());
        revoke = true;
      }
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = downloadName;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      if (revoke) window.setTimeout(() => URL.revokeObjectURL(href), 2_000);
    } catch {
      toast.error("图片下载失败");
    }
  }, [downloadName, source]);

  const interactive = done && !failed;
  const showOverlay = isWorking || !source || failed || showCompletion;
  const showProgress = !failed && (isWorking || showCompletion || !source);

  return (
    <figure
      aria-busy={isWorking}
      className={`chat-generated-image${
        source ? " chat-generated-image--has-preview" : ""
      }`}
      style={{ aspectRatio }}
    >
      {source ? (
        interactive ? (
          <button
            aria-label="放大查看图片"
            className="chat-generated-image__zoom"
            onClick={() => setLightboxOpen(true)}
            type="button"
          >
            <img
              alt={alt}
              className="chat-generated-image__preview"
              key={imageKey}
              src={source}
            />
          </button>
        ) : (
          <img
            alt={alt}
            className="chat-generated-image__preview"
            key={imageKey}
            src={source}
          />
        )
      ) : null}
      {isWorking && !failed ? (
        <div aria-hidden="true" className="chat-generated-image__shimmer">
          <i className="chat-generated-image__shimmer-dots" />
          <i className="chat-generated-image__shimmer-sweep" />
        </div>
      ) : null}
      {showOverlay ? (
        <div
          className={`chat-generated-image__state${failed ? " is-failed" : ""}`}
          role={failed ? "alert" : "status"}
        >
          <span className="chat-generated-image__icon">
            {failed ? (
              <CircleAlert className="size-5" />
            ) : loadingRevision ? (
              <LoaderCircle className="size-5" />
            ) : (
              <ImageIcon className="size-5" />
            )}
          </span>
          <strong>{stateLabel}</strong>
          <span>{title}</span>
          {showProgress ? (
            <span className="chat-generated-image__progress-row">
              <span
                aria-valuemax={100}
                aria-valuemin={0}
                aria-valuenow={Math.round(progress)}
                className="chat-generated-image__progressbar"
                role="progressbar"
              >
                <i
                  className="chat-generated-image__progressbar-fill"
                  style={{ width: `${progress}%` }}
                />
              </span>
              <span className="chat-generated-image__percent">
                {Math.floor(progress)}%
              </span>
            </span>
          ) : null}
        </div>
      ) : null}
      {interactive ? (
        <div className="chat-generated-image__actions">
          <button
            aria-label="放大查看"
            onClick={() => setLightboxOpen(true)}
            title="放大查看"
            type="button"
          >
            <Maximize2 className="size-3.5" />
          </button>
          <button
            aria-label="下载图片"
            onClick={() => void handleDownload()}
            title="下载图片"
            type="button"
          >
            <Download className="size-3.5" />
          </button>
        </div>
      ) : null}
      {interactive ? (
        <Dialog onOpenChange={setLightboxOpen} open={lightboxOpen}>
          <DialogContent
            aria-describedby={undefined}
            className="chat-image-lightbox"
            showCloseButton={false}
          >
            <DialogTitle className="sr-only">查看生成的图片</DialogTitle>
            <img alt={alt} className="chat-image-lightbox__image" src={source} />
            <div className="chat-image-lightbox__toolbar">
              <button
                onClick={() => void handleDownload()}
                type="button"
              >
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
      ) : null}
    </figure>
  );
}

export function ChatStreamPartRenderer({
  onAction,
  part,
  siblingParts,
  streaming = false,
}: {
  onAction?: (action: TrustedComponentAction) => void | Promise<void>;
  part: MessagePart;
  siblingParts?: MessagePart[];
  streaming?: boolean;
}) {
  if (part.type === "image") return <ChatImagePart part={part} />;
  return (
    <MessagePartRenderer
      onAction={onAction}
      part={part}
      siblingParts={siblingParts}
      streaming={streaming}
    />
  );
}
