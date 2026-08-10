import { useEffect, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";

import type { SandboxBootstrapJob } from "@/types/control";
import { cn } from "@/lib/utils";

/**
 * Live sandbox image build progress.
 *
 * The backend's `progress_percent` is authoritative, but inside a long Docker
 * step (e.g. `RUN pip install`) it can sit still for minutes while the daemon
 * emits nothing parseable. This component therefore animates between polls:
 *
 * - the backend percent is always the floor — the bar never regresses;
 * - new log lines (`log_seq` deltas) from the backend stream push the bar
 *   forward: the more logs arrive, the more it moves;
 * - a guaranteed time creep (fast right after a phase starts, decaying but
 *   never zero) keeps the bar moving even while Docker is silent;
 * - each phase has a display cap slightly above its real end, so the
 *   simulation bridges gaps without overshooting the next phase;
 * - an ETA is derived from a smoothed display rate and shown next to the
 *   percent.
 */

/** Per-phase display caps. Unknown phases cap at 90. */
const PHASE_CAP: Record<string, number> = {
  queued: 4,
  detect_docker: 14,
  pull_runner: 68,
  build_runner: 68,
  resolve_digest: 72,
  pin_digest: 72,
  smoke_test: 95,
  persist_runtime: 98,
};
const UNKNOWN_PHASE_CAP = 90;

const TICK_MS = 400;
/** Slowest guaranteed motion (percent per second): the bar never freezes. */
const MIN_CREEP = 0.08;
/** Extra motion at the very start of a phase (decays with phase age). */
const EARLY_CREEP = 0.55;
/** Time constant (seconds) for the early-phase speed boost. */
const CREEP_DECAY_S = 70;
/** Each freshly appended backend log line pushes the bar this many percent. */
const LOG_BOOST_PER_LINE = 0.5;
/** Per-tick cap so a log burst cannot blow past the phase ceiling. */
const MAX_LOG_BOOST = 8;

function formatEta(seconds: number): string {
  if (seconds < 60) return `剩余约 ${Math.max(1, Math.ceil(seconds))} 秒`;
  return `剩余约 ${Math.max(1, Math.ceil(seconds / 60))} 分钟`;
}

function useSmoothedBootstrapProgress(job: SandboxBootstrapJob | null) {
  const [displayed, setDisplayed] = useState(() => job?.progress_percent ?? 0);
  const [etaSeconds, setEtaSeconds] = useState<number | null>(null);

  const jobRef = useRef(job);
  jobRef.current = job;

  const stateRef = useRef({
    jobId: "",
    phase: "",
    phaseStartedAt: 0,
    sim: 0,
    prevValue: 0,
    seenLogSeq: 0,
    lastTickAt: 0,
    rateEma: 0,
    rateSamples: 0,
  });

  // Reset the simulation whenever the job (or its phase) changes.
  useEffect(() => {
    const state = stateRef.current;
    const nextId = job?.job_id ?? "";
    if (nextId !== state.jobId) {
      state.jobId = nextId;
      state.phase = job?.phase ?? "";
      state.phaseStartedAt = performance.now();
      state.sim = job?.progress_percent ?? 0;
      state.prevValue = job?.progress_percent ?? 0;
      state.seenLogSeq = job?.log_seq ?? 0;
      state.lastTickAt = 0;
      state.rateEma = 0;
      state.rateSamples = 0;
      setDisplayed(job?.progress_percent ?? 0);
      setEtaSeconds(null);
    } else if (job && job.phase !== state.phase) {
      state.phase = job.phase;
      state.phaseStartedAt = performance.now();
    }
  }, [job]);

  const running = job?.status === "running";

  useEffect(() => {
    if (!running) {
      const current = jobRef.current;
      if (current) setDisplayed(current.progress_percent);
      return;
    }
    const timer = window.setInterval(() => {
      const state = stateRef.current;
      const current = jobRef.current;
      if (!current || current.status !== "running") return;

      const now = performance.now();
      const dt = state.lastTickAt
        ? Math.max(0.05, Math.min(5, (now - state.lastTickAt) / 1000))
        : TICK_MS / 1000;
      state.lastTickAt = now;

      const backend = Math.max(0, Math.min(100, current.progress_percent));

      // Motion driven by the backend log stream: every new line pushes the bar.
      const logSeq = current.log_seq ?? 0;
      const logDelta = Math.max(0, logSeq - state.seenLogSeq);
      state.seenLogSeq = logSeq;
      const logBoost = Math.min(MAX_LOG_BOOST, logDelta * LOG_BOOST_PER_LINE);

      // Front-loaded time creep: fast at phase start, decaying but never zero.
      const phaseAge = Math.max(0, (now - state.phaseStartedAt) / 1000);
      const creep =
        MIN_CREEP +
        Math.max(0, EARLY_CREEP - MIN_CREEP) * Math.exp(-phaseAge / CREEP_DECAY_S);

      const cap = PHASE_CAP[current.phase] ?? UNKNOWN_PHASE_CAP;
      const prevValue = state.prevValue;
      state.sim = Math.min(cap, state.sim + (logBoost + creep) * dt);
      const value = Math.min(cap, Math.max(backend, state.sim));
      state.prevValue = value;
      setDisplayed(value);

      // Smoothed display rate → remaining time estimate.
      const instantRate = Math.max(0, (value - prevValue) / dt);
      state.rateEma =
        state.rateSamples === 0
          ? instantRate
          : 0.7 * state.rateEma + 0.3 * instantRate;
      state.rateSamples += 1;
      if (state.rateSamples >= 2) {
        const clampedRate = Math.max(0.05, Math.min(4, state.rateEma));
        setEtaSeconds(Math.min(2400, Math.max(5, (100 - value) / clampedRate)));
      }
    }, TICK_MS);
    return () => window.clearInterval(timer);
  }, [running]);

  return { displayed, etaSeconds };
}

/**
 * Live sandbox image build progress.
 *
 * While a bootstrap job runs it shows a spinning loader, the real-time detail
 * the backend derives from the Docker build stream (e.g. "正在下载镜像
 * ubuntu:24.04 · 15.1 MB / 28.7 MB" or "正在构建 · 步骤 3/8：RUN pip install"),
 * the phase description, an animated striped progress bar that keeps moving
 * between polls (driven by the backend log stream plus a decaying time creep),
 * and an estimated remaining time.
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
  const { displayed, etaSeconds } = useSmoothedBootstrapProgress(job);
  if (!job) return null;

  const running = job.status === "running";
  const percent = Math.round(displayed);
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
            "h-full rounded-full transition-[width] duration-300 ease-out",
            isAmber ? "bg-amber-600 dark:bg-amber-400" : "bg-primary",
            running && "sandbox-progress-stripes",
          )}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}
