import { useEffect, useRef } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { haptic } from "@/lib/native-bridge";

/** Product order; each consumer supplies the current model's supported subset. */
export const THINKING_STOPS = [
  { label: "极速", thinking: "off", responseMode: "fast" },
  { label: "低", thinking: "low", responseMode: "agentic" },
  { label: "中", thinking: "medium", responseMode: "agentic" },
  { label: "高", thinking: "high", responseMode: "agentic" },
  { label: "极高", thinking: "xhigh", responseMode: "agentic" },
  { label: "最高", thinking: "max", responseMode: "agentic" },
] as const;
export type ThinkingStop = (typeof THINKING_STOPS)[number];

export function supportedThinkingStops(modes: readonly string[], thinkingRequired = false): ThinkingStop[] {
  return THINKING_STOPS.filter((stop) => stop.thinking === "off" ? !thinkingRequired : modes.includes(stop.thinking));
}

export interface EffortStop {
  label: string;
  thinking: ThinkingStop["thinking"];
}

/** Effort-only stops (used when response mode is configured separately). */
export function thinkingEffortStops(modes: readonly string[], thinkingRequired = false): EffortStop[] {
  return THINKING_STOPS
    .filter((stop) => stop.thinking === "off" ? !thinkingRequired : modes.includes(stop.thinking))
    .map((stop) => ({
      thinking: stop.thinking,
      label: stop.thinking === "off" ? "关闭" : stop.label,
    }));
}

export function thinkingStopIndex(responseMode: string, thinkingMode: string, stops: readonly { thinking: string }[] = THINKING_STOPS): number {
  const mode = responseMode === "fast" ? "off" : thinkingMode;
  return Math.max(0, stops.findIndex((stop) => stop.thinking === mode));
}

export function ThinkingSlider({ value, onChange, disabled, stops = THINKING_STOPS }: {
  value: number;
  onChange: (index: number) => void;
  disabled?: boolean;
  stops?: readonly { label: string; thinking: string }[];
}) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const lastIndexRef = useRef(value);
  const draggingRef = useRef(false);
  const max = Math.max(0, stops.length - 1);
  const current = Math.max(0, Math.min(max, value));
  const inactive = disabled || stops.length <= 1;
  const stopSignature = stops.map(stop => stop.thinking).join(",");
  useEffect(() => { lastIndexRef.current = current; }, [current]);
  useEffect(() => { draggingRef.current = false; }, [stopSignature]);
  const indexFromEvent = (clientX: number): number => {
    const el = trackRef.current;
    if (!el || !el.getBoundingClientRect().width) return current;
    const rect = el.getBoundingClientRect();
    return Math.round(Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)) * max);
  };
  const commit = (index: number) => {
    if (inactive) return;
    const next = Math.max(0, Math.min(max, index));
    if (next !== lastIndexRef.current) { lastIndexRef.current = next; haptic(1); onChange(next); }
  };
  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (inactive) return;
    draggingRef.current = true;
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* unavailable in some webviews */ }
    commit(indexFromEvent(event.clientX));
  };
  const stopDragging = () => { draggingRef.current = false; };
  if (!stops.length) return null;
  const pct = max ? (current / max) * 100 : 0;
  return (
    <div className="thinking-slider">
      <div
        aria-label="思考强度" aria-disabled={inactive} aria-valuemax={max} aria-valuemin={0}
        aria-valuenow={current} aria-valuetext={stops[current]?.label}
        className="thinking-slider__track" role="slider" tabIndex={inactive ? -1 : 0} ref={trackRef}
        onPointerDown={handlePointerDown}
        onPointerMove={(event) => { if (!inactive && draggingRef.current) commit(indexFromEvent(event.clientX)); }}
        onPointerUp={stopDragging} onPointerCancel={stopDragging} onLostPointerCapture={stopDragging}
        onKeyDown={(event) => {
          const next = event.key === "Home" ? 0 : event.key === "End" ? max
            : ["ArrowRight", "ArrowUp"].includes(event.key) ? current + 1
            : ["ArrowLeft", "ArrowDown"].includes(event.key) ? current - 1 : null;
          if (next !== null) { event.preventDefault(); event.stopPropagation(); commit(next); }
        }}
      >
        <div className="thinking-slider__fill" style={{ width: `${pct}%` }} />
        <div className="thinking-slider__dots">{stops.map((stop) => <span className="thinking-slider__dot" key={stop.thinking} />)}</div>
        <div className="thinking-slider__thumb" style={{ left: `${pct}%` }} />
      </div>
      <div className="thinking-slider__labels">{stops.map((stop, index) => (
        <button
          type="button"
          disabled={inactive}
          className={`thinking-slider__label ${index === current ? "is-active" : ""}`}
          key={stop.thinking}
          onClick={() => commit(index)}
          onPointerDown={(event) => event.stopPropagation()}
        >
          {stop.label}
        </button>
      ))}</div>
    </div>
  );
}
