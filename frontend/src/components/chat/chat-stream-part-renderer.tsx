import { useEffect, useMemo, useState } from "react";
import { CircleAlert, ImageIcon, LoaderCircle } from "lucide-react";

import { downloadFile } from "@/api/files";
import { MessagePartRenderer } from "@/components/chat/message-part-renderer";
import type { TrustedComponentAction } from "@/components/chat/trusted-component-renderer";
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
  const stateLabel =
    downloadFailed
      ? "图片预览加载失败"
      : part.status === "failed"
      ? "图片生成失败"
      : source
        ? "正在优化预览"
        : "正在生成图片";
  const imageKey = useMemo(
    () => `${fileId || directSource}-${revision}`,
    [directSource, fileId, revision],
  );

  return (
    <figure
      aria-busy={isWorking}
      className="chat-generated-image"
      style={{ aspectRatio }}
    >
      {source ? (
        <img alt={alt} className="chat-generated-image__preview" key={imageKey} src={source} />
      ) : null}
      {isWorking || !source || failed ? (
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
          {part.status !== "failed" && !downloadFailed ? (
            <i aria-hidden="true" className="chat-generated-image__progress" />
          ) : null}
        </div>
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
