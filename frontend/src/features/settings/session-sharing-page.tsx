import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Eye, Link2, MessageSquareShare, ShieldOff, SquareCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from '@/lib/notifications';

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
import { Checkbox } from "@/components/ui/checkbox";
import { ClearableSearchInput } from "@/components/shared/clearable-search-input";
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
import { useListSelection } from "@/lib/list-selection";
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
  const [search, setSearch] = useState("");
  const [revokeTarget, setRevokeTarget] =
    useState<SessionShareManagementView | null>(null);
  const [batchRevokeOpen, setBatchRevokeOpen] = useState(false);
  const queryKey = workspaceQueryKey(workspaceId, "session-shares");
  const shares = useQuery({
    queryKey,
    queryFn: () => listSessionShareManagement(),
    refetchOnMount: "always",
  });

  // Keep the management surface active-only even if an older backend returns
  // retained revoked rows (newer endpoints filter them server-side).
  const rows = useMemo(
    () => (shares.data ?? []).filter((row) => row.status === "active"),
    [shares.data],
  );

  const filteredRows = useMemo(() => {
    const q = search.trim().toLocaleLowerCase();
    if (!q) return rows;
    return rows.filter((row) =>
      `${row.session_title || ""} ${row.label || ""} ${row.token_prefix || ""}`
        .toLocaleLowerCase()
        .includes(q),
    );
  }, [rows, search]);

  const visibleIds = useMemo(() => filteredRows.map((row) => row.id), [filteredRows]);
  const selection = useListSelection(visibleIds);
  const selectedShares = useMemo(
    () => rows.filter((row) => selection.selectedIds.has(row.id)),
    [rows, selection.selectedIds],
  );

  const revoke = useMutation({
    mutationFn: (shareId: string) => revokeSessionShare(shareId),
    onSuccess: async (_result, shareId) => {
      selection.removeIds([shareId]);
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

  const batchRevoke = useMutation({
    mutationFn: async (targets: SessionShareManagementView[]) => {
      const results = await Promise.allSettled(targets.map((share) => revokeSessionShare(share.id)));
      const succeededIds: string[] = [];
      results.forEach((result, index) => {
        if (result.status === "fulfilled") succeededIds.push(targets[index].id);
      });
      return { succeededIds, failed: targets.length - succeededIds.length };
    },
    onSuccess: async ({ succeededIds, failed }) => {
      selection.removeIds(succeededIds);
      const removed = new Set(succeededIds);
      queryClient.setQueryData<SessionShareManagementView[]>(
        queryKey,
        (current) => current?.filter((item) => !removed.has(item.id)) ?? [],
      );
      await queryClient.invalidateQueries({ queryKey });
      setBatchRevokeOpen(false);
      if (failed === 0) toast.success(`已取消 ${succeededIds.length} 条分享`);
      else if (succeededIds.length > 0) toast.warning(`已取消 ${succeededIds.length} 条分享，${failed} 条失败`);
      else toast.error("批量取消分享失败");
    },
    onError: (error: Error) => toast.error(error.message || "批量取消分享失败"),
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

  const totalViews = rows.reduce((sum, row) => sum + row.view_count, 0);
  const totalLinks = rows.reduce((sum, row) => sum + row.active_token_count, 0);
  const busy = revoke.isPending || batchRevoke.isPending;

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
        <div className="flex flex-wrap items-start justify-between gap-3">
          <SectionHeading
            description="每条记录都是创建时冻结的只读快照；源会话后续修改不会影响已分享内容。"
            title="已分享的会话"
          />
          {rows.length ? (
            <Button
              aria-pressed={selection.multiSelect}
              onClick={selection.toggleMultiSelect}
              size="sm"
              type="button"
              variant={selection.multiSelect ? "secondary" : "outline"}
            >
              <SquareCheck className="size-4" />
              {selection.multiSelect ? "退出多选" : "多选"}
            </Button>
          ) : null}
        </div>

        {rows.length ? (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <ClearableSearchInput
              aria-label="搜索已分享会话"
              containerClassName="min-w-52 flex-1 sm:max-w-sm"
              onChange={setSearch}
              placeholder="搜索会话标题、标签或令牌前缀…"
              value={search}
            />
            {selection.multiSelect ? (
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  aria-label="全选当前列表"
                  checked={selection.visibleCheckboxState}
                  disabled={busy || !filteredRows.length}
                  onCheckedChange={(checked) => selection.toggleVisible(checked === true)}
                />
                全选当前列表
              </label>
            ) : null}
          </div>
        ) : null}

        {selection.multiSelect && selection.selectedCount > 0 ? (
          <div
            aria-label="已分享会话批量操作"
            className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border bg-muted/30 px-3 py-2 text-sm"
            role="region"
          >
            <span>
              已选 <strong>{selection.selectedCount}</strong> 条分享
              {selection.otherSelectedCount > 0 ? `（另有 ${selection.otherSelectedCount} 条不在当前筛选）` : ""}
            </span>
            <div className="ml-auto flex flex-wrap items-center gap-1.5">
              <Button
                disabled={busy}
                onClick={() => setBatchRevokeOpen(true)}
                size="xs"
                type="button"
                variant="destructive"
              >
                <ShieldOff className="size-3.5" />
                批量取消分享
              </Button>
              <Button disabled={busy} onClick={selection.clear} size="xs" type="button" variant="ghost">
                取消选择
              </Button>
            </div>
          </div>
        ) : null}

        {!rows.length ? (
          <div className="mt-4">
            <EmptyState
              description="在侧栏的会话菜单中选择“分享”，创建一个只读链接后，它会出现在这里。"
              title="还没有正在分享的会话"
            />
          </div>
        ) : !filteredRows.length ? (
          <div className="mt-4">
            <EmptyState
              description={`未找到与「${search.trim()}」匹配的分享记录。`}
              title="没有匹配的分享会话"
            />
          </div>
        ) : (
          <div className="mt-4 grid gap-3">
            {filteredRows.map((share) => {
              const selected = selection.selectedIds.has(share.id);
              const sourceId = share.source_session_id || share.session_id;
              return (
                <article
                  className={`rounded-xl border bg-background p-4 transition-colors hover:bg-muted/20 ${selected ? "border-ring ring-1 ring-ring" : ""}`}
                  key={share.id}
                >
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="flex min-w-0 items-start gap-3">
                      {selection.multiSelect ? (
                        <Checkbox
                          aria-label={`选择分享 ${share.session_title || "未命名会话"}`}
                          className="mt-2"
                          disabled={busy}
                          {...selection.checkboxProps(share.id)}
                        />
                      ) : null}
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
                    <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                      {sourceId && workspaceId ? (
                        <Button asChild size="sm" variant="ghost">
                          <Link to={`/w/${workspaceId}/chat/${encodeURIComponent(sourceId)}`}>
                            <ExternalLink className="size-3.5" />
                            原会话
                          </Link>
                        </Button>
                      ) : null}
                      <Button
                        aria-label={`取消分享 ${share.session_title || "未命名会话"}`}
                        disabled={busy}
                        onClick={() => setRevokeTarget(share)}
                        size="sm"
                        type="button"
                        variant="outline"
                      >
                        <ShieldOff className="size-3.5" />
                        取消分享
                      </Button>
                    </div>
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
              );
            })}
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

      <AlertDialog
        onOpenChange={(open) => {
          if (!open && !batchRevoke.isPending) setBatchRevokeOpen(false);
        }}
        open={batchRevokeOpen}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>批量取消 {selectedShares.length} 条会话分享？</AlertDialogTitle>
            <AlertDialogDescription>
              选中的 {selectedShares.length} 条分享链接都会立即失效并移出当前列表；历史快照与审计记录仍会保留。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={batchRevoke.isPending}>保留分享</AlertDialogCancel>
            <AlertDialogAction
              disabled={batchRevoke.isPending}
              onClick={(event) => {
                event.preventDefault();
                batchRevoke.mutate(selectedShares);
              }}
              variant="destructive"
            >
              {batchRevoke.isPending ? "取消中…" : `取消 ${selectedShares.length} 条分享`}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageFrame>
  );
}
