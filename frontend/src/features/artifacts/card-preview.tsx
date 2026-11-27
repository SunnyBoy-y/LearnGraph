import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Code2, FileText, Sparkles } from "lucide-react";

import { getArtifactCardPreview } from "@/api/artifacts";
import { TrustedComponentRenderer } from "@/components/chat/trusted-component-renderer";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import {
  artifactKindMeta,
  DECLARATIVE_COMPONENT_CARD_TYPE,
  extractPureSvgDataUrl,
  resolveArtifactGalleryKind,
  type ArtifactGalleryKind,
} from "@/features/artifacts/card-types";
import { workspaceQueryKey } from "@/lib/query-keys";
import { sandboxedHtmlPreviewDocument } from "@/lib/sandboxed-html-preview";
import { cn } from "@/lib/utils";
import type { ArtifactCard } from "@/types/artifacts";

export const artifactCardPreviewViewportClassName = "artifact-card-preview-viewport relative aspect-video w-full min-w-0 overflow-hidden rounded-[16px] bg-muted/60";
const COVER_WIDTH = 800;
const COVER_HEIGHT = 450;

function buildCoverDocument(html: string, kind: ArtifactGalleryKind) {
  // Keep authored HTML and CSS but never start an application inside the gallery.
  const document = sandboxedHtmlPreviewDocument(html, { scripts: false, runtimeShim: false });
  const styles = `<style data-lg-cover-fit>
html{margin:0!important;overflow:hidden!important}
body{min-height:100vh;box-sizing:border-box;overflow:hidden!important}
:where(body){margin:0;align-content:center}
*,*::before,*::after{animation-play-state:paused!important;transition:none!important}
${kind === "svg" ? "svg{display:block;margin:auto;max-width:100%!important;max-height:100vh!important;height:auto}" : ""}
</style>`;
  return /<\/head>/i.test(document) ? document.replace(/<\/head>/i, `${styles}</head>`) : `${styles}${document}`;
}

export function ArtifactKindBadge({ kind, className }: { kind: ArtifactGalleryKind; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs font-medium text-muted-foreground", className)} data-artifact-kind-badge={kind}>
      {kind === "interactive" ? <Code2 className="size-3" /> : null}
      {kind === "html" ? <FileText className="size-3" /> : null}
      {kind === "animation" ? <Sparkles className="size-3" /> : null}
      {artifactKindMeta(kind).badgeLabel}
    </span>
  );
}

