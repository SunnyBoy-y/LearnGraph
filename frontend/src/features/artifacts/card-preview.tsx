import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Code2, Eye, FileText, Play, Sparkles } from "lucide-react";

import { getArtifactCardPreview } from "@/api/artifacts";
import { TrustedComponentRenderer } from "@/components/chat/trusted-component-renderer";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  artifactKindMeta,
  DECLARATIVE_COMPONENT_CARD_TYPE,
  extractPureSvgDataUrl,
  needsScriptForInitialCover,
  resolveArtifactGalleryKind,
  type ArtifactGalleryKind,
} from "@/features/artifacts/card-types";
import { usePreviewPause } from "@/lib/preview-pause-controller";
import { workspaceQueryKey } from "@/lib/query-keys";
import { sandboxedHtmlPreviewDocument } from "@/lib/sandboxed-html-preview";
import { cn } from "@/lib/utils";
import type { ArtifactCard } from "@/types/artifacts";

export const artifactCardPreviewViewportClassName =
  "artifact-card-preview-viewport relative aspect-video w-full min-w-0 overflow-hidden rounded-[8px] bg-[#f7f7f8] dark:bg-muted/40";

const COVER_CANVAS_WIDTH = 800;
const COVER_CANVAS_HEIGHT = 450;
const COVER_RESET_STYLE = `<style data-lg-cover-fit>
html,body{margin:0!important;padding:0!important;width:${COVER_CANVAS_WIDTH}px!important;height:${COVER_CANVAS_HEIGHT}px!important;overflow:hidden!important;}
svg{max-width:100%;height:auto;}
::-webkit-scrollbar{display:none;}
</style>`;

function buildCoverDocument(
  previewHtml: string,
  options: { scripts: boolean; centerSvg?: boolean },
): string {
  const sanitized = sandboxedHtmlPreviewDocument(previewHtml, {
    scripts: options.scripts,
    runtimeShim: false,
  });
  const extraStyle = options.centerSvg
    ? `<style data-lg-cover-fit>
html,body{margin:0!important;padding:16px!important;width:${COVER_CANVAS_WIDTH}px!important;height:${COVER_CANVAS_HEIGHT}px!important;overflow:hidden!important;display:flex!important;flex-direction:column!important;align-items:center!important;justify-content:center!important;background:#f7f7f8!important;}
main,section,article{max-width:100%!important;width:100%!important;margin:0!important;}
svg{max-width:100%!important;max-height:360px!important;height:auto!important;display:block;margin:auto;}
::-webkit-scrollbar{display:none;}
</style>`
    : COVER_RESET_STYLE;
  if (/<\/head>/i.test(sanitized)) {
    return sanitized.replace(/<\/head>/i, `${extraStyle}</head>`);
  }
  return `${extraStyle}${sanitized}`;
}

function isCardControl(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    Boolean(
      target.closest(
        'button, a, input, select, textarea, [role="button"], [role="menuitem"], [data-preview-control]',
      ),
    )
  );
}

export function ArtifactKindBadge({
  kind,
  className,
}: {
  kind: ArtifactGalleryKind;
  className?: string;
}) {
  const meta = artifactKindMeta(kind);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border border-black/8 bg-background/92 px-2 py-0.5 text-[11px] font-medium leading-4 text-foreground/85 shadow-2xs backdrop-blur-xs dark:border-white/12 dark:bg-background/85",
        className,
      )}
      data-artifact-kind-badge={kind}
    >
      {kind === "interactive" ? <Code2 className="size-3 text-foreground/70" /> : null}
      {kind === "html" ? <FileText className="size-3 text-foreground/70" /> : null}
      {kind === "animation" ? <Sparkles className="size-3 text-foreground/70" /> : null}
      <span>{meta.badgeLabel}</span>
    </span>
  );
}

/**
 * Clean 16:9 Gallery Card shell following the Artifact Gallery design:
 * 1. Crisp 16:9 preview first (no white wash, no duplicate play buttons, no nested card borders)
 * 2. Title + `...` More action menu + `source · updatedAt` below preview
 * 3. Distinct open action + Share button in footer
 */
