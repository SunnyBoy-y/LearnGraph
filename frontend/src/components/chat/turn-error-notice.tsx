import { useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Minimize2,
  RefreshCcw,
  Settings,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  classifyTurnError,
  type StructuredTurnErrorInput,
} from "@/features/chat/turn-error";

export function TurnErrorNotice({
  error,
  onRetry,
  onOpenProviderSettings,
  onCompactAndRetry,
  busy = false,
  retryDisabled = false,
  compactPending = false,
}: {
  error: StructuredTurnErrorInput | string | null | undefined;
  onRetry?: () => void;
  onOpenProviderSettings?: () => void;
  onCompactAndRetry?: () => void;
  busy?: boolean;
  retryDisabled?: boolean;
  compactPending?: boolean;
}) {
  const [showDetails, setShowDetails] = useState(false);
  const classified = classifyTurnError(error);

  return (
    <div
      className="my-2 rounded-xl border border-destructive/30 bg-destructive/5 px-3.5 py-3 text-sm"
      data-turn-error-kind={classified.kind}
      role="alert"
    >
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" />
        <div className="min-w-0 flex-1 space-y-2">
          <div>
            <p className="font-medium text-foreground">{classified.title}</p>
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
              {classified.guidance}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {classified.kind === "auth" && onOpenProviderSettings ? (
              <Button
                size="xs"
                variant="default"
                onClick={onOpenProviderSettings}
              >
                <Settings className="mr-1 size-3.5" />
                打开模型配置
              </Button>
            ) : null}

            {classified.kind === "context_limit" && onCompactAndRetry ? (
              <Button
                size="xs"
                variant="default"
                disabled={busy || compactPending || retryDisabled}
                onClick={onCompactAndRetry}
              >
                <Minimize2 className="mr-1 size-3.5" />
                {compactPending ? "正在压缩上下文…" : "压缩上下文并重试"}
              </Button>
            ) : null}

            {onRetry ? (
              <Button
                size="xs"
                variant={
                  classified.primaryAction === "retry" ? "default" : "outline"
                }
                disabled={busy || retryDisabled}
                onClick={onRetry}
              >
                <RefreshCcw className="mr-1 size-3.5" />
                重试
              </Button>
            ) : null}

            {classified.kind === "bad_request" && onOpenProviderSettings ? (
              <Button
                size="xs"
                variant="outline"
                onClick={onOpenProviderSettings}
              >
                <Settings className="mr-1 size-3.5" />
                检查模型配置
              </Button>
            ) : null}

            <button
              type="button"
              className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              aria-expanded={showDetails}
              onClick={() => setShowDetails((current) => !current)}
            >
              {showDetails ? (
                <ChevronDown className="size-3.5" />
              ) : (
                <ChevronRight className="size-3.5" />
              )}
              查看技术详情
            </button>
          </div>

          {showDetails ? (
            <pre className="max-h-36 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border/60 bg-background/80 p-2 font-mono text-[11px] text-muted-foreground">
              {classified.rawDetail}
            </pre>
          ) : null}
        </div>
      </div>
    </div>
  );
}
