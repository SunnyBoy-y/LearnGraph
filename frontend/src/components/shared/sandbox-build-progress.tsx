import { useEffect, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";

import type { SandboxBootstrapJob } from "@/types/control";
import { cn } from "@/lib/utils";

/**
 * Live sandbox image build progress.
 *
 * The bar always shows the backend's real `progress_percent`. The backend
 * itself keeps that value moving during long Docker steps (a time creep that
 * is fast right after a phase starts, plus a boost per new log line), so the
 * value is real, monotonic and survives page refreshes — refreshing never
 * makes the bar jump back. The remaining time is estimated from the smoothed
 * rate of the real percent samples.
 */

const ETA_SAMPLE_MS = 1000;
const ETA_MAX_SECONDS = 2400; // 40 min
const ETA_MIN_SECONDS = 5;

function formatEta(seconds: number): string {
  if (seconds < 60) return `剩余约 ${Math.max(1, Math.ceil(seconds))} 秒`;
  return `剩余约 ${Math.max(1, Math.ceil(seconds / 60))} 分钟`;
}

/** Estimate the remaining time from real `progress_percent` samples. */
function useBootstrapEta(job: SandboxBootstrapJob | null) {
  const [etaSeconds, setEtaSeconds] = useState<number | null>(null);

  const jobRef = useRef(job);
  jobRef.current = job;

  const stateRef = useRef({
    jobId: "",
    prevPct: 0,
    prevAt: 0,
    rateEma: 0,
    samples: 0,
  });

  useEffect(() => {
    const state = stateRef.current;
    const nextId = job?.job_id ?? "";
    if (nextId !== state.jobId) {
      state.jobId = nextId;
      state.prevPct = job?.progress_percent ?? 0;
      state.prevAt = performance.now();
      state.rateEma = 0;
      state.samples = 0;
      setEtaSeconds(null);
    }
  }, [job]);

  const running = job?.status === "running";

  useEffect(() => {
    if (!running) {
      setEtaSeconds(null);
      return;
    }
    const timer = window.setInterval(() => {
      const current = jobRef.current;
      if (!current || current.status !== "running") return;
      const state = stateRef.current;
      const pct = current.progress_percent;
      const now = performance.now();
      if (pct === state.prevPct) return;
      const dt = (now - state.prevAt) / 1000;
      if (dt <= 0 || dt > 60) {
        // Re-base after a long gap (e.g. tab was hidden) instead of a bogus rate.
        state.prevPct = pct;
        state.prevAt = now;
        return;
      }
      const rate = Math.max(0, (pct - state.prevPct) / dt);
      state.rateEma =
        state.samples === 0 ? rate : 0.7 * state.rateEma + 0.3 * rate;
      state.samples += 1;
      state.prevPct = pct;
      state.prevAt = now;
      if (state.samples >= 2) {
        const clampedRate = Math.max(0.05, Math.min(4, state.rateEma));
        setEtaSeconds(
          Math.min(
            ETA_MAX_SECONDS,
            Math.max(ETA_MIN_SECONDS, (100 - pct) / clampedRate),
          ),
        );
      }
    }, ETA_SAMPLE_MS);
    return () => window.clearInterval(timer);
  }, [running]);

  return etaSeconds;
}

/**
 * Live sandbox image build progress.
 *
 * While a bootstrap job runs it shows a spinning loader, the real-time detail
 * the backend derives from the Docker build stream (e.g. "正在下载镜像
 * ubuntu:24.04 · 15.1 MB / 28.7 MB" or "正在构建 · 步骤 3/8：RUN pip install"),
 * the phase description, an animated striped progress bar fed by the backend's
 * real percent, and an estimated remaining time.
 */
export function SandboxBuildProgress({
  job,
  tone = "default",
  className,
}: {
  job: SandboxBootstrapJob | null;
  tone?: "default" | "amber";
  className?: string;
}) {
  const etaSeconds = useBootstrapEta(job);
  if (!job) return null;

  const running = job.status === "running";
  const percent = Math.round(Math.max(0, Math.min(100, job.progress_percent)));
  const detail = job.detail || job.message;
  const isAmber = tone === "amber";

  return (
    <div className={cn("w-full space-y-2", className)}>
      <div className="flex items-center gap-2">
        {running ? (
          <LoaderCircle
            aria-hidden="true"
            className="size-3.5 shrink-0 animate-spin"
          />
        ) : (
          <span
            className={cn(
              "size-1.5 shrink-0 rounded-full",
              job.status === "succeeded" ? "bg-emerald-500" : "bg-red-500",
            )}
          />
        )}
        <p
          className="min-w-0 flex-1 truncate text-sm font-medium"
          title={detail}
        >
          {detail}
        </p>
        {running ? (
          <span className="shrink-0 text-right font-mono text-xs tabular-nums opacity-70">
            {percent}%
            {etaSeconds !== null ? (
              <span className="ml-2 inline-block text-[10px] opacity-80">
                {formatEta(etaSeconds)}
              </span>
            ) : null}
          </span>
        ) : null}
      </div>
      {job.message && job.message !== detail ? (
        <p className="text-[11px] leading-4 text-muted-foreground">
          {job.message}
        </p>
      ) : null}
      <div
        className={cn(
          "h-2 overflow-hidden rounded-full",
          isAmber ? "bg-amber-200 dark:bg-amber-900" : "bg-muted",
        )}
      >
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-700 ease-out",
            isAmber ? "bg-amber-600 dark:bg-amber-400" : "bg-primary",
            running && "sandbox-progress-stripes",
          )}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}
