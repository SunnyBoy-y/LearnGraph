import { useEffect, useRef, useState, type ReactNode } from "react";
import { Maximize2, Minimize2, Pause, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { usePreviewPause, usePreviewPauseScope } from "@/lib/preview-pause-controller";

/**
 * Wraps a sandboxed iframe preview with a top-right expand toggle.
 *
 * Expanding fills the middle canvas of the app's left/center/right shell
 * layout: the wrapper is promoted to the browser top layer (Popover API) and
 * pinned (position: fixed) to the live rect of the center column, re-glued on
 * resize/scroll, so the side panels stay visible and untouched — no popup, no
 * backdrop. The iframe DOM node is never moved or remounted, so sub-app
 * channels and heartbeats survive. Inside the canvas the iframe uses the
 * default fluid layout (width/height 100%, no fixed pixel height), so the
 * preview adapts to the enlarged area without ratio distortion.
 *
 * The pinned rect is `canvas ∩ viewport`, not the raw canvas rect. On the
 * desktop shell the canvas IS the scroll container (height 100dvh,
 * `overflow: hidden`) so both are identical. On phones — and inside the native
 * app shell at any width — `.workspace-main` degrades to a document-height
 * block (`display: block; min-height: 100dvh; overflow-y: visible`, see
 * index.css) and the document scrolls instead. There the raw canvas rect is the
 * whole page: pinning to it put the wrapper at document coordinates, so the
 * viewport showed a slice far below the card's content (blank) while the
 * right-2/top-2 exit button sat above the screen, and the overlay even slid
 * along with the page. Intersecting with the viewport keeps the desktop
 * behaviour byte-identical and collapses to the visible viewport on phones.
 *
 * The top layer is what makes the expansion work inside the artifact card wall
 * and inside Radix dialogs: it lays the wrapper out against the viewport and
 * paints it outside every ancestor clip, so an ancestor containment or transform
 * — which would otherwise become the containing block for `position: fixed`
 * descendants — cannot capture it. That trap is invisible to ancestor sniffing
 * because `getComputedStyle(node).contain` still reports "none".
 *
 * When the top layer is unavailable, when no center canvas exists, or when the
 * applied rect does not actually land on the pinned target, the expand falls
 * back to the native fullscreen API so the toggle never appears dead.
 */

/** Expanded canvas hugs the middle-column edges exactly (no inset). */
const CANVAS_INSET = 0;

/**
 * Rect the expanded wrapper is pinned to: the center canvas clipped to the
 * viewport. Identical to the canvas rect on the desktop shell (the canvas is the
 * scroll container and equals the viewport); on phones, where the canvas grows
 * to document height and the document itself scrolls, it collapses to the
 * visible viewport instead of the whole page.
 */
function pinnedTargetRect(canvas: HTMLElement): {
  left: number;
  top: number;
  width: number;
  height: number;
} {
  const rect = canvas.getBoundingClientRect();
  const left = Math.max(rect.left, 0);
  const top = Math.max(rect.top, 0);
  const right = Math.min(rect.right, window.innerWidth);
  const bottom = Math.min(rect.bottom, window.innerHeight);
  return {
    left,
    top,
    width: Math.max(0, right - left),
    height: Math.max(0, bottom - top),
  };
}

/** True when some ancestor traps fixed positioning (transform/translate/…). */
function hasFixedContainingBlockAncestor(element: HTMLElement | null): boolean {
  const extended = (style: CSSStyleDeclaration) =>
    style as CSSStyleDeclaration & { translate?: string; rotate?: string; scale?: string };
  for (let node = element?.parentElement ?? null; node; node = node.parentElement) {
    const style = extended(getComputedStyle(node));
    const willChange = style.willChange || "auto";
    if (
      style.transform !== "none" ||
      (style.translate ?? "none") !== "none" ||
      (style.rotate ?? "none") !== "none" ||
      (style.scale ?? "none") !== "none" ||
      style.perspective !== "none" ||
      style.filter !== "none" ||
      (willChange !== "auto" && /transform|perspective/i.test(willChange)) ||
      style.contain.includes("paint") ||
      style.contain.includes("layout") ||
      style.contain === "strict" ||
      style.contain === "content"
    ) {
      return true;
    }
  }
  return false;
}

/** The app's middle canvas column: the chat canvas, else the workspace main. */
function findMiddleCanvas(element: HTMLElement | null): HTMLElement | null {
  let node = element?.parentElement ?? null;
  while (node) {
    if (
      node.classList.contains("chat-canvas-page") ||
      node.classList.contains("workspace-main")
    ) {
      return node;
    }
    node = node.parentElement;
  }
  return null;
}

/** True when this element can be promoted to the browser top layer. */
function supportsTopLayer(element: HTMLElement | null): boolean {
  return Boolean(element && typeof element.showPopover === "function");
}

export function FullscreenPreview({
  children,
  className,
  label,
}: {
  children: ReactNode;
  className?: string;
  label: string;
}) {
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const scope = usePreviewPauseScope();
  const { paused, pause, resume } = usePreviewPause(wrapperRef);
  // The group callbacks change identity whenever the group re-renders (a new
  // `suspended` flag), but the effects below must run on their own signals only —
  // read the callbacks through refs so a group re-render cannot bounce the pin
  // state (false → true) and flicker playback.
  const activateRef = useRef<(() => void) | null>(null);
  const pinRef = useRef<((pinned: boolean) => void) | null>(null);
  useEffect(() => {
    activateRef.current = scope?.activate ?? null;
    pinRef.current = scope?.pin ?? null;
  });

  // Tracks the native-fullscreen fallback path (transformed-ancestor traps).
  useEffect(() => {
    const handleChange = () => {
      setIsFullscreen(document.fullscreenElement === wrapperRef.current);
    };
    document.addEventListener("fullscreenchange", handleChange);
    return () => document.removeEventListener("fullscreenchange", handleChange);
  }, []);

  // Center-canvas expansion: promote the wrapper to the top layer (so ancestor
  // containment or transforms cannot capture it), pin it to the middle canvas
  // rect clipped to the viewport, and keep it glued while the layout resizes or
  // any ancestor scrolls.
  useEffect(() => {
    if (!expanded) return;
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    const canvas = findMiddleCanvas(wrapper);
    let observer: ResizeObserver | null = null;
    let inTopLayer = false;
    const apply = () => {
      if (!canvas) return;
      const target = pinnedTargetRect(canvas);
      wrapper.style.left = `${target.left + CANVAS_INSET}px`;
      wrapper.style.top = `${target.top + CANVAS_INSET}px`;
      wrapper.style.width = `${Math.max(0, target.width - CANVAS_INSET * 2)}px`;
      wrapper.style.height = `${Math.max(0, target.height - CANVAS_INSET * 2)}px`;
    };
    if (canvas && supportsTopLayer(wrapper)) {
      try {
        wrapper.showPopover();
        inTopLayer = true;
      } catch {
        // Not a popover (attribute not committed) or already open: keep the
        // plain fixed-position path below.
      }
    }
    apply();
    if (canvas) {
      const target = pinnedTargetRect(canvas);
      const wrapperRect = wrapper.getBoundingClientRect();
      const aligned =
        Math.abs(wrapperRect.left - target.left) <= 2 &&
        Math.abs(wrapperRect.top - target.top) <= 2 &&
        Math.abs(wrapperRect.width - target.width) <= 2;
      if (!aligned && !inTopLayer) {
        // A containing-block trap we cannot detect from computed styles: hand
        // over to the native fullscreen API (which escapes the DOM clip) and
        // keep the plain placement only as a last resort.
        void wrapper.requestFullscreen().catch(() => undefined);
      }
      observer = new ResizeObserver(apply);
      observer.observe(canvas);
    }
    window.addEventListener("resize", apply);
    window.addEventListener("scroll", apply, true);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", apply);
      window.removeEventListener("scroll", apply, true);
      window.removeEventListener("keydown", onKeyDown);
      if (typeof wrapper.hidePopover === "function") {
        try {
          wrapper.hidePopover();
        } catch {
          // Already closed (e.g. the attribute was removed with the state flag).
        }
      }
      wrapper.style.removeProperty("left");
      wrapper.style.removeProperty("top");
      wrapper.style.removeProperty("width");
      wrapper.style.removeProperty("height");
    };
  }, [expanded]);

  async function toggleExpand() {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    if (expanded || isFullscreen) {
      if (isFullscreen) {
        try {
          await document.exitFullscreen();
        } catch {
          // Fullscreen exit can be rejected by browser policy.
        }
      }
      setExpanded(false);
      return;
    }
    // The top layer escapes both containment and transformed ancestors, so the
    // in-canvas expansion is preferred whenever a middle canvas exists.
    if (findMiddleCanvas(wrapper) && supportsTopLayer(wrapper)) {
      setExpanded(true);
      return;
    }
    // No top layer and trapped by a transformed ancestor (e.g. inside the card
    // preview dialog) or no center canvas in scope: fall back to native
    // fullscreen, which still fills the whole viewport.
    if (
      hasFixedContainingBlockAncestor(wrapper) ||
      !findMiddleCanvas(wrapper)
    ) {
      try {
        await wrapper.requestFullscreen();
        return;
      } catch {
        // Rejected (browser policy); fall through to the in-canvas expansion.
      }
    }
    setExpanded(true);
  }

  const active = expanded || isFullscreen;

  // An expanded preview owns the canvas, so it must survive the card wall's
  // viewport band (the card's own box can scroll away while the top-layer
  // overlay keeps covering the screen) and must become the group's playing
  // preview — otherwise "expand" would open a frozen overlay.
  useEffect(() => {
    if (!active) {
      pinRef.current?.(false);
      return;
    }
    pinRef.current?.(true);
    activateRef.current?.();
  }, [active]);

  // A preview that unmounts must not leave its group pinned forever.
  useEffect(() => () => pinRef.current?.(false), []);

  return (
    <>
      <style>{`
        .fullscreen-preview:fullscreen { display: flex; flex-direction: column; }
        .fullscreen-preview:fullscreen > iframe { width: 100% !important; height: 100% !important; min-height: 100% !important; flex: 1; border: 0; border-radius: 0; }
        .fullscreen-preview--expanded {
          position: fixed;
          inset: 0;
          z-index: 999;
          display: flex;
          flex-direction: column;
          min-width: 0;
          min-height: 0;
          overflow: hidden;
          background: var(--background);
        }
        /* Top layer (popover): drop the UA popover chrome so the wrapper is a
           plain fixed rectangle glued to the canvas. */
        .fullscreen-preview--expanded[popover] {
          margin: 0;
          border: 0;
          padding: 0;
          max-width: none;
          max-height: none;
          color: inherit;
          background: var(--background);
        }
        .fullscreen-preview--expanded > iframe {
          width: 100% !important;
          height: 100% !important;
          min-height: 0 !important;
          flex: 1;
          border: 0;
          border-radius: 0;
        }
      `}</style>
      <div
        className={`fullscreen-preview ${expanded ? "fullscreen-preview--expanded" : "relative"} ${className ?? ""}`}
        popover={expanded ? "manual" : undefined}
        ref={wrapperRef}
      >
        {children}
        <Button
          aria-label={active ? `退出 ${label} 放大预览` : `放大预览 ${label}`}
          className="absolute right-2 top-2 z-20"
          data-preview-control=""
          onClick={() => void toggleExpand()}
          size="icon-sm"
          type="button"
          variant="ghost"
        >
          {active ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
        </Button>
        <Button
          aria-label={paused ? `继续播放 ${label}` : `暂停播放 ${label}`}
          className="absolute right-11 top-2 z-20"
          data-preview-control=""
          onClick={() => {
            if (!paused) {
              pause();
              return;
            }
            // Play always means "this preview": it also claims playback from a
            // group that currently holds every other preview paused.
            activateRef.current?.();
            resume();
          }}
          size="icon-sm"
          type="button"
          variant="ghost"
        >
          {paused ? <Play className="size-4" /> : <Pause className="size-4" />}
        </Button>
      </div>
    </>
  );
}
