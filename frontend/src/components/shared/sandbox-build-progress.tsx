import { LoaderCircle } from "lucide-react";

import type { SandboxBootstrapJob } from "@/types/control";
import { cn } from "@/lib/utils";

/**
 * Live sandbox image build progress.
 *
 * While a bootstrap job runs it shows a spinning loader, the real-time detail
 * the backend derives from the Docker build stream (e.g. "正在下载镜像
 * ubuntu:24.04 · 15.1 MB / 28.7 MB" or "正在构建 · 步骤 3/8：RUN pip install"),
 * the phase description, and an animated striped progress bar.
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
  if (!job) return null;

  const running = job.status === "running";
  const percent = Math.max(0, Math.min(100, Math.round(job.progress_percent)));
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
          <span className="shrink-0 font-mono text-xs tabular-nums opacity-70">
            {percent}%
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
