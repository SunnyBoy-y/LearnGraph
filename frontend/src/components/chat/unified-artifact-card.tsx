import { useCallback, useMemo, useState } from "react";
import {
  CircleAlert,
  Code2,
  Download,
  Eye,
  FileCode2,
  ImageIcon,
  LoaderCircle,
  Maximize2,
} from "lucide-react";

import { fileStreamUrl } from "@/api/files";
import { FullscreenPreview } from "@/components/chat/fullscreen-preview";
import { SVGBlock } from "@/components/chat/svg-block";
import { parseIllustrationSvg } from "@/lib/safe-illustration";
import { ImageLightbox } from "@/components/chat/image-lightbox";
import { FilePreviewContent } from "@/components/resources/file-preview-content";
import { Button } from "@/components/ui/button";
import {
  resolveArtifactMeta,
  type UnifiedArtifactMeta,
} from "@/features/chat/response-composer";
import { useFileStreamUrl } from "@/hooks/use-file-stream-url";
import { downloadViaNative, toAbsoluteApiUrl } from "@/lib/native-download";
import { sandboxedHtmlPreviewDocument } from "@/lib/sandboxed-html-preview";
import { cn } from "@/lib/utils";
import type { MessagePart } from "@/types/sessions";
import "./unified-artifact-card.css";

function formatSize(sizeBytes: unknown): string {
  if (typeof sizeBytes !== "number" || !Number.isFinite(sizeBytes)) return "";
  if (sizeBytes < 1024) return `${Math.max(0, Math.round(sizeBytes))} B`;
  if (sizeBytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
  }
  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
}

function parseSvgAspectRatio(svgMarkup: string | undefined): string | undefined {
  if (!svgMarkup) return undefined;
  const vb = /viewBox\s*=\s*["']\s*([+-]?\d*\.?\d+)[\s,]+([+-]?\d*\.?\d+)[\s,]+([+-]?\d*\.?\d+)[\s,]+([+-]?\d*\.?\d+)\s*["']/i.exec(
    svgMarkup,
  );
  if (vb) {
    const w = Number(vb[3]);
    const h = Number(vb[4]);
    if (Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0) {
      return `${w} / ${h}`;
    }
  }
  return undefined;
}

