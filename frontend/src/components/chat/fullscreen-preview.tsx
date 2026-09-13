import { useEffect, useRef, useState, type ReactNode } from "react";
import { Maximize2, Minimize2, Pause, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { usePreviewPause } from "@/lib/preview-pause-controller";

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
 * The top layer is what makes the expansion work inside the artifact card wall:
 * those cards set `content-visibility: auto`, which implies layout + paint
 * containment and therefore becomes the containing block for `position: fixed`
 * descendants. Measured in Chromium, a plain fixed wrapper there lands at the
 * card's offsets and is clipped by the card instead of filling the canvas —
 * while `getComputedStyle(card).contain` still reports "none", so the regression
 * is invisible to ancestor sniffing. Top-layer elements are laid out against the
 * viewport and painted outside every ancestor clip, containment included.
 *
 * When the top layer is unavailable, when no center canvas exists, or when the
 * applied rect does not actually land on the canvas, the expand falls back to
 * the native fullscreen API so the toggle never appears dead.
 */

/** Expanded canvas hugs the middle-column edges exactly (no inset). */
const CANVAS_INSET = 0;

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
  const { paused, toggle: togglePlayback } = usePreviewPause(wrapperRef);

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
  // rect, and keep it glued while the layout resizes or any ancestor scrolls.
  useEffect(() => {
    if (!expanded) return;
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    const canvas = findMiddleCanvas(wrapper);
    let observer: ResizeObserver | null = null;
    let inTopLayer = false;
    const apply = () => {
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      wrapper.style.left = `${rect.left + CANVAS_INSET}px`;
      wrapper.style.top = `${rect.top + CANVAS_INSET}px`;
      wrapper.style.width = `${Math.max(0, rect.width - CANVAS_INSET * 2)}px`;
      wrapper.style.height = `${Math.max(0, rect.height - CANVAS_INSET * 2)}px`;
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
      const wrapperRect = wrapper.getBoundingClientRect();
      const canvasRect = canvas.getBoundingClientRect();
      const aligned =
        Math.abs(wrapperRect.left - canvasRect.left) <= 2 &&
        Math.abs(wrapperRect.top - canvasRect.top) <= 2 &&
        Math.abs(wrapperRect.width - canvasRect.width) <= 2;
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
          onClick={togglePlayback}
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
