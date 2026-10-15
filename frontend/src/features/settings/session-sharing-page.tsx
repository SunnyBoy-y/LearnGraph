import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Link2, MessageSquareShare, ShieldOff } from "lucide-react";
import { toast } from "sonner";

import {
  listSessionShareManagement,
  revokeSessionShare,
  type SessionShareManagementView,
} from "@/api/session-sharing";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  MetricStrip,
  PageFrame,
  PageIntro,
  SectionHeading,
  Surface,
} from "@/components/shared/page-elements";
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function scopeLabel(scope: string): string {
  if (scope === "range") return "区间";
  if (scope === "answers") return "仅回答";
  return "整段对话";
}

function shareStatus(share: SessionShareManagementView): string {
  if (share.active_token_count > 0) return "分享中";
  return "已失效";
}

export function SessionSharingPage() {
  const { workspaceId } = useAuth();
  const queryClient = useQueryClient();
  const [revokeTarget, setRevokeTarget] =
    useState<SessionShareManagementView | null>(null);
  const queryKey = workspaceQueryKey(workspaceId, "session-shares");
  const shares = useQuery({
    queryKey,
    queryFn: () => listSessionShareManagement(),
    refetchOnMount: "always",
  });

  const revoke = useMutation({
    mutationFn: (shareId: string) => revokeSessionShare(shareId),
    onSuccess: async (_result, shareId) => {
      // Remove the row immediately.  The follow-up refetch is still important
      // because another tab may have created or revoked a share meanwhile.
      queryClient.setQueryData<SessionShareManagementView[]>(
        queryKey,
        (current) => current?.filter((item) => item.id !== shareId) ?? [],
      );
      await queryClient.invalidateQueries({ queryKey });
      setRevokeTarget(null);
      toast.success("已取消分享");
    },
    onError: (error: Error) => toast.error(error.message || "取消分享失败"),
  });

  if (shares.isPending) {
    return (
      <PageFrame>
        <LoadingState label="正在读取会话分享…" />
      </PageFrame>
    );
  }

  if (shares.isError) {
    return (
      <PageFrame>
        <ErrorState message={shares.error.message} onRetry={() => void shares.refetch()} />
      </PageFrame>
    );
  }

  // Keep the management surface active-only even if an older backend returns
  // retained revoked rows (newer endpoints filter them server-side).
  const rows = (shares.data ?? []).filter((row) => row.status === "active");
  const totalViews = rows.reduce((sum, row) => sum + row.view_count, 0);
  const totalLinks = rows.reduce((sum, row) => sum + row.active_token_count, 0);

  return (
    <PageFrame>
      <PageIntro
        description="管理通过只读链接分享的会话。取消分享会立即让链接失效并移出这里；快照、阅读次数和审计记录仍会保留。"
        eyebrow="Sessions & Sharing"
        title="会话与分享"
      />

      <MetricStrip
        items={[
          { label: "当前分享", value: rows.length, hint: "未取消的分享快照", tone: "info" },
          { label: "阅读次数", value: totalViews, hint: "所有当前分享累计", tone: "positive" },
          { label: "可用链接", value: totalLinks, hint: "当前仍可打开", tone: "default" },
        ]}
      />

      <Surface className="p-5">
        <SectionHeading
          description="每条记录都是创建时冻结的只读快照；源会话后续修改不会影响已分享内容。"
          title="已分享的会话"
        />

        {!rows.length ? (
          <div className="mt-4">
            <EmptyState
              description="在侧栏的会话菜单中选择“分享”，创建一个只读链接后，它会出现在这里。"
              title="还没有正在分享的会话"
            />
          </div>
        ) : (
          <div className="mt-4 grid gap-3">
            {rows.map((share) => (
              <article
                className="rounded-xl border bg-background p-4 transition-colors hover:bg-muted/20"
                key={share.id}
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="flex min-w-0 items-start gap-3">
                    <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                      <MessageSquareShare className="size-4" />
                    </span>
                    <div className="min-w-0">
                      <h3 className="truncate text-sm font-semibold" title={share.session_title}>
                        {share.session_title || "未命名会话"}
                      </h3>
                      <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                        <Badge variant="outline">{scopeLabel(share.scope)}</Badge>
                        <span>{share.message_count} 条消息</span>
                        <span aria-hidden="true">·</span>
                        <span>创建于 {formatDate(share.created_at)}</span>
                        {share.label ? (
                          <>
                            <span aria-hidden="true">·</span>
                            <span className="truncate" title={share.label}>
                              {share.label}
                            </span>
                          </>
                        ) : null}
                      </div>
                    </div>
                  </div>
                  <Button
                    aria-label={`取消分享 ${share.session_title || "未命名会话"}`}
                    disabled={revoke.isPending}
                    onClick={() => setRevokeTarget(share)}
                    size="sm"
                    type="button"
                    variant="outline"
                  >
                    <ShieldOff className="size-3.5" />
                    取消分享
                  </Button>
                </div>

                <div className="mt-4 grid gap-2 border-t pt-3 text-xs sm:grid-cols-3">
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <Eye className="size-3.5" />
                    <span>点击阅读</span>
                    <strong className="font-semibold tabular-nums text-foreground">
                      {share.view_count} 次
                    </strong>
                  </div>
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <Link2 className="size-3.5" />
                    <span>{share.active_token_count} 个可用链接</span>
                  </div>
                  <div className="text-muted-foreground sm:text-right">
                    最近阅读 {formatDate(share.last_viewed_at)}
                  </div>
                </div>
                {share.tokens.length ? (
                  <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
                    {share.tokens.map((token) => (
                      <span className="font-mono" key={token.id}>
                        {token.token_prefix}… · {token.view_count} 次
                        {token.max_views ? ` / ${token.max_views}` : ""}
                      </span>
                    ))}
                  </div>
                ) : null}
                <p className="mt-2 text-[11px] text-muted-foreground/80">
                  状态：{shareStatus(share)} · 取消后分享记录不会被清除
                </p>
              </article>
            ))}
          </div>
        )}
      </Surface>

      <AlertDialog
        onOpenChange={(open) => {
          if (!open && !revoke.isPending) setRevokeTarget(null);
        }}
        open={Boolean(revokeTarget)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>取消这条会话分享？</AlertDialogTitle>
            <AlertDialogDescription>
              “{revokeTarget?.session_title || "未命名会话"}”的所有分享链接会立即失效并从列表中移除。分享快照、阅读次数和记录仍会保留。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={revoke.isPending}>保留分享</AlertDialogCancel>
            <AlertDialogAction
              disabled={revoke.isPending}
              onClick={(event) => {
                event.preventDefault();
                if (revokeTarget) revoke.mutate(revokeTarget.id);
              }}
              variant="destructive"
            >
              {revoke.isPending ? "取消中…" : "取消分享"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageFrame>
  );
}