export function UnifiedArtifactCard({
  part,
  meta: providedMeta,
  siblingParts,
}: {
  part: MessagePart;
  meta?: UnifiedArtifactMeta;
  siblingParts?: MessagePart[];
}) {
  const meta = useMemo(
    () => providedMeta ?? resolveArtifactMeta(part, siblingParts),
    [part, providedMeta, siblingParts],
  );

  const [downloading, setDownloading] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [showSource, setShowSource] = useState(false);

  const fileId = meta.fileId ?? "";
  const needsStream =
    Boolean(fileId) && meta.artifactType === "svg" && !meta.svgMarkup;
  const {
    source: streamedUrl,
    loading: streamLoading,
    failed: streamFailed,
  } = useFileStreamUrl(needsStream ? fileId : "", part.id);

  const isNativeSvg = part.type === "svg";
  const isSvg = meta.artifactType === "svg";
  const svgMarkup = useMemo(() => {
    const source = meta.svgMarkup;
    return source && (!isNativeSvg || parseIllustrationSvg(source, true)) ? source : undefined;
  }, [isNativeSvg, meta.svgMarkup]);
  const svgDataUrl = useMemo(() => {
    if (!svgMarkup) return "";
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svgMarkup)}`;
  }, [svgMarkup]);

  const svgSrc = svgDataUrl || streamedUrl;
  const svgAspectRatio = useMemo(
    () => parseSvgAspectRatio(meta.svgMarkup),
    [meta.svgMarkup],
  );

  const sizeLabel = formatSize(meta.sizeBytes);
  const metaLine = [
    meta.path || undefined,
    sizeLabel || undefined,
  ]
    .filter(Boolean)
    .join(" · ");

  const downloadFilename = useMemo(() => {
    if (meta.path) {
      const base = meta.path.split("/").pop();
      if (base) return base;
    }
    const cleanTitle = meta.title.replace(/[\\/:*?"<>|]+/g, "_").trim() || "artifact";
    if (meta.artifactType === "svg" && !cleanTitle.toLowerCase().endsWith(".svg")) {
      return `${cleanTitle}.svg`;
    }
    if (meta.artifactType === "html" && !/\.html?$/i.test(cleanTitle)) {
      return `${cleanTitle}.html`;
    }
    return cleanTitle;
  }, [meta.artifactType, meta.path, meta.title]);

  const handleDownload = useCallback(async () => {
    if (downloading) return;
    setDownloading(true);
    try {
      if (fileId) {
        if (
          downloadViaNative(
            toAbsoluteApiUrl(`/api/v1/files/${encodeURIComponent(fileId)}/content`),
            downloadFilename,
          )
        ) {
          return;
        }
        const url = await fileStreamUrl(fileId);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = downloadFilename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        return;
      }

      const inlinePayload = isSvg ? svgMarkup : meta.previewHtml;
      if (!inlinePayload) return;
      const mime =
        meta.artifactType === "svg"
          ? "image/svg+xml;charset=utf-8"
          : "text/html;charset=utf-8";
      const blobUrl = URL.createObjectURL(new Blob([inlinePayload], { type: mime }));
      const anchor = document.createElement("a");
      anchor.href = blobUrl;
      anchor.download = downloadFilename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(blobUrl), 2_000);
    } finally {
      setDownloading(false);
    }
  }, [downloadFilename, downloading, fileId, isSvg, meta.previewHtml, svgMarkup]);

  const canDownload = Boolean(fileId || (isSvg ? svgMarkup : meta.previewHtml));
  const canToggleSource = Boolean(svgMarkup);
  const isHtml = meta.artifactType === "html";

  return (
    <section
      aria-label={meta.title}
      className={cn(
        "unified-artifact-card",
        meta.capabilities.wideLayout && "unified-artifact-card--wide",
      )}
      data-artifact-canonical-id={meta.canonicalId}
      data-artifact-type={meta.artifactType}
    >
      <header className="unified-artifact-card__header">
        <div className="unified-artifact-card__identity">
          <span className="unified-artifact-card__icon" aria-hidden="true">
            {isSvg ? (
              <ImageIcon className="size-4" />
            ) : (
              <FileCode2 className="size-4" />
            )}
          </span>
          <div className="unified-artifact-card__titles">
            <strong className="unified-artifact-card__title">{meta.title}</strong>
            {metaLine ? (
              <span className="unified-artifact-card__meta" title={metaLine}>
                {metaLine}
              </span>
            ) : null}
          </div>
        </div>

        <div className="unified-artifact-card__actions">
          {canToggleSource ? (
            <Button
              aria-label={showSource ? `预览 ${meta.title}` : `查看源码 ${meta.title}`}
              onClick={() => setShowSource((prev) => !prev)}
              size="xs"
              type="button"
              variant="ghost"
            >
              {showSource ? <Eye className="size-3.5" /> : <Code2 className="size-3.5" />}
              <span>{showSource ? "预览" : "源码"}</span>
            </Button>
          ) : null}
          {isSvg && svgSrc ? (
            <Button
              aria-label={`全屏查看 ${meta.title}`}
              onClick={() => setLightboxOpen(true)}
              size="xs"
              type="button"
              variant="ghost"
            >
              <Maximize2 className="size-3.5" />
              <span>全屏</span>
            </Button>
          ) : null}
          {canDownload ? (
            <Button
              aria-label={`下载 ${meta.title}`}
              disabled={downloading}
              onClick={() => void handleDownload()}
              size="xs"
              type="button"
              variant="outline"
            >
              {downloading ? (
                <LoaderCircle className="size-3.5 animate-spin" />
              ) : (
                <Download className="size-3.5" />
              )}
              <span>下载</span>
            </Button>
          ) : null}
        </div>
      </header>

      <div className="unified-artifact-card__stage">
        {showSource && svgMarkup ? (
          <pre className="unified-artifact-card__source">
            <code>{svgMarkup}</code>
          </pre>
        ) : isNativeSvg ? (
          <SVGBlock label={meta.title} nodes={part.data?.nodes} viewBox={part.data?.view_box} />
        ) : isSvg ? (
          needsStream && streamLoading ? (
            <div className="unified-artifact-card__state" role="status">
              <LoaderCircle className="size-4 animate-spin" />
              <span>正在加载预览…</span>
            </div>
          ) : svgSrc && !streamFailed ? (
            <button
              aria-label={`放大查看 ${meta.title}`}
              className="unified-artifact-card__svg-button"
              onClick={() => setLightboxOpen(true)}
              type="button"
            >
              <img
                alt={meta.title}
                className="unified-artifact-card__svg-img"
                src={svgSrc}
                style={svgAspectRatio ? { aspectRatio: svgAspectRatio } : undefined}
              />
            </button>
          ) : meta.previewHtml ? (
            <FullscreenPreview className="unified-artifact-card__frame-wrap" label={meta.title}>
              <iframe
                allow=""
                className="unified-artifact-card__frame"
                loading="lazy"
                referrerPolicy="no-referrer"
                sandbox="allow-scripts"
                srcDoc={sandboxedHtmlPreviewDocument(meta.previewHtml, {
                  runtimeShim: false,
                })}
                title={meta.title}
              />
            </FullscreenPreview>
          ) : (
            <div className="unified-artifact-card__state" role="alert">
              <CircleAlert className="size-4" />
              <span>SVG 预览不可用</span>
            </div>
          )
        ) : isHtml ? (
          meta.previewHtml ? (
            <FullscreenPreview className="unified-artifact-card__frame-wrap" label={meta.title}>
              <iframe
                allow=""
                className="unified-artifact-card__frame"
                loading="lazy"
                referrerPolicy="no-referrer"
                sandbox="allow-scripts"
                srcDoc={sandboxedHtmlPreviewDocument(meta.previewHtml, {
                  runtimeShim: true,
                })}
                title={meta.title}
              />
            </FullscreenPreview>
          ) : fileId ? (
            <FullscreenPreview className="unified-artifact-card__frame-wrap" label={meta.title}>
              <FilePreviewContent
                className="min-h-[380px]"
                enabled
                fileId={fileId}
                fileSize={meta.sizeBytes}
                filename={meta.path || meta.title}
                mimeType={meta.mimeType}
              />
            </FullscreenPreview>
          ) : null
        ) : null}
      </div>

      {isSvg && svgSrc ? (
        <ImageLightbox
          actions={
            canDownload ? (
              <button onClick={() => void handleDownload()} type="button">
                <Download className="size-4" />
                下载 SVG
              </button>
            ) : undefined
          }
          alt={meta.title}
          dialogTitle={meta.title}
          onOpenChange={setLightboxOpen}
          open={lightboxOpen}
          src={svgSrc}
        />
      ) : null}
    </section>
  );
}
