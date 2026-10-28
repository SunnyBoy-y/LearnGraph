import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Play } from "lucide-react";

import { getArtifactCardPreview } from "@/api/artifacts";
import { MagicCardHost } from "@/components/chat/magic-card-host";
import { TrustedComponentRenderer } from "@/components/chat/trusted-component-renderer";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { DECLARATIVE_COMPONENT_CARD_TYPE } from "@/features/artifacts/card-types";
import {
  PreviewPauseScopeContext,
  type PreviewPauseScope,
} from "@/lib/preview-pause-controller";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { ArtifactCard } from "@/types/artifacts";

/**
 * The artifact article is the card frame.  Its preview viewport must only
 * provide sizing and clipping for the running content; giving it a second
 * border made every artifact look like a card inside another card.
 */
export const artifactCardPreviewViewportClassName =
  "artifact-card-preview-viewport relative min-h-80 max-h-[520px] min-w-0 overflow-hidden";

/** Cards keep their own controls: clicks on them must not be read as "play". */
function isCardControl(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    Boolean(
      target.closest(
        'button, a, input, select, textarea, [role="button"], [data-preview-control]',
      ),
    )
  );
}

/**
 * The shared card-wall shell used by both the draft card wall and the share
 * management wall.  The two pages provide different actions/metadata, but the
 * article, preview position, playback click handling, and metadata placement
 * stay identical.
 */
export function ArtifactCardShell({
  active,
  ariaLabel,
  card,
  footer,
  meta,
  metaAriaLabel = "卡片信息",
  onActivate,
  selected = false,
  version,
  workspaceId,
}: {
  active: boolean;
  ariaLabel?: string;
  card: ArtifactCard;
  footer: ReactNode;
  meta: ReactNode;
  metaAriaLabel?: string;
  onActivate: (cardId: string) => void;
  selected?: boolean;
  /** When set, render the immutable published snapshot instead of the draft. */
  version?: number;
  workspaceId: string;
}) {
  return (
    <div className="artifact-card-item flex min-w-0 flex-col">
      <article
        aria-label={ariaLabel ?? card.title}
        className={`flex min-w-0 flex-col rounded-xl border bg-background p-0 ${selected ? "border-ring bg-muted/40" : ""}`}
        onClick={(event) => {
          if (isCardControl(event.target)) return;
          onActivate(card.id);
        }}
        style={{ contentVisibility: "auto", containIntrinsicSize: "520px" }}
      >
        {/* The preview owns the visible title. Keep an accessible name without
            adding a second title/status header above the preview. */}
        <span aria-hidden="true" className="sr-only">
          {card.title}
        </span>
        <CardInlinePreview
          active={active}
          card={card}
          onActivate={onActivate}
          version={version}
          workspaceId={workspaceId}
        />
        <div className="mx-3 mt-3 flex min-w-0 flex-wrap items-center gap-1 border-t pb-3 pt-2">
          {footer}
        </div>
      </article>
      <div
        aria-label={metaAriaLabel}
        className="artifact-card-meta flex min-w-0 flex-wrap items-center gap-1.5 px-1.5 pt-2"
        role="group"
      >
        {meta}
      </div>
    </div>
  );
}

export function CardInlinePreview({
  active,
  card,
  onActivate,
  version,
  workspaceId,
}: {
  active: boolean;
  card: ArtifactCard;
  onActivate: (cardId: string) => void;
  /** When set, render the immutable published snapshot instead of the draft. */
  version?: number;
  workspaceId: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  // True while this preview is expanded (top layer) or natively fullscreen: the
  // expanded overlay keeps covering the canvas even when the card's own box has
  // been scrolled out of the lazy-mount band, so it must stay mounted and running.
  const [pinned, setPinned] = useState(false);

  // Keep only nearby previews running so long libraries do not start dozens
  // of iframe runtimes at once. React Query retains the fetched snapshots.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => setVisible(entry.isIntersecting),
      { rootMargin: "480px" },
    );
    observer.observe(container);
    return () => observer.disconnect();
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

  const activate = useCallback(() => onActivate(card.id), [card.id, onActivate]);
  // Off-viewport previews are suspended by the scope instead of being unmounted:
  // unmounting throws the iframe away, so scrolling back restarts the card from
  // zero. Only a preview that was never activated and left the band is still
  // reclaimed, which keeps the number of live iframes bounded.
  const live = visible || active || pinned;
  const suspended = !pinned && (!active || !visible);
  const scope = useMemo<PreviewPauseScope>(
    () => ({ suspended, activate, pin: setPinned }),
    [suspended, activate],
  );
  const showPreview = live && !preview.isPending && !preview.isError;

  return (
    <div
      className={artifactCardPreviewViewportClassName}
      data-artifact-preview-viewport="true"
      data-preview-suspended={suspended ? "true" : "false"}
      ref={containerRef}
    >
      {!live || preview.isPending ? (
        <Skeleton aria-label={`正在加载 ${card.title} 的预览`} className="h-80 w-full" />
      ) : preview.isError ? (
        <div className="flex h-80 flex-col items-center justify-center gap-3 p-4 text-center">
          <p className="text-sm text-destructive">{preview.error.message || "加载预览失败"}</p>
          <Button
            onClick={() => void preview.refetch()}
            size="sm"
            type="button"
            variant="outline"
          >
            重新加载预览
          </Button>
        </div>
      ) : (
        <PreviewPauseScopeContext.Provider value={scope}>
          {card.card_type === DECLARATIVE_COMPONENT_CARD_TYPE ? (
            /* 组件卡已不再进入本页列表（见 card-types.ts），这里保留渲染分支：
               将来若恢复入口，或接口在别处返回组件卡，预览能力不丢。 */
            <div className="max-h-[520px] overflow-auto p-4">
              <TrustedComponentRenderer
                data={preview.data.preview_snapshot}
                fallbackId={card.card_id}
                interactive={false}
              />
            </div>
          ) : (
            <MagicCardHost
              key={`${card.updated_at}-${version ?? "draft"}`}
              data={preview.data.preview_snapshot}
            />
          )}
        </PreviewPauseScopeContext.Provider>
      )}
      {showPreview && suspended ? (
        /* A paused card must say so and offer the one action it accepts. Without
           this the wall looks broken: frozen mid-animation with no explanation. */
        <button
          className="absolute inset-0 z-30 flex cursor-pointer items-center justify-center bg-background/45 transition-colors hover:bg-background/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          onClick={activate}
          title={`播放 ${card.title}`}
          type="button"
        >
          <span className="flex items-center gap-1.5 rounded-full border border-border bg-background/90 px-3 py-1.5 text-xs font-medium shadow-sm">
            <Play className="size-3.5" />
            点击播放
          </span>
        </button>
      ) : null}
    </div>
  );
}
