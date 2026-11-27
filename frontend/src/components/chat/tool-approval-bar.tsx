import { useEffect, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Globe,
  ShieldAlert,
  Trash2,
} from "lucide-react";

import { createSandboxDestructiveGrant } from "@/api/control";
import { decideEgressApproval, resumeEgressApproval } from "@/api/egress";
import {
  decideFetchAuthorization,
  resumeFetchAuthorization,
} from "@/api/fetch-authorizations";
import { Button } from "@/components/ui/button";
import type { PendingApprovalItem } from "@/features/chat/pending-approvals";
import { toast } from "@/lib/notifications";

function kindMeta(kind: PendingApprovalItem["kind"]) {
  switch (kind) {
    case "sandbox_delete":
      return {
        badge: "删除工作区文件",
        icon: Trash2,
        hint: "授权仅供下一次匹配这些路径的删除命令使用，使用后立即失效；不会影响宿主机文件。",
      };
    case "egress":
      return {
        badge: "网络出站审批",
        icon: Globe,
        hint: "仅按精确主机名放行公网访问；内网、环回与元数据地址仍会被出网代理拦截。",
      };
    case "fetch":
      return {
        badge: "网页读取授权",
        icon: ShieldAlert,
        hint: "仅放行应用层网页/论文抓取操作；如走沙箱容器，网络出站仍独立受 Egress 审批约束。",
      };
  }
}

