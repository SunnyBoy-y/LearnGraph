import { useState } from "react";
import { ChevronDown, ChevronRight, LoaderCircle, RefreshCw } from "lucide-react";

import {
  formatBackoffDuration,
  formatRetryCategory,
  type ProviderRetryAttempt,
} from "@/features/chat/retry-attempts";

export function RetryDetailsBlock({
  attempts,
  isActive,
}: {
  attempts: readonly ProviderRetryAttempt[];
  isActive: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  if (!attempts.length) return null;

  const latest = attempts[attempts.length - 1];
  const retryCount = attempts.length;
  const maxRetries = latest.maxRetries;
  const backoff = formatBackoffDuration(latest.backoffMs);
  const categoryLabel = formatRetryCategory(latest.errorCategory);
  const showingLiveWait = isActive && latest.phase !== "exhausted";

  const headline = showingLiveWait
    ? latest.phase === "started"
      ? `正在执行第 ${latest.attemptNo} 次尝试${maxRetries ? `（共允许重试 ${maxRetries} 次）` : ""}`
      : `上游${categoryLabel}，${backoff ? `等待 ${backoff} 后` : "即将"}进行第 ${latest.attemptNo} 次尝试${maxRetries ? `（最多重试 ${maxRetries} 次）` : ""}`
    : latest.phase === "exhausted"
      ? `自动重试已达上限（共尝试 ${latest.attemptNo} 次）`
      : `本次回答共经历 ${retryCount} 次自动重试`;

  return (
    <div
      className="my-2 rounded-lg border border-border/70 bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
      data-retry-details-block="true"
      data-retry-live={showingLiveWait ? "true" : "false"}
      role={showingLiveWait ? "status" : undefined}
      aria-live={showingLiveWait ? "polite" : undefined}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 text-left text-xs font-medium text-foreground/85 hover:text-foreground"
        aria-expanded={expanded}
        onClick={() => setExpanded((current) => !current)}
      >
        {showingLiveWait ? (
          <LoaderCircle className="size-3.5 shrink-0 animate-spin text-amber-600 dark:text-amber-400" />
        ) : (
          <RefreshCw className="size-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1 truncate">{headline}</span>
        {expanded ? (
          <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
        )}
      </button>

      {expanded ? (
        <ol className="mt-2 space-y-1.5 border-t border-border/60 pt-2">
          {attempts.map((attempt, index) => {
            const waitText = formatBackoffDuration(attempt.backoffMs);
            return (
              <li
                key={`${attempt.attemptNo}-${index}`}
                className="flex flex-col gap-0.5 rounded bg-background/60 px-2 py-1"
              >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] font-medium text-foreground/80">
                  <span>第 {attempt.attemptNo} 次尝试</span>
                  <span>·</span>
                  <span>{formatRetryCategory(attempt.errorCategory)}</span>
                  {waitText ? (
                    <>
                      <span>·</span>
                      <span>退避 {waitText}</span>
                    </>
                  ) : null}
                  {attempt.phase === "exhausted" ? (
                    <span className="text-destructive">（已达重试上限）</span>
                  ) : null}
                </div>
                {attempt.errorMessage ? (
                  <p className="break-all font-mono text-[11px] text-muted-foreground">
                    {attempt.errorMessage}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : null}
    </div>
  );
}