export function ArtifactCardShell({
  activationLabel, ariaLabel, card, footer, headerAction, meta, metaAriaLabel, onActivate, onResolvedKind,
  selected = false, subtitle, updated, version, workspaceId, expandedContent, selection, compact,
}: {
  compact?: boolean;
  selection?: { checked: boolean; disabled: boolean; toggle: (shiftKey?: boolean) => void };
  activationLabel?: string;
  ariaLabel?: string;
  card: ArtifactCard;
  footer: ReactNode;
  expandedContent?: ReactNode;
  headerAction?: ReactNode;
  meta?: ReactNode;
  metaAriaLabel?: string;
  onActivate: (cardId: string, resolvedKind?: ArtifactGalleryKind) => void;
  onResolvedKind?: (cardId: string, kind: ArtifactGalleryKind) => void;
  selected?: boolean;
  subtitle?: ReactNode;
  updated?: ReactNode;
  version?: number;
  workspaceId: string;
}) {
  const [kind, setKind] = useState(() => resolveArtifactGalleryKind(card));
  const handleKindResolved = useCallback((next: ArtifactGalleryKind) => {
    setKind(next);
    onResolvedKind?.(card.card_id, next);
  }, [card.card_id, onResolvedKind]);

  return (
    <article aria-label={ariaLabel ?? card.title} className={cn(
      "artifact-card group relative flex min-w-0 flex-col rounded-[24px] border border-border/70 bg-card p-3 text-card-foreground shadow-xs motion-safe:transition-[transform,box-shadow] motion-safe:duration-200 motion-safe:hover:-translate-y-0.5 hover:shadow-sm",
      compact && "motion-safe:hover:translate-y-0",
      (selected || selection?.checked) && "border-ring ring-1 ring-ring/40",
      selection && !selection.disabled && "cursor-pointer",
    )} onClick={selection && !selection.disabled ? (event) => {
      if (!(event.target as Element).closest('button')) selection.toggle(event.shiftKey);
    } : undefined}>
      {selection && <Checkbox aria-label={`选择 ${card.title}`} checked={selection.checked} disabled={selection.disabled} onClickCapture={(event) => { (event.currentTarget as HTMLElement).dataset.shift = event.shiftKey ? "1" : ""; }} onCheckedChange={() => { const el = document.activeElement as HTMLElement | null; const shift = el?.dataset.shift === "1"; if (el?.dataset) delete el.dataset.shift; selection.toggle(shift); }} className="absolute left-5 top-5 z-10 size-5 bg-background" />}
      <div className="relative">
        <ArtifactPreview card={card} onKindResolved={handleKindResolved} version={version} workspaceId={workspaceId} />
        {!selection && <button
          aria-label={activationLabel ?? `${meta ? "打开页面" : artifactKindMeta(kind).actionLabel} ${card.title}`}
          className="absolute inset-0 cursor-pointer rounded-[16px] focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          onClick={() => onActivate(card.id, kind)}
          type="button"
        />}
      </div>
      <div className={cn("grid min-w-0 gap-1 px-1 py-3", compact && "py-2")}>
        <div className="flex min-w-0 items-center justify-between gap-2 text-xs text-muted-foreground">
          <ArtifactKindBadge kind={kind} />
          {updated}
        </div>
        <div className="flex min-w-0 items-start gap-2">
          <h3 className="line-clamp-2 min-w-0 flex-1 break-words text-base font-semibold leading-snug sm:text-lg" title={card.title}>
            {card.title}
          </h3>
          {headerAction ? <div className="shrink-0">{headerAction}</div> : null}
        </div>
        {subtitle ? <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">{subtitle}</div> : null}
        {meta ? <div aria-label={metaAriaLabel} className="flex min-w-0 flex-wrap items-center gap-2 text-xs text-muted-foreground" role="group">{meta}</div> : null}
      </div>
      {footer != null && <div className={cn("mt-auto flex min-w-0 flex-wrap items-center gap-1.5 border-t border-border/60 px-1 pt-2.5", compact && "pt-2")}>
        {footer}
      </div>}
      {expandedContent}
    </article>
  );
}

/** Shared, lazy, non-interactive rendering for draft and immutable published previews. */
export function ArtifactPreview({ card, onKindResolved, version, workspaceId }: {
  card: ArtifactCard;
  onKindResolved?: (kind: ArtifactGalleryKind) => void;
  version?: number;
  workspaceId: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [scale, setScale] = useState(0.4);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        setVisible(true);
        observer.disconnect();
      }
    }, { rootMargin: "320px" });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const resize = () => {
      if (container.clientWidth) setScale(container.clientWidth / COVER_WIDTH);
    };
    resize();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  const preview = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "cards", card.card_id, "preview", version == null ? "draft" : "version", version ?? card.updated_at),
    queryFn: () => version == null ? getArtifactCardPreview(card.card_id) : getArtifactCardPreview(card.card_id, { version }),
    enabled: visible,
    staleTime: 60_000,
  });
  const snapshot = preview.data?.preview_snapshot;
  const kind = resolveArtifactGalleryKind(card, snapshot);
  useEffect(() => { onKindResolved?.(kind); }, [kind, onKindResolved]);

  const html = typeof snapshot?.preview_html === "string" ? snapshot.preview_html : "";
  const svg = useMemo(() => kind === "svg" && html ? extractPureSvgDataUrl(html).pureSvgDataUrl : undefined, [kind, html]);
  const cover = useMemo(() => html && !svg ? buildCoverDocument(html, kind) : "", [html, kind, svg]);
  const runtimeOnly = Boolean(snapshot?.bundle_id || snapshot?.artifact_url);

  return (
    <div className={artifactCardPreviewViewportClassName} data-artifact-kind={kind} data-artifact-preview-viewport="true" ref={containerRef}>
      {!visible || preview.isPending ? (
        <Skeleton aria-label={`正在加载 ${card.title} 的预览`} className="h-full w-full rounded-none" />
      ) : preview.isError ? (
        <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center">
          <p className="line-clamp-2 text-xs text-destructive">{preview.error.message || "加载预览失败"}</p>
          <Button className="relative z-10" onClick={() => void preview.refetch()} size="xs" type="button" variant="outline">重新加载</Button>
        </div>
      ) : card.card_type === DECLARATIVE_COMPONENT_CARD_TYPE ? (
        <div className="pointer-events-none h-full overflow-hidden p-3">
          <TrustedComponentRenderer data={snapshot ?? {}} fallbackId={card.card_id} interactive={false} />
        </div>
      ) : svg ? (
        <img alt={card.title} className="h-full w-full object-contain p-3" draggable={false} src={svg} />
      ) : cover ? (
        <iframe
          aria-hidden="true"
          className="pointer-events-none absolute top-0 left-0 border-0"
          loading="lazy"
          referrerPolicy="no-referrer"
          sandbox=""
          scrolling="no"
          srcDoc={cover}
          style={{ width: COVER_WIDTH, height: COVER_HEIGHT, transform: `scale(${scale})`, transformOrigin: "top left" }}
          tabIndex={-1}
          title={`${card.title} 封面预览`}
        />
      ) : (
        <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center text-muted-foreground">
          {runtimeOnly ? <Code2 className="size-5" /> : <FileText className="size-5" />}
          <span className="text-xs">{runtimeOnly ? "启动应用后查看内容" : "暂无预览内容"}</span>
        </div>
      )}
    </div>
  );
}

export { ArtifactPreview as CardInlinePreview };
