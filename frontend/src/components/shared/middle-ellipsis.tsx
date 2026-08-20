import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

/**
 * 中间截断 + 长按复制的文本（手机窄屏会话 ID 用）：
 *  - 一行放不下时保留头尾、中间省略（…）
 *  - 长按 / 右键复制完整文本
 *  - user-select: none 防止与系统长按选择冲突
 */
export function MiddleEllipsis({
  text,
  className,
}: {
  text: string;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const [display, setDisplay] = useState(text);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const measure = (str: string): number => {
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d");
      if (!ctx) return str.length;
      ctx.font = window.getComputedStyle(el).font;
      return ctx.measureText(str).width;
    };

    const compute = () => {
      const available = el.clientWidth;
      if (available <= 0) return;
      const full = measure(text);
      if (full <= available) {
        setDisplay(text);
        return;
      }
      // 中间截断：尾部固定保留约 25%，二分查找头部最长可显示长度
      const ellipsis = "…";
      const tailLen = Math.max(4, Math.floor(text.length * 0.25));
      const total = text.length;
      const tail = text.slice(total - tailLen);
      let lo = 4;
      let hi = total - tailLen;
      let head = 4;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        const w = measure(text.slice(0, mid) + ellipsis + tail);
        if (w <= available) {
          head = mid;
          lo = mid + 1;
        } else {
          hi = mid - 1;
        }
      }
      if (head <= 4) {
        setDisplay(ellipsis + tail);
      } else {
        setDisplay(text.slice(0, head) + ellipsis + tail);
      }
    };

    compute();
    const ro = new ResizeObserver(compute);
    ro.observe(el);
    return () => ro.disconnect();
  }, [text]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("会话 ID 已复制");
    } catch {
      toast.error("复制失败");
    }
  };

  return (
    <span
      ref={ref}
      className={className}
      title={text}
      style={{ userSelect: "none", WebkitUserSelect: "none" }}
      onContextMenu={(event) => {
        event.preventDefault();
        void copy();
      }}
      onPointerDown={(event) => {
        const startX = event.clientX;
        const startY = event.clientY;
        const timer = window.setTimeout(() => void copy(), 600);
        const cleanup = () => {
          window.clearTimeout(timer);
          window.removeEventListener("pointerup", onUp);
          window.removeEventListener("pointercancel", onUp);
          window.removeEventListener("pointermove", onMove);
        };
        const onUp = () => cleanup();
        const onMove = (me: PointerEvent) => {
          if (
            Math.abs(me.clientX - startX) > 8 ||
            Math.abs(me.clientY - startY) > 8
          ) {
            cleanup();
          }
        };
        window.addEventListener("pointerup", onUp, { once: true });
        window.addEventListener("pointercancel", onUp, { once: true });
        window.addEventListener("pointermove", onMove);
      }}
    >
      {display}
    </span>
  );
}
