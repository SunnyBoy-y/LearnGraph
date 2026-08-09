import { useEffect, useRef, useState, type ReactNode } from "react";
import { Maximize2, Minimize2 } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Wraps a sandboxed iframe preview with a top-right fullscreen toggle.
 *
 * The fullscreen request is applied to this wrapper (not the iframe itself) so
 * the exit button remains visible and clickable while the preview is expanded.
 */
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

  useEffect(() => {
    const handleChange = () => {
      setIsFullscreen(document.fullscreenElement === wrapperRef.current);
    };
    document.addEventListener("fullscreenchange", handleChange);
    return () => document.removeEventListener("fullscreenchange", handleChange);
  }, []);

  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement === wrapperRef.current) {
        await document.exitFullscreen();
      } else {
        await wrapperRef.current?.requestFullscreen();
      }
    } catch {
      // Fullscreen can be rejected by browser policy; keep the inline preview usable.
    }
  }

  return (
    <>
      <style>{`
        .fullscreen-preview:fullscreen { display: flex; }
        .fullscreen-preview:fullscreen > iframe { height: 100%; min-height: 100%; flex: 1; border-radius: 0; }
      `}</style>
      <div
        className={`fullscreen-preview relative ${className ?? ""}`}
        ref={wrapperRef}
      >
        {children}
        <Button
          aria-label={isFullscreen ? `退出 ${label} 全屏` : `全屏预览 ${label}`}
          className="absolute right-2 top-2 z-20"
          onClick={() => void toggleFullscreen()}
          size="icon-sm"
          type="button"
          variant="ghost"
        >
          {isFullscreen ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
        </Button>
      </div>
    </>
  );
}
