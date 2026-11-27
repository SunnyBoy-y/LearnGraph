import { useMemo } from "react";

import {
  ThinkingSlider,
  thinkingEffortStops,
  thinkingStopIndex,
} from "@/components/chat/thinking-slider";
import { thinkingModeDescription } from "@/lib/generation-config";
import type { ResponseMode, ThinkingMode } from "@/lib/session-composer-prefs";
import { cn } from "@/lib/utils";

export interface ReasoningSelectorProps {
  responseMode: ResponseMode;
  thinkingMode: ThinkingMode;
  /** Supported reasoning efforts declared by the current model (excluding "off"). */
  thinkingModes: readonly ThinkingMode[];
  thinkingRequired?: boolean;
  onChange: (mode: ThinkingMode) => void;
  disabled?: boolean;
  showTitle?: boolean;
  className?: string;
}

export function ReasoningSelector({
  responseMode,
  thinkingMode,
  thinkingModes,
  thinkingRequired = false,
  onChange,
  disabled = false,
  showTitle = true,
  className,
}: ReasoningSelectorProps) {
  const nonOffModes = useMemo(
    () => thinkingModes.filter((mode) => mode !== "off"),
    [thinkingModes],
  );

  const stops = useMemo(
    () =>
      nonOffModes.length > 0
        ? thinkingEffortStops(nonOffModes, thinkingRequired)
        : [],
    [nonOffModes, thinkingRequired],
  );

  const activeIndex = useMemo(
    () => thinkingStopIndex(responseMode, thinkingMode, stops),
    [responseMode, thinkingMode, stops],
  );

  const activeMode =
    (stops[activeIndex]?.thinking as ThinkingMode | undefined) ?? "off";
  const isDisabled = disabled || (responseMode === "fast" && !thinkingRequired);

  if (!stops.length) {
    return (
      <div
        className={cn("space-y-1", className)}
        data-slot="reasoning-selector"
      >
        {showTitle ? (
          <p className="text-xs font-medium text-foreground">思考强度</p>
        ) : null}
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          当前模型未声明思考强度配置，将按服务商默认执行。
        </p>
      </div>
    );
  }

  return (
    <div
      className={cn("space-y-2", className)}
      data-slot="reasoning-selector"
    >
      {showTitle ? (
        <p className="text-xs font-medium text-foreground">思考强度</p>
      ) : null}
      <ThinkingSlider
        stops={stops}
        value={activeIndex}
        disabled={isDisabled}
        onChange={(index) => {
          const stop = stops[index];
          if (stop) onChange(stop.thinking as ThinkingMode);
        }}
      />
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        {isDisabled
          ? "极速模式下思考已关闭，切换响应模式后可调整思考强度。"
          : thinkingModeDescription(activeMode)}
      </p>
    </div>
  );
}
