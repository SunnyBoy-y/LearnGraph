import { useEffect, useRef, useState, type ReactNode } from "react";
import { Maximize2, Minimize2 } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Wraps a sandboxed iframe preview with a top-right expand toggle.
 *
 * Expanding fills the middle canvas of the app's left/center/right shell
 * layout: the wrapper is pinned (position: fixed) to the live rect of the
 * center column and re-glued on resize/scroll, so the side panels stay
 * visible and untouched — no popup, no backdrop. The iframe DOM node is never
 * moved or remounted, so sub-app channels and heartbeats survive. Inside the
 * canvas the iframe uses the default fluid layout (width/height 100%, no fixed
 * pixel height), so the preview adapts to the enlarged area without ratio
 * distortion.
 *
 * When fixed positioning cannot escape a transformed ancestor (e.g. the card
 * preview dialog), or no center canvas exists, the expand falls back to the
 * native fullscreen API so the toggle never appears dead.
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

  // Tracks the native-fullscreen fallback path (transformed-ancestor traps).
  useEffect(() => {
    const handleChange = () => {
      setIsFullscreen(document.fullscreenElement === wrapperRef.current);
    };
    document.addEventListener("fullscreenchange", handleChange);
    return () => document.removeEventListener("fullscreenchange", handleChange);
  }, []);

  // Center-canvas expansion: pin the wrapper to the middle canvas rect and
  // keep it glued while the layout resizes or any ancestor scrolls.
  useEffect(() => {
    if (!expanded) return;
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    const canvas = findMiddleCanvas(wrapper);
    let observer: ResizeObserver | null = null;
    const apply = () => {
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      wrapper.style.left = `${rect.left + CANVAS_INSET}px`;
      wrapper.style.top = `${rect.top + CANVAS_INSET}px`;
      wrapper.style.width = `${Math.max(0, rect.width - CANVAS_INSET * 2)}px`;
      wrapper.style.height = `${Math.max(0, rect.height - CANVAS_INSET * 2)}px`;
    };
    apply();
    if (canvas) {
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
    // Trapped by a transformed ancestor (e.g. inside the card preview dialog)
    // or no center canvas in scope: fall back to the native fullscreen API.
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
        .fullscreen-preview:fullscreen { display: flex; }
        .fullscreen-preview:fullscreen > iframe { height: 100%; min-height: 100%; flex: 1; border-radius: 0; }
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
      </div>
    </>
  );
}