export function ToolApprovalBar({
  items,
  onResolved,
}: {
  items: readonly PendingApprovalItem[];
  onResolved: (id: string, outcome: "approved" | "denied") => void;
}) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [batchBusy, setBatchBusy] = useState(false);

  useEffect(() => {
    if (activeIndex >= items.length) {
      setActiveIndex(Math.max(0, items.length - 1));
    }
  }, [activeIndex, items.length]);

  if (!items.length) return null;

  const current = items[Math.min(activeIndex, items.length - 1)]!;
  const meta = kindMeta(current.kind);
  const Icon = meta.icon;
  const isBusy = batchBusy || busyId === current.id;

  const notifyRefresh = () => {
    window.dispatchEvent(new CustomEvent("learngraph:refresh-messages"));
  };

  const handleSandboxDelete = async (
    item: Extract<PendingApprovalItem, { kind: "sandbox_delete" }>,
    allow: boolean,
  ) => {
    if (!allow) {
      onResolved(item.id, "denied");
      toast.info("已拒绝删除沙箱工作区文件");
      return;
    }
    setBusyId(item.id);
    try {
      for (const path of item.paths) {
        await createSandboxDestructiveGrant({
          chat_session_id: item.chatSessionId,
          path_prefix: path.startsWith("work/") ? path : `work/${path}`,
          action: "delete_path",
          sandbox_session_id: item.sandboxSessionId,
          command_intent_digest: item.commandIntentDigest,
          ttl_seconds: 300,
          reason: "user_confirmed_single_use_in_chat",
        });
      }
      toast.success("已授权下一次匹配的删除命令，使用后立即失效");
      onResolved(item.id, "approved");
      notifyRefresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除授权失败");
    } finally {
      setBusyId(null);
    }
  };

  const handleEgress = async (
    item: Extract<PendingApprovalItem, { kind: "egress" }>,
    decision: "allow_once" | "allow_always" | "deny",
  ) => {
    setBusyId(item.id);
    try {
      await decideEgressApproval(item.requestId, decision);
      if (decision !== "deny" && item.resumeMode === "server") {
        await resumeEgressApproval(item.requestId);
      } else if (decision !== "deny" && item.hostname) {
        const isAcquisition =
          item.toolName === "download_external_image" ||
          item.toolName === "download_github_source";
        window.dispatchEvent(
          new CustomEvent("learngraph:compose", {
            detail: {
              content: isAcquisition
                ? `已批准${item.toolLabel || "外部下载工具"}访问主机 ${item.hostname}。请使用与刚才完全相同的参数继续该下载。`
                : `已批准沙箱访问主机 ${item.hostname}。请继续执行刚才需要该主机的命令。`,
              autoSend: true,
            },
          }),
        );
      }
      toast.success(
        decision === "allow_always"
          ? `已永久放行出网主机 ${item.hostname}`
          : decision === "allow_once"
            ? `已允许本次访问 ${item.hostname}`
            : `已拒绝访问 ${item.hostname}`,
      );
      onResolved(item.id, decision === "deny" ? "denied" : "approved");
      notifyRefresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "出网审批失败");
    } finally {
      setBusyId(null);
    }
  };

  const handleFetch = async (
    item: Extract<PendingApprovalItem, { kind: "fetch" }>,
    decision: "allow_once" | "allow_always" | "deny",
  ) => {
    setBusyId(item.id);
    try {
      await decideFetchAuthorization(item.requestId, decision);
      if (decision !== "deny" && item.resumeMode === "server") {
        await resumeFetchAuthorization(item.requestId);
      } else if (decision !== "deny" && item.requestedUrl) {
        const toolHint =
          item.toolName === "read_paper"
            ? "read_paper（沿用上一轮的 provider 和 paper_id）读取论文原文"
            : item.toolName === "browser_read_page"
              ? "browser_read_page 获取网页内容"
              : "fetch_web_page 获取网页内容";
        window.dispatchEvent(
          new CustomEvent("learngraph:compose", {
            detail: {
              content: `已批准读取 ${item.requestedUrl}，请继续使用 ${toolHint}。`,
              autoSend: true,
            },
          }),
        );
      }
      toast.success(
        decision === "allow_always"
          ? "已加入网页抓取白名单"
          : decision === "allow_once"
            ? "已允许本次抓取"
            : "已拒绝网页抓取",
      );
      onResolved(item.id, decision === "deny" ? "denied" : "approved");
      notifyRefresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "网页抓取授权失败");
    } finally {
      setBusyId(null);
    }
  };

  const handleRejectAll = async () => {
    setBatchBusy(true);
    try {
      for (const item of items) {
        if (item.kind === "sandbox_delete") {
          onResolved(item.id, "denied");
        } else if (item.kind === "egress") {
          try {
            await decideEgressApproval(item.requestId, "deny");
          } catch {
            // Continue rejecting remaining items in the batch.
          }
          onResolved(item.id, "denied");
        } else {
          try {
            await decideFetchAuthorization(item.requestId, "deny");
          } catch {
            // Continue rejecting remaining items in the batch.
          }
          onResolved(item.id, "denied");
        }
      }
      toast.info(`已拒绝全部 ${items.length} 项待审批请求`);
      notifyRefresh();
    } finally {
      setBatchBusy(false);
    }
  };

  return (
    <section
      aria-label="待处理工具审批"
      className="mb-2 rounded-2xl border border-amber-400/50 bg-amber-50/80 px-4 py-3 text-sm shadow-sm dark:border-amber-500/40 dark:bg-amber-950/30"
      data-tool-approval-bar="true"
      data-approval-kind={current.kind}
      role="region"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Icon className="size-4 shrink-0 text-amber-600 dark:text-amber-400" />
          <span className="font-semibold text-foreground">{meta.badge}</span>
          {items.length > 1 ? (
            <span className="rounded-full bg-amber-200/70 px-2 py-0.5 text-[11px] font-medium text-amber-900 dark:bg-amber-900/60 dark:text-amber-200">
              待审批 {Math.min(activeIndex, items.length - 1) + 1} / {items.length}
            </span>
          ) : null}
        </div>
        {items.length > 1 ? (
          <div className="flex items-center gap-1">
            <Button
              aria-label="上一条审批"
              disabled={activeIndex <= 0 || isBusy}
              onClick={() => setActiveIndex((idx) => Math.max(0, idx - 1))}
              size="icon-xs"
              variant="ghost"
            >
              <ChevronLeft className="size-3.5" />
            </Button>
            <Button
              aria-label="下一条审批"
              disabled={activeIndex >= items.length - 1 || isBusy}
              onClick={() =>
                setActiveIndex((idx) => Math.min(items.length - 1, idx + 1))
              }
              size="icon-xs"
              variant="ghost"
            >
              <ChevronRight className="size-3.5" />
            </Button>
          </div>
        ) : null}
      </div>

      <div className="mt-2 space-y-1.5 text-xs">
        {current.message ? (
          <p className="leading-5 text-foreground/90">{current.message}</p>
        ) : null}

        {current.kind === "sandbox_delete" ? (
          <ul className="max-h-24 list-disc space-y-0.5 overflow-auto pl-5 font-mono text-[11px] text-foreground/90">
            {current.paths.map((path) => (
              <li key={path}>{path}</li>
            ))}
          </ul>
        ) : (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-background/70 px-2.5 py-1.5 font-mono text-[11px] text-foreground">
            <span>目标主机：{current.hostname}</span>
            {current.requestedUrl ? (
              <span className="truncate text-muted-foreground">
                {current.requestedUrl}
              </span>
            ) : null}
          </div>
        )}

        <p className="text-[11px] leading-4 text-muted-foreground">{meta.hint}</p>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {current.kind === "sandbox_delete" ? (
            <>
              <Button
                disabled={isBusy}
                onClick={() => void handleSandboxDelete(current, true)}
                size="xs"
              >
                {isBusy ? "授权中…" : "允许本次"}
              </Button>
              <Button
                disabled={isBusy}
                onClick={() => void handleSandboxDelete(current, false)}
                size="xs"
                variant="outline"
              >
                拒绝
              </Button>
            </>
          ) : current.kind === "egress" ? (
            <>
              <Button
                disabled={isBusy}
                onClick={() => void handleEgress(current, "allow_once")}
                size="xs"
              >
                {isBusy ? "处理中…" : "允许本次"}
              </Button>
              <Button
                disabled={isBusy}
                onClick={() => void handleEgress(current, "allow_always")}
                size="xs"
                variant="outline"
              >
                始终允许该域名
              </Button>
              <Button
                disabled={isBusy}
                onClick={() => void handleEgress(current, "deny")}
                size="xs"
                variant="ghost"
              >
                拒绝
              </Button>
            </>
          ) : (
            <>
              <Button
                disabled={isBusy}
                onClick={() => void handleFetch(current, "allow_once")}
                size="xs"
              >
                {isBusy ? "处理中…" : "允许本次"}
              </Button>
              <Button
                disabled={isBusy}
                onClick={() => void handleFetch(current, "allow_always")}
                size="xs"
                variant="outline"
              >
                始终允许该域名
              </Button>
              <Button
                disabled={isBusy}
                onClick={() => void handleFetch(current, "deny")}
                size="xs"
                variant="ghost"
              >
                拒绝
              </Button>
            </>
          )}
        </div>

        {items.length > 1 ? (
          <Button
            disabled={isBusy}
            onClick={() => void handleRejectAll()}
            size="xs"
            variant="ghost"
          >
            全部拒绝 ({items.length})
          </Button>
        ) : null}
      </div>
    </section>
  );
}