export function ArtifactCardShell({
  ariaLabel,
  card,
  footer,
  headerAction,
  meta,
  metaAriaLabel = "产物元信息",
  onActivate,
  onResolvedKind,
  selected = false,
  subtitle,
  version,
  workspaceId,
}: {
  ariaLabel?: string;
  card: ArtifactCard;
  footer: ReactNode;
  headerAction?: ReactNode;
  meta?: ReactNode;
  metaAriaLabel?: string;
  onActivate: (cardId: string, resolvedKind?: ArtifactGalleryKind) => void;
  onResolvedKind?: (cardId: string, kind: ArtifactGalleryKind) => void;
  selected?: boolean;
  subtitle?: ReactNode;
  /** When set, render the immutable published snapshot instead of the draft. */
  version?: number;
  workspaceId: string;
}) {
  const [resolvedKind, setResolvedKind] = useState<ArtifactGalleryKind>(() =>
    resolveArtifactGalleryKind(card),
  );

  const handleKindResolved = useCallback(
    (kind: ArtifactGalleryKind) => {
      setResolvedKind(kind);
      onResolvedKind?.(card.card_id, kind);
    },
    [card.card_id, onResolvedKind],
  );

  const handleActivate = useCallback(() => {
    onActivate(card.id, resolvedKind);
  }, [card.id, onActivate, resolvedKind]);

  return (
    <div className="artifact-card-item flex min-w-0 flex-col">
      <article
        aria-label={ariaLabel ?? card.title}
        className={cn(
          "artifact-card group flex min-w-0 flex-col overflow-hidden rounded-[12px] border border-[#e5e5e5] bg-background p-2 transition-all duration-200 hover:border-[#c8c8cc] hover:shadow-md dark:border-border dark:hover:border-border/90",
          selected && "border-ring ring-1 ring-ring/40",
        )}
        style={{ contentVisibility: "auto", containIntrinsicSize: "300px" }}
      >
        <div
          className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-[8px]"
          onClick={(event) => {
            if (isCardControl(event.target)) return;
            handleActivate();
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              if (isCardControl(event.target)) return;
              event.preventDefault();
              handleActivate();
            }
          }}
          role="button"
          tabIndex={0}
          title={`${artifactKindMeta(resolvedKind).actionLabel} ${card.title}`}
        >
          <CardInlinePreview
            card={card}
            kind={resolvedKind}
            onKindResolved={handleKindResolved}
            version={version}
            workspaceId={workspaceId}
          />
        </div>

        <div className="grid min-w-0 gap-0.5 px-1 pt-2.5 pb-1.5">
          <div className="flex min-w-0 items-center gap-1.5">
            <h3
              className="min-w-0 flex-1 truncate text-[15px] font-semibold leading-snug tracking-tight text-foreground"
              title={card.title}
            >
              {card.title}
            </h3>
            {headerAction ? (
              <div className="shrink-0" data-preview-control="true">
                {headerAction}
              </div>
            ) : null}
          </div>
          {subtitle ? (
            <div className="flex min-w-0 items-center gap-1.5 truncate text-xs text-muted-foreground">
              {subtitle}
            </div>
          ) : null}
          {meta ? (
            <div
              aria-label={metaAriaLabel}
              className="artifact-card-meta mt-1 flex min-w-0 flex-wrap items-center gap-1.5"
              role="group"
            >
              {meta}
            </div>
          ) : null}
        </div>

        <div
          className="mt-auto flex min-w-0 flex-wrap items-center gap-1 border-t border-border/60 px-1 pt-2 pb-0.5"
          data-preview-control="true"
        >
          {footer}
        </div>
      </article>
    </div>
  );
}

