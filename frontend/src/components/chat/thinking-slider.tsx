import { useEffect, useRef } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { haptic } from "@/lib/native-bridge";

/** 手机端思考力度滑动条档位：极速（无工具/无思考）+ 低/中/高/极高（智能体 + 思考力度）。 */
export const THINKING_STOPS = [
  { label: "极速", thinking: "off", responseMode: "fast" },
  { label: "低", thinking: "low", responseMode: "agentic" },
  { label: "中", thinking: "medium", responseMode: "agentic" },
  { label: "高", thinking: "high", responseMode: "agentic" },
  { label: "极高", thinking: "xhigh", responseMode: "agentic" },
] as const;

/** 根据当前 responseMode + thinkingMode 推导滑动条档位索引。 */
export function thinkingStopIndex(
  responseMode: string,
  thinkingMode: string,
): number {
  if (responseMode === "fast") return 0;
  const index = THINKING_STOPS.findIndex(
    (stop) => stop.responseMode === "agentic" && stop.thinking === thinkingMode,
  );
  return index >= 0 ? index : 0;
}

export function ThinkingSlider({
  value,
  onChange,
  disabled,
}: {
  value: number;
  onChange: (index: number) => void;
  disabled?: boolean;
}) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const lastIndexRef = useRef(value);
  const draggingRef = useRef(false);

  // Keep the deduplication baseline aligned when the selected mode changes
  // outside the slider (for example, from the model menu).
  useEffect(() => {
    lastIndexRef.current = value;
  }, [value]);

  const indexFromEvent = (clientX: number): number => {
    const el = trackRef.current;
    if (!el) return value;
    const rect = el.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    return Math.round(ratio * (THINKING_STOPS.length - 1));
  };

  const commit = (index: number) => {
    if (index !== lastIndexRef.current) {
      lastIndexRef.current = index;
      haptic(1);
      onChange(index);
    }
  };

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (disabled) return;
    draggingRef.current = true;
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // ignore
    }
    commit(indexFromEvent(event.clientX));
  };
  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    // Pointer movement while merely hovering must not change the selection.
    // Movement becomes active only after a press starts a drag gesture.
    if (disabled || !draggingRef.current) return;
    commit(indexFromEvent(event.clientX));
  };

  const stopDragging = () => {
    draggingRef.current = false;
  };

  const pct = (value / (THINKING_STOPS.length - 1)) * 100;

  return (
    <div className="thinking-slider">
      <div
        aria-valuemax={THINKING_STOPS.length - 1}
        aria-valuemin={0}
        aria-valuenow={value}
        className="thinking-slider__track"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={stopDragging}
        onPointerCancel={stopDragging}
        onLostPointerCapture={stopDragging}
        role="slider"
        ref={trackRef}
      >
        <div className="thinking-slider__fill" style={{ width: `${pct}%` }} />
        <div className="thinking-slider__dots">
          {THINKING_STOPS.map((stop) => (
            <span className="thinking-slider__dot" key={stop.label} />
          ))}
        </div>
        <div className="thinking-slider__thumb" style={{ left: `${pct}%` }} />
      </div>
      <div className="thinking-slider__labels">
        {THINKING_STOPS.map((stop, index) => (
          <span className={index === value ? "is-active" : ""} key={stop.label}>
            {stop.label}
          </span>
        ))}
      </div>
    </div>
  );
}
