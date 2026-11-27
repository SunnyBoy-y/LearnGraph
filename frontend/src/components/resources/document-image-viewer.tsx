import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircle, Maximize2, Minus, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useFileStreamUrl } from "@/hooks/use-file-stream-url";
import { cn } from "@/lib/utils";

const ZOOM_MIN = 0.25;
const ZOOM_MAX = 5;
const ZOOM_STEP = 0.25;
const STAGE_PADDING = 16;

function clampZoom(value: number) {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(value * 100) / 100));
}

/**
 * 学习工作台的图片查看区。
 *
 * 图片是这一屏的视觉中心：按真实宽高比铺满可用空间，超出时滚动查看，缩放通过
 * 底部一条轻量工具栏完成。这里刻意不套卡片、不加阴影——外层容器已经给足了边界。
 * 宽窄屏切换只改变外层布局，本组件保持挂载，因此缩放状态不会丢。
 */
export function DocumentImageViewer({
  alt,
  className,
  fileId,
  url = "",
}: {
  alt: string;
  className?: string;
  fileId?: string;
  url?: string;
}) {
  const stageRef = useRef<HTMLDivElement>(null);
  const { source: streamed, failed, loading } = useFileStreamUrl(url ? undefined : fileId);
  const source = url || streamed;
  const [stage, setStage] = useState({ width: 0, height: 0 });
  const [natural, setNatural] = useState({ width: 0, height: 0 });
  const [zoom, setZoom] = useState(1);
  const [decodeError, setDecodeError] = useState(false);

  useEffect(() => {
    const node = stageRef.current;
    if (!node) return;
    const measure = () => {
      const width = Math.max(node.clientWidth - STAGE_PADDING * 2, 0);
      const height = Math.max(node.clientHeight - STAGE_PADDING * 2, 0);
      setStage((current) =>
        current.width === width && current.height === height
          ? current
          : { width, height },
      );
    };
    measure();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const fitted = useMemo(() => {
    if (!natural.width || !natural.height || !stage.width || !stage.height) {
      return undefined;
    }
    const ratio = natural.width / natural.height;
    return ratio >= stage.width / stage.height
      ? { width: stage.width, height: stage.width / ratio }
      : { width: stage.height * ratio, height: stage.height };
  }, [natural, stage]);

  const toggleFullscreen = useCallback(() => {
    const node = stageRef.current;
    if (!node) return;
    const target = node.parentElement ?? node;
    if (document.fullscreenElement) {
      void document.exitFullscreen?.().catch(() => undefined);
      return;
    }
    void target.requestFullscreen?.().catch(() => undefined);
  }, []);

  if (failed || decodeError) {
    return (
      <div className={cn("file-image-viewer file-image-viewer--error", className)} role="alert">
        无法读取这张图片，可下载后查看。
      </div>
    );
  }
  if (!source) {
    return (
      <div className={cn("file-image-viewer file-image-viewer--loading", className)} role="status">
        <LoaderCircle className="size-4 animate-spin" />
        {loading ? "正在读取图片…" : "暂无预览"}
      </div>
    );
  }

  return (
    <div className={cn("file-image-viewer", className)}>
      <div className="file-image-stage" data-fitted={fitted ? "true" : "false"} ref={stageRef}>
        <img
          alt={alt}
          className="file-image-stage__image"
          draggable={false}
          onError={() => setDecodeError(true)}
          onLoad={(event) => {
            const image = event.currentTarget;
            setNatural({ width: image.naturalWidth, height: image.naturalHeight });
          }}
          src={source}
          style={fitted ? { width: fitted.width * zoom, height: fitted.height * zoom } : undefined}
        />
      </div>
      <div aria-label="图片查看工具" className="file-image-tools" role="toolbar">
        <Button
          aria-label="缩小"
          disabled={zoom <= ZOOM_MIN}
          onClick={() => setZoom((current) => clampZoom(current - ZOOM_STEP))}
          size="icon-sm"
          variant="ghost"
        >
          <Minus className="size-4" />
        </Button>
        <span aria-live="polite" className="file-image-tools__zoom tabular-nums">
          {Math.round(zoom * 100)}%
        </span>
        <Button
          aria-label="放大"
          disabled={zoom >= ZOOM_MAX}
          onClick={() => setZoom((current) => clampZoom(current + ZOOM_STEP))}
          size="icon-sm"
          variant="ghost"
        >
          <Plus className="size-4" />
        </Button>
        <Button
          aria-label="适应窗口"
          aria-pressed={zoom === 1}
          onClick={() => setZoom(1)}
          size="sm"
          variant="ghost"
        >
          适应窗口
        </Button>
        <Button aria-label="全屏查看" onClick={toggleFullscreen} size="icon-sm" variant="ghost">
          <Maximize2 className="size-4" />
        </Button>
      </div>
    </div>
  );
}