export function CardInlinePreview({
  card,
  kind,
  onKindResolved,
  version,
  workspaceId,
}: {
  card: ArtifactCard;
  kind?: ArtifactGalleryKind;
  onKindResolved?: (kind: ArtifactGalleryKind) => void;
  /** When set, render the immutable published snapshot instead of the draft. */
  version?: number;
  workspaceId: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const frameWrapRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [frameLoaded, setFrameLoaded] = useState(false);
  const [scale, setScale] = useState(0.4);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) setVisible(true);
      },
      { rootMargin: "480px" },
    );
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const updateScale = () => {
      const width = container.clientWidth;
      if (width > 0) {
        setScale(width / COVER_CANVAS_WIDTH);
      }
    };
    updateScale();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(updateScale);
    ro.observe(container);
    return () => ro.disconnect();
  }, []);

  const preview = useQuery({
    queryKey: workspaceQueryKey(
      workspaceId,
      "cards",
      card.card_id,
      "preview",
      version == null ? "draft" : "version",
      version ?? card.updated_at,
    ),
    queryFn: () =>
      version == null
        ? getArtifactCardPreview(card.card_id)
        : getArtifactCardPreview(card.card_id, { version }),
    enabled: visible,
    staleTime: 60_000,
  });

  const snapshot = preview.data?.preview_snapshot;
  const effectiveKind = useMemo(
    () => kind ?? resolveArtifactGalleryKind(card, snapshot),
    [card, kind, snapshot],
  );

  useEffect(() => {
    if (!snapshot) return;
    const detected = resolveArtifactGalleryKind(card, snapshot);
    if (detected !== kind) {
      onKindResolved?.(detected);
    }
  }, [card, kind, onKindResolved, snapshot]);

  const previewHtml = typeof snapshot?.preview_html === "string" ? snapshot.preview_html : "";
  const bundleId = typeof snapshot?.bundle_id === "string" ? snapshot.bundle_id : "";
  const artifactUrl = typeof snapshot?.artifact_url === "string" ? snapshot.artifact_url : "";
  const { pureSvgDataUrl } = useMemo(
    () =>
      effectiveKind === "svg" && previewHtml
        ? extractPureSvgDataUrl(previewHtml)
        : { svgMarkup: undefined, pureSvgDataUrl: undefined },
    [effectiveKind, previewHtml],
  );

  const allowScriptsInCover = useMemo(() => {
    if (!previewHtml) return false;
    if (effectiveKind === "animation") return true;
    return needsScriptForInitialCover(previewHtml);
  }, [effectiveKind, previewHtml]);

  const coverSrcDoc = useMemo(() => {
    if (!previewHtml || pureSvgDataUrl) return "";
    return buildCoverDocument(previewHtml, {
      scripts: allowScriptsInCover,
      centerSvg: effectiveKind === "svg",
    });
  }, [allowScriptsInCover, effectiveKind, previewHtml, pureSvgDataUrl]);

  // Pause JS-driven cover frames (and resume animation previews on hover)
  const { pause, resume } = usePreviewPause(frameWrapRef, {
    autoVisibility: true,
  });

  useEffect(() => {
    setFrameLoaded(false);
  }, [coverSrcDoc]);

  useEffect(() => {
    if (!allowScriptsInCover || !frameLoaded) return;
    if (effectiveKind === "animation") {
      if (hovered) {
        resume();
        return;
      }
      const timer = window.setTimeout(() => pause(), 120);
      return () => window.clearTimeout(timer);
    }
    // For canvas/JS-rendered interactive covers, allow a brief paint window then pause.
    resume();
    const timer = window.setTimeout(() => pause(), 450);
    return () => window.clearTimeout(timer);
  }, [allowScriptsInCover, effectiveKind, frameLoaded, hovered, pause, resume]);

  const kindMeta = artifactKindMeta(effectiveKind);

  return (
    <div
      className={artifactCardPreviewViewportClassName}
      data-artifact-kind={effectiveKind}
      data-artifact-preview-viewport="true"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      ref={containerRef}
    >
      {/* Top-left type badge */}
      <div className="pointer-events-none absolute top-2 left-2 z-20">
        <ArtifactKindBadge kind={effectiveKind} />
      </div>

      {!visible || preview.isPending ? (
        <Skeleton aria-label={`正在加载 ${card.title} 的预览`} className="h-full w-full rounded-none" />
      ) : preview.isError ? (
        <div
          className="flex h-full w-full flex-col items-center justify-center gap-2 p-4 text-center"
          data-preview-control="true"
        >
          <p className="line-clamp-2 text-xs text-destructive">
            {preview.error.message || "加载预览失败"}
          </p>
          <Button
            onClick={() => void preview.refetch()}
            size="xs"
            type="button"
            variant="outline"
          >
            重新加载
          </Button>
        </div>
      ) : card.card_type === DECLARATIVE_COMPONENT_CARD_TYPE ? (
        <div className="pointer-events-none h-full w-full overflow-hidden p-3 pt-8">
          <TrustedComponentRenderer
            data={snapshot ?? {}}
            fallbackId={card.card_id}
            interactive={false}
          />
        </div>
      ) : pureSvgDataUrl ? (
        <img
          alt={card.title}
          className="h-full w-full select-none object-contain p-3 pt-6 transition-transform duration-200 group-hover:scale-[1.02]"
          draggable={false}
          src={pureSvgDataUrl}
        />
      ) : coverSrcDoc ? (
        <div className="relative h-full w-full overflow-hidden" ref={frameWrapRef}>
          <iframe
            aria-hidden="true"
            className="pointer-events-none absolute top-0 left-0 select-none border-0 bg-white"
            loading="lazy"
            onLoad={() => setFrameLoaded(true)}
            referrerPolicy="no-referrer"
            sandbox={allowScriptsInCover ? "allow-scripts" : ""}
            scrolling="no"
            srcDoc={coverSrcDoc}
            style={{
              width: COVER_CANVAS_WIDTH,
              height: COVER_CANVAS_HEIGHT,
              transform: `scale(${scale})`,
              transformOrigin: "top left",
            }}
            tabIndex={-1}
            title={`${card.title} 封面预览`}
          />
        </div>
      ) : bundleId || artifactUrl ? (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 bg-gradient-to-br from-slate-50 to-slate-100/80 p-4 text-center dark:from-muted/40 dark:to-muted/20">
          <div className="flex size-10 items-center justify-center rounded-xl border bg-background shadow-xs">
            <Code2 className="size-5 text-foreground/75" />
          </div>
          <p className="line-clamp-1 max-w-[80%] text-xs font-medium text-foreground/85">
            {card.title}
          </p>
          <span className="text-[11px] text-muted-foreground">点击启动交互式子应用</span>
        </div>
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-1.5 p-4 text-center">
          <FileText className="size-5 text-muted-foreground" />
          <span className="text-xs text-muted-foreground">暂无预览内容</span>
        </div>
      )}

      {/* Subtle corner badge for animations (never obscuring content) */}
      {effectiveKind === "animation" && !preview.isPending && !preview.isError ? (
        <div className="pointer-events-none absolute right-2 bottom-2 z-20 inline-flex items-center gap-1 rounded-md bg-black/65 px-2 py-0.5 text-[10px] font-medium text-white backdrop-blur-xs">
          <Play className="size-2.5 fill-current" />
          <span>{hovered ? "预览中" : "悬停播放"}</span>
        </div>
      ) : null}

      {/* Subtle hover pill for quick open affordance — zero wash when idle */}
      {effectiveKind !== "animation" && !preview.isPending && !preview.isError ? (
        <div className="pointer-events-none absolute right-2 bottom-2 z-20 inline-flex items-center gap-1 rounded-md bg-foreground/85 px-2 py-0.5 text-[11px] font-medium text-background opacity-0 shadow-xs transition-opacity duration-150 group-hover:opacity-100">
          {effectiveKind === "svg" ? (
            <Eye className="size-3" />
          ) : (
            <Play className="size-3" />
          )}
          <span>{kindMeta.actionLabel}</span>
        </div>
      ) : null}
    </div>
  );
}
