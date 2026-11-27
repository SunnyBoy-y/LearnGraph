import { createElement, useCallback, useMemo, useState, type ReactNode } from "react";
import { Code2, Download, Eye, ImageIcon, Maximize2 } from "lucide-react";

import { ImageLightbox } from "@/components/chat/image-lightbox";
import { parseViewBoxAspectRatio } from "@/components/chat/svg-block";
import { Button } from "@/components/ui/button";
import { parseIllustrationSvg, type IllustrationNode } from "@/lib/safe-illustration";
import type { RichContentDisplayMode } from "@/features/chat/response-composer";
import "./unified-artifact-card.css";

function element(node: IllustrationNode | string, key: number): ReactNode {
  return typeof node === "string"
    ? node
    : createElement(node.tag, { ...node.attrs, key }, ...node.children.map(element));
}

export function SvgIllustration({
  svg,
  alt,
  label,
  caption,
  displayMode = "inline",
  variant = "fallback",
}: {
  svg: string;
  alt: string;
  label?: string;
  caption?: string;
  displayMode?: RichContentDisplayMode;
  variant?: "fallback" | "node";
}) {
  const tree = useMemo(() => parseIllustrationSvg(svg), [svg]);
  const [showSource, setShowSource] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);

  const svgDataUrl = useMemo(
    () => (tree ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}` : ""),
    [svg, tree],
  );
  const aspectRatio = useMemo(
    () => parseViewBoxAspectRatio(tree?.attrs.viewBox),
    [tree?.attrs.viewBox],
  );

  const title = (label || alt || "SVG 图形").trim() || "SVG 图形";
  const figureLabel = variant === "fallback" ? `${alt}（SVG 示意图）` : title;
  const svgAriaLabel = alt || label || "SVG 示意图";

  const handleDownload = useCallback(() => {
    if (!tree) return;
    const cleanName = title.replace(/[\\/:*?"<>|]+/g, "_").trim() || "diagram";
    const filename = cleanName.toLowerCase().endsWith(".svg") ? cleanName : `${cleanName}.svg`;
    const blobUrl = URL.createObjectURL(
      new Blob([svg], { type: "image/svg+xml;charset=utf-8" }),
    );
    const anchor = document.createElement("a");
    anchor.href = blobUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(blobUrl), 2_000);
  }, [svg, title, tree]);

  if (!tree) return null;

  const isInline = displayMode === "inline";
  const actionButtons = (
    <>
      <Button
        aria-label={showSource ? `预览 ${title}` : `查看源码 ${title}`}
        onClick={() => setShowSource((prev) => !prev)}
        size="xs"
        type="button"
        variant="ghost"
      >
        {showSource ? <Eye className="size-3.5" /> : <Code2 className="size-3.5" />}
        <span>{showSource ? "预览" : "源码"}</span>
      </Button>
      <Button
        aria-label={`全屏查看 ${title}`}
        onClick={() => setLightboxOpen(true)}
        size="xs"
        type="button"
        variant="ghost"
      >
        <Maximize2 className="size-3.5" />
        <span>全屏</span>
      </Button>
      <Button
        aria-label={`下载 ${title}`}
        onClick={handleDownload}
        size="xs"
        type="button"
        variant={isInline ? "ghost" : "outline"}
      >
        <Download className="size-3.5" />
        <span>下载</span>
      </Button>
    </>
  );

  const svgElement = createElement(
    "svg",
    {
      ...tree.attrs,
      role: "img",
      "aria-label": svgAriaLabel,
      preserveAspectRatio: "xMidYMid meet",
      style: aspectRatio ? { aspectRatio } : undefined,
    },
    ...tree.children.map(element),
  );

  if (!isInline) {
    return (
      <section
        aria-label={title}
        className="unified-artifact-card unified-artifact-card--artifact"
        data-artifact-type="svg"
        data-display-mode="artifact"
      >
        <header className="unified-artifact-card__header">
          <div className="unified-artifact-card__identity">
            <span className="unified-artifact-card__icon" aria-hidden="true">
              <ImageIcon className="size-4" />
            </span>
            <div className="unified-artifact-card__titles">
              <strong className="unified-artifact-card__title">{title}</strong>
            </div>
          </div>
          <div className="unified-artifact-card__actions">{actionButtons}</div>
        </header>
        <div className="unified-artifact-card__stage">
          {showSource ? (
            <pre className="unified-artifact-card__source">
              <code>{svg}</code>
            </pre>
          ) : (
            <figure className="rich-doc__illustration" aria-label={figureLabel}>
              {svgElement}
              {variant === "node" && caption ? <figcaption>{caption}</figcaption> : null}
            </figure>
          )}
        </div>
        {svgDataUrl ? (
          <ImageLightbox
            actions={
              <button onClick={handleDownload} type="button">
                <Download className="size-4" />
                下载 SVG
              </button>
            }
            alt={svgAriaLabel}
            dialogTitle={title}
            onOpenChange={setLightboxOpen}
            open={lightboxOpen}
            src={svgDataUrl}
          />
        ) : null}
      </section>
    );
  }

  return (
    <figure
      aria-label={figureLabel}
      className="rich-doc__illustration rich-svg-figure"
      data-display-mode="inline"
      data-source-open={showSource ? "true" : undefined}
    >
      <div className="rich-doc__illustration-stage">
        <div
          aria-label={`${title} 操作`}
          className="rich-svg-figure__floating-actions"
          role="toolbar"
        >
          {actionButtons}
        </div>
        {showSource ? (
          <pre className="unified-artifact-card__source unified-artifact-card__source--inline">
            <code>{svg}</code>
          </pre>
        ) : (
          svgElement
        )}
      </div>
      {variant === "node" && caption ? <figcaption>{caption}</figcaption> : null}
      {svgDataUrl ? (
        <ImageLightbox
          actions={
            <button onClick={handleDownload} type="button">
              <Download className="size-4" />
              下载 SVG
            </button>
          }
          alt={svgAriaLabel}
          dialogTitle={title}
          onOpenChange={setLightboxOpen}
          open={lightboxOpen}
          src={svgDataUrl}
        />
      ) : null}
    </figure>
  );
}
