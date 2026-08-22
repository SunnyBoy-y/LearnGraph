import { useEffect, useMemo, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Pause,
  Play,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

export type StepperSlotValue = string | number | null;

export type StepperStep = {
  caption: string;
  highlight?: number[];
  slot_values?: StepperSlotValue[];
  annotations?: string[];
  note?: string;
};

export type StepperProps = {
  title: string;
  description?: string;
  controls?: "manual" | "auto";
  interval_ms?: number;
  slot_labels?: string[];
  slots?: StepperSlotValue[];
  steps: StepperStep[];
};

function cellText(value: StepperSlotValue | undefined): string {
  if (value === null || value === undefined) return "·";
  return String(value);
}

function StepperCard({
  title,
  description,
  controls = "manual",
  interval_ms = 1_000,
  slot_labels = [],
  slots = [],
  steps,
}: StepperProps) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);

  const stepCount = steps.length;
  const step = steps[Math.min(index, stepCount - 1)];
  const finalStep = index >= stepCount - 1;

  useEffect(() => {
    if (!playing || finalStep) {
      if (playing && finalStep) setPlaying(false);
      return;
    }
    const timer = window.setInterval(
      () => setIndex((prev) => Math.min(prev + 1, stepCount - 1)),
      Math.min(Math.max(interval_ms, 300), 60_000),
    );
    return () => window.clearInterval(timer);
  }, [playing, finalStep, interval_ms, stepCount]);

  const highlightSet = useMemo(
    () => new Set(step?.highlight ?? []),
    [step],
  );

  const displayedSlots = step?.slot_values ?? slots;

  const renderSlots = () => {
    const count = Math.max(displayedSlots.length, slot_labels.length, 1);
    const cells = Array.from({ length: count }, (_, i) => ({ i }));
    return (
      <div
        className="stepper-slots"
        style={{ display: "grid", gridTemplateColumns: `repeat(${count}, minmax(36px, 1fr))`, gap: 6 }}
      >
        {cells.map(({ i }) => (
          <div key={i} className="flex flex-col items-center gap-1">
            <span className="text-[10px] leading-none text-muted-foreground">
              {slot_labels[i] ?? String(i)}
            </span>
            <div
              className={`flex h-9 w-full items-center justify-center rounded-md border text-sm tabular-nums ${
                highlightSet.has(i) ? "border-primary bg-primary/15 text-primary" : "bg-background"
              }`}
            >
              {cellText(displayedSlots[i])}
            </div>
            {step?.annotations?.[i] ? (
              <span className="line-clamp-1 max-w-full text-[10px] leading-tight text-destructive">
                {step.annotations[i]}
              </span>
            ) : null}
          </div>
        ))}
      </div>
    );
  };

  const jump = (next: number) => setIndex(Math.min(Math.max(next, 0), stepCount - 1));

  return (
    <section className="message-component" aria-label={title}>
      <div className="message-component__heading">
        <ShieldCheck className="size-4" />
        <div>
          <strong>{title}</strong>
          {description ? (
            <span className="text-muted-foreground">{description}</span>
          ) : null}
        </div>
        <Badge variant="secondary">步进器 {index + 1}/{stepCount}</Badge>
      </div>

      {stepCount > 0 ? (
        <div className="space-y-3">
          {renderSlots()}

          <div className="stepper-caption rounded-md bg-muted/60 px-3 py-2 text-sm">
            <span className="mr-2 font-semibold text-muted-foreground">Step {index + 1}</span>
            {step?.caption}
            {step?.note ? <div className="mt-0.5 text-muted-foreground">{step.note}</div> : null}
          </div>

          <div className="flex items-center gap-1.5">
            {controls === "auto" ? (
              <Button
                aria-label={playing ? "暂停" : "播放"}
                onClick={() => setPlaying((p) => !p)}
                size="sm"
                variant="outline"
              >
                {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
              </Button>
            ) : null}
            <Button
              aria-label="上一步"
              disabled={index === 0}
              onClick={() => jump(index - 1)}
              size="sm"
              variant="outline"
            >
              <ChevronLeft className="size-4" />
            </Button>
            <Button
              aria-label="下一步"
              disabled={finalStep}
              onClick={() => jump(index + 1)}
              size="sm"
              variant="outline"
            >
              <ChevronRight className="size-4" />
            </Button>
            <Button
              aria-label="重置"
              disabled={index === 0 && !playing}
              onClick={() => {
                setPlaying(false);
                jump(0);
              }}
              size="sm"
              variant="ghost"
            >
              <RotateCcw className="size-4" />
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-muted-foreground">该步进器尚未包含步骤。</p>
      )}
    </section>
  );
}

export { StepperCard };