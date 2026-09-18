import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { RotateCcw, X, ZoomIn, ZoomOut } from "lucide-react";

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";

/**
 * 共享图片灯箱：整屏透明层 + 0.5×–5× 缩放。
 *
 * 结构（DOM、CSS 与手势的分工都按这个来）：
 * - `.chat-image-lightbox`（DialogContent）：铺满视口的透明层，只负责"占住整屏"。
 * - `__surface`：真正的手势面，滚轮/拖动/双指的监听都在这一层，所以**光标不用
 *   正好压在那张小图上**也能缩放（小图、100% 未放大的时候尤其重要）。
 * - `__stage`：图片外面那圈圆角+阴影的"卡片"，缩放/平移的 transform 打在这里，
 *   **外框与图是一个刚体**——缩小时不会留下一个不变的大空框。
 * - `__toolbar`：贴在层底部居中，永远不会被内容顶出视口。
 *
 * 数字口径：
 * - `100%` = 适配：图片按 contain 填进可用框（`min(92vw,1500px) × 82svh`），
 *   小图会被拉大填满（否则小截图看着"太小"），大图会被缩到看得全。
 * - 缩放 0.5×–5×（相对适配尺寸）；工具条按钮 1.25 倍率；滚轮=缩放（以光标为锚点）；
 *   双指=缩放+中点平移；单击两下=还原；按住拖动=平移。
 *
 * 双指必须在这里自己用 pointer 事件实现：App 壳把 WebView 自带缩放关掉了
 * （`WebAppScreen.kt` / `MainActivity.kt` 的 `setSupportZoom(false)`），
 * 页面级 pinch 不生效，指望浏览器缩放会把整页一起放大。
 *
 * 滚轮监听走原生非 passive 注册：React 在根节点上给 `onWheel` 挂的是 passive
 * 监听，`preventDefault()` 无效，拦不住弹窗背后的页面滚动。
 */

/** 缩放下限/上限：0.5×–5×（用户口径），相对"适配尺寸"。 */
const MIN_SCALE = 0.5;
const MAX_SCALE = 5;
/** 工具条每次「放大/缩小」的倍率。 */
const ZOOM_FACTOR = 1.25;
/** 滚轮灵敏度：每 100px 约 25% 缩放。 */
const WHEEL_SENSITIVITY = 0.0022;
/** 适配框：宽 min(vw×0.92, 1500px)，高 vh×0.82（与 CSS 的 92vw / 82svh 一致）。 */
const FRAME_WIDTH_RATIO = 0.92;
const FRAME_MAX_WIDTH = 1500;
const FRAME_HEIGHT_RATIO = 0.82;
/** 适配时最多放大的倍数：小图要能填满框，但太小的小图别糊成一团。 */
const FIT_MAX_UPSCALE = 4;
/** 双击（鼠标双击或触屏两次轻点）还原的判定窗口与位移容差。 */
const DOUBLE_TAP_MS = 320;
const TAP_SLOP = 24;

type Point = { x: number; y: number };

/** 双指手势起点快照：距离、中点与当时的变换。 */
type PinchState = {
  distance: number;
  midpoint: Point;
  scale: number;
  offset: Point;
};

/** 单指/鼠标拖动状态：起点、起点时的平移量、累计位移（用于区分点击与拖动）。 */
type DragState = {
  pointerId: number;
  origin: Point;
  offset: Point;
  moved: number;
};

function clampScale(value: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));
}

