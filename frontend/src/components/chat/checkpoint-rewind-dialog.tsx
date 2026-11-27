import { useEffect, useState } from "react";
import { AlertTriangle, History, LoaderCircle, RotateCcw, Trash2 } from "lucide-react";

import {
  executeTurnCheckpointRewind,
  previewTurnCheckpointRewind,
  type CheckpointRewindPreview,
  type CheckpointRewindResult,
} from "@/api/checkpoints";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/lib/notifications";

export function CheckpointRewindDialog({
  open,
  sessionId,
  turnId,
  onOpenChange,
  onRewound,
}: {
  open: boolean;
  sessionId: string;
  turnId: string | null;
  onOpenChange: (open: boolean) => void;
  onRewound?: (result: CheckpointRewindResult) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<CheckpointRewindPreview | null>(null);

  useEffect(() => {
    if (!open || !sessionId || !turnId) {
      setPreview(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void previewTurnCheckpointRewind(sessionId, turnId, true)
      .then((data) => {
        if (!cancelled) setPreview(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "加载文件回退预览失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, sessionId, turnId]);

  const handleConfirm = async () => {
    if (!preview || !turnId) return;
    setExecuting(true);
    setError(null);
    try {
      const result = await executeTurnCheckpointRewind(sessionId, turnId, {
        expected_current_hashes: preview.expected_current_hashes,
        include_subsequent_turns: true,
      });
      const summary = [
        result.restored_paths.length ? `恢复 ${result.restored_paths.length} 个文件` : null,
        result.deleted_paths.length ? `删除 ${result.deleted_paths.length} 个新建文件` : null,
        result.conflicts.length ? `跳过 ${result.conflicts.length} 个冲突文件` : null,
      ]
        .filter(Boolean)
        .join("，");
      if (result.conflicts.length) {
        toast.warning("沙箱文件已部分回退（存在冲突跳过）", {
          description: summary || "存在冲突文件未覆盖",
        });
      } else {
        toast.success("沙箱文件已回退", {
          description: summary || "没有需要变更的文件",
        });
      }
      onRewound?.(result);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "文件回退失败");
    } finally {
      setExecuting(false);
    }
  };

  const actionableCount = (preview?.restore_count ?? 0) + (preview?.delete_count ?? 0);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <History className="size-4" />
            回退沙箱工作区文件到该轮之前
          </DialogTitle>
          <DialogDescription>
            系统已核对当前文件哈希；若文件在记录后被二次修改，将自动标记冲突并跳过，不会静默覆盖。
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <LoaderCircle className="size-4 animate-spin" />
            正在计算受影响文件与冲突状态…
          </div>
        ) : error ? (
          <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-xs text-destructive" role="alert">
            {error}
          </div>
        ) : preview ? (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2 text-xs">
              <span className="rounded-md bg-muted px-2 py-1 font-medium">
                恢复旧版本：{preview.restore_count}
              </span>
              <span className="rounded-md bg-muted px-2 py-1 font-medium">
                删除新建文件：{preview.delete_count}
              </span>
              {preview.conflict_count > 0 ? (
                <span className="rounded-md bg-amber-500/15 px-2 py-1 font-medium text-amber-700 dark:text-amber-300">
                  冲突将跳过：{preview.conflict_count}
                </span>
              ) : null}
            </div>

            <ul className="max-h-64 space-y-1.5 overflow-auto rounded-lg border border-border/70 p-2 text-xs">
              {preview.items.map((item) => (
                <li
                  key={item.path}
                  className="flex flex-col gap-0.5 rounded bg-muted/40 px-2.5 py-1.5"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-[11px]">{item.path}</span>
                    {item.conflict ? (
                      <span className="inline-flex shrink-0 items-center gap-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300">
                        <AlertTriangle className="size-3" />
                        冲突跳过
                      </span>
                    ) : item.action === "delete" ? (
                      <span className="inline-flex shrink-0 items-center gap-1 rounded bg-destructive/10 px-1.5 py-0.5 text-[10px] font-medium text-destructive">
                        <Trash2 className="size-3" />
                        删除新建
                      </span>
                    ) : item.action === "restore" ? (
                      <span className="inline-flex shrink-0 items-center gap-1 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                        <RotateCcw className="size-3" />
                        恢复前像
                      </span>
                    ) : (
                      <span className="shrink-0 text-[10px] text-muted-foreground">
                        无需变更
                      </span>
                    )}
                  </div>
                  {item.conflict && item.conflict_reason ? (
                    <p className="text-[11px] text-amber-700 dark:text-amber-300">
                      {item.conflict_reason}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <DialogFooter>
          <Button
            disabled={executing}
            onClick={() => onOpenChange(false)}
            size="sm"
            variant="outline"
          >
            取消
          </Button>
          <Button
            disabled={loading || executing || !preview || actionableCount === 0}
            onClick={() => void handleConfirm()}
            size="sm"
          >
            {executing ? "回退中…" : `确认安全回退 (${actionableCount})`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