function distanceBetween(a: Point, b: Point): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function midpointOf(a: Point, b: Point): Point {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

function frameSize(): { width: number; height: number } {
  return {
    width: Math.min(
      window.innerWidth * FRAME_WIDTH_RATIO,
      FRAME_MAX_WIDTH,
    ),
    height: window.innerHeight * FRAME_HEIGHT_RATIO,
  };
}

export function ImageLightbox({
  actions,
  alt,
  dialogTitle,
  onOpenChange,
  open,
  src,
}: {
  /** 工具条里的额外动作（如「下载图片」），排在「关闭」之前。 */
  actions?: ReactNode;
  /** 图片替代文本。 */
  alt: string;
  /** 无障碍标题（视觉隐藏），说明这一层在预览什么。 */
  dialogTitle: string;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  src: string;
}) {
  const imageRef = useRef<HTMLImageElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  // Radix 的 DialogContent 会晚一拍才挂上节点：只用 ref 的话首次 effect 里
  // 还是 null，而依赖不变就不会再跑，滚轮监听会永远绑不上。
  // 用回调 ref 把节点同步进 state，让监听随节点就位。
  const [surfaceEl, setSurfaceEl] = useState<HTMLDivElement | null>(null);
  const bindSurface = useCallback((node: HTMLDivElement | null) => {
    setSurfaceEl(node);
  }, []);

  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState<Point>({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  /** 适配尺寸（100% 时的显示像素）；图片加载完才知道原图多大。 */
  const [fit, setFit] = useState<{ width: number; height: number } | null>(null);

  // 指针事件里必须读到最新变换，但不能把监听绑在每次 state 变化上：state 与 ref 同步写。
  const scaleRef = useRef(1);
  const offsetRef = useRef<Point>({ x: 0, y: 0 });
  const pointersRef = useRef(new Map<number, Point>());
  const pinchRef = useRef<PinchState | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const lastTapRef = useRef<{ at: number; point: Point } | null>(null);

  /**
   * 平移上限：卡片就是图片的显示盒，所以放大后每个方向只能多出「超出量的一半」，
   * 到边即止，不会把图拖出可视区。
   */
  const offsetBounds = useCallback((nextScale: number): Point => {
    const stage = stageRef.current;
    if (!stage) return { x: 0, y: 0 };
    const width = stage.offsetWidth;
    const height = stage.offsetHeight;
    return {
      x: Math.max(0, (width * nextScale - width) / 2),
      y: Math.max(0, (height * nextScale - height) / 2),
    };
  }, []);

  const apply = useCallback(
    (nextScale: number, nextOffset: Point) => {
      const bounds = offsetBounds(nextScale);
      const clamped = {
        x: Math.min(bounds.x, Math.max(-bounds.x, nextOffset.x)),
        y: Math.min(bounds.y, Math.max(-bounds.y, nextOffset.y)),
      };
      scaleRef.current = nextScale;
      offsetRef.current = clamped;
      setScale(nextScale);
      setOffset(clamped);
    },
    [offsetBounds],
  );

  const reset = useCallback(() => apply(1, { x: 0, y: 0 }), [apply]);

  /** 按原图比例算适配尺寸：小图拉大填满可用框（上限 FIT_MAX_UPSCALE），大图缩到全看得见。 */
  const measureFit = useCallback(() => {
    const image = imageRef.current;
    if (!image) return;
    const naturalWidth = image.naturalWidth;
    const naturalHeight = image.naturalHeight;
    if (!naturalWidth || !naturalHeight) return;
    const frame = frameSize();
    const ratio = Math.min(
      frame.width / naturalWidth,
      frame.height / naturalHeight,
      FIT_MAX_UPSCALE,
    );
    setFit({ width: naturalWidth * ratio, height: naturalHeight * ratio });
  }, []);

  /**
   * 以某个屏幕点为锚缩放（不传锚点则围绕中心）：锚点下的像素保持不动，
   * 滚轮/双指才不会"越缩越跑"。
   */
  const zoomAround = useCallback(
    (factor: number, clientPoint?: Point) => {
      const current = scaleRef.current;
      const next = clampScale(current * factor);
      if (next === current) return;
      let anchor: Point = { x: 0, y: 0 };
      const stage = stageRef.current;
      if (clientPoint && stage) {
        const rect = stage.getBoundingClientRect();
        anchor = {
          x: clientPoint.x - (rect.left + rect.width / 2),
          y: clientPoint.y - (rect.top + rect.height / 2),
        };
      }
      const from = offsetRef.current;
      const ratio = next / current;
      apply(next, {
        x: anchor.x - (anchor.x - from.x) * ratio,
        y: anchor.y - (anchor.y - from.y) * ratio,
      });
    },
    [apply],
  );

  // 打开（或换图）回到适配状态：关掉再打开不会残留上一张的缩放与平移。
  useEffect(() => {
    if (!open) return;
    pointersRef.current.clear();
    pinchRef.current = null;
    dragRef.current = null;
    lastTapRef.current = null;
    setDragging(false);
    scaleRef.current = 1;
    offsetRef.current = { x: 0, y: 0 };
    setScale(1);
    setOffset({ x: 0, y: 0 });
    // 图片可能已在缓存里，onLoad 不一定再触发：这里补一次测量。
    measureFit();
    const onResize = () => measureFit();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [measureFit, open, src]);

  // 适配尺寸变了（首帧/旋转/改窗口）之后重算一次夹取，避免留下越界位移。
  useLayoutEffect(() => {
    if (!open) return;
    apply(scaleRef.current, offsetRef.current);
  }, [apply, fit, open]);

  // 滚轮缩放（原生非 passive）：监听挂在整屏手势面上，光标在哪都能缩。
  useEffect(() => {
    if (!open || !surfaceEl) return;
    const surface = surfaceEl;
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault();
      // deltaMode: 0=像素 1=行 2=页，先归一化再换算倍率。
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 100 : 1;
      const factor = Math.exp(-event.deltaY * unit * WHEEL_SENSITIVITY);
      zoomAround(factor, { x: event.clientX, y: event.clientY });
    };
    surface.addEventListener("wheel", handleWheel, { passive: false });
    return () => surface.removeEventListener("wheel", handleWheel);
  }, [open, surfaceEl, zoomAround]);

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    // 工具条按钮上的按下不算手势，否则连点「放大」会被当成双击还原。
    if ((event.target as HTMLElement | null)?.closest?.("button")) return;
    const surface = surfaceEl;
    surface?.setPointerCapture?.(event.pointerId);
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });

    if (pointersRef.current.size === 2) {
      const [first, second] = [...pointersRef.current.values()];
      pinchRef.current = {
        distance: distanceBetween(first, second),
        midpoint: midpointOf(first, second),
        scale: scaleRef.current,
        offset: offsetRef.current,
      };
      dragRef.current = null;
      setDragging(true);
      return;
    }
    if (pointersRef.current.size > 2) return;
    dragRef.current = {
      pointerId: event.pointerId,
      origin: { x: event.clientX, y: event.clientY },
      offset: offsetRef.current,
      moved: 0,
    };
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!pointersRef.current.has(event.pointerId)) return;
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const points = [...pointersRef.current.values()];

    if (points.length >= 2 && pinchRef.current) {
      const pinch = pinchRef.current;
      const [first, second] = points;
      const spread = distanceBetween(first, second);
      const next = clampScale(pinch.scale * (spread / (pinch.distance || 1)));
      const center = midpointOf(first, second);
      const stage = stageRef.current;
      if (!stage) return;
      const rect = stage.getBoundingClientRect();
      const toLocal = (point: Point): Point => ({
        x: point.x - (rect.left + rect.width / 2),
        y: point.y - (rect.top + rect.height / 2),
      });
      // 双指中点下的像素保持跟随：起始中点 m0 与当前中点 m1 共同决定平移量。
      const m0 = toLocal(pinch.midpoint);
      const m1 = toLocal(center);
      const k = next / pinch.scale;
      apply(next, {
        x: m1.x - k * (m0.x - pinch.offset.x),
        y: m1.y - k * (m0.y - pinch.offset.y),
      });
      return;
    }

    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const dx = event.clientX - drag.origin.x;
    const dy = event.clientY - drag.origin.y;
    drag.moved = Math.max(drag.moved, Math.hypot(dx, dy));
    if (drag.moved > 2) setDragging(true);
    apply(scaleRef.current, { x: drag.offset.x + dx, y: drag.offset.y + dy });
  };

  const handlePointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const tracked = pointersRef.current.delete(event.pointerId);
    surfaceEl?.releasePointerCapture?.(event.pointerId);
    if (pointersRef.current.size < 2) pinchRef.current = null;

    const drag = dragRef.current;
    if (drag && drag.pointerId === event.pointerId) {
      dragRef.current = null;
      // 双击还原：两次轻点（位置接近、间隔够短）且当前有缩放时才生效。
      const isTap = tracked && drag.moved <= TAP_SLOP;
      if (isTap && scaleRef.current !== 1) {
        const now = Date.now();
        const point = { x: event.clientX, y: event.clientY };
        const last = lastTapRef.current;
        if (last && now - last.at <= DOUBLE_TAP_MS && distanceBetween(last.point, point) <= TAP_SLOP) {
          lastTapRef.current = null;
          reset();
        } else {
          lastTapRef.current = { at: now, point };
        }
      } else {
        lastTapRef.current = null;
      }
    }
    if (pointersRef.current.size === 0) setDragging(false);
  };

  const zoomsIn = scale < MAX_SCALE - 1e-6;
  const zoomsOut = scale > MIN_SCALE + 1e-6;
  const hasTransform =
    scale !== 1 || Math.round(offset.x) !== 0 || Math.round(offset.y) !== 0;

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent
        aria-describedby={undefined}
        className="chat-image-lightbox"
        showCloseButton={false}
      >
        <DialogTitle className="sr-only">{dialogTitle}</DialogTitle>
        <div
          className="chat-image-lightbox__surface"
          onPointerCancel={handlePointerUp}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          ref={bindSurface}
        >
          <div
            className={`chat-image-lightbox__stage${scale > 1 ? " is-zoomed" : ""}${
              dragging ? " is-dragging" : ""
            }`}
            ref={stageRef}
            style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})` }}
          >
            <img
              alt={alt}
              className="chat-image-lightbox__image"
              draggable={false}
              onDragStart={(event) => event.preventDefault()}
              onLoad={measureFit}
              ref={imageRef}
              src={src}
              style={fit ? { height: fit.height, width: fit.width } : undefined}
            />
          </div>
          <div className="chat-image-lightbox__toolbar">
            <button
              aria-label="缩小图片"
              disabled={!zoomsOut}
              onClick={() => zoomAround(1 / ZOOM_FACTOR)}
              title="缩小（滚轮下滚 / 双指捏合）"
              type="button"
            >
              <ZoomOut className="size-4" />
            </button>
            <span
              aria-label={`当前缩放 ${Math.round(scale * 100)}%`}
              className="chat-image-lightbox__percent"
            >
              {Math.round(scale * 100)}%
            </span>
            <button
              aria-label="放大图片"
              disabled={!zoomsIn}
              onClick={() => zoomAround(ZOOM_FACTOR)}
              title="放大（滚轮上滚 / 双指张开）"
              type="button"
            >
              <ZoomIn className="size-4" />
            </button>
            <button
              aria-label="重置缩放"
              disabled={!hasTransform}
              onClick={reset}
              title="重置（双击图片也可还原）"
              type="button"
            >
              <RotateCcw className="size-4" />
              重置
            </button>
            {actions}
            <DialogClose asChild>
              <button type="button">
                <X className="size-4" />
                关闭
              </button>
            </DialogClose>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
