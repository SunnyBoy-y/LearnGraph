import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Plus, RefreshCcw } from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import {
  createEgressApproval,
  decideEgressApproval,
  listEgressApprovals,
} from "@/api";
import { useAuth } from "@/features/auth/auth-context-value";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { workspaceResourcePrefix } from "@/lib/query-keys";
import type {
  EgressAuthorizationDecision,
  EgressAuthorizationRequest,
} from "@/types/egress";

const STATUS_FILTERS: Array<{ value: string; label: string }> = [
  { value: "", label: "全部" },
  { value: "pending", label: "待审批" },
  { value: "approved", label: "已允许" },
  { value: "denied", label: "已拒绝" },
  { value: "expired", label: "已过期" },
  { value: "consumed", label: "已消费" },
];

function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

function DecisionButtons({
  request,
  onDecide,
  busy,
}: {
  request: EgressAuthorizationRequest;
  onDecide: (decision: EgressAuthorizationDecision) => void;
  busy: boolean;
}) {
  if (request.status !== "pending") return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        disabled={busy}
        onClick={() => onDecide("allow_once")}
        size="sm"
        variant="outline"
      >
        允许一次
      </Button>
      <Button
        disabled={busy}
        onClick={() => onDecide("allow_always")}
        size="sm"
        title="写入你的用户级 egress 允许列表；需要工作区管理权限"
        variant="outline"
      >
        总是允许
      </Button>
      <Button
        disabled={busy}
        onClick={() => onDecide("deny")}
        size="sm"
        variant="outline"
      >
        拒绝
      </Button>
    </div>
  );
}

export function EgressApprovalsPage() {
  const { workspaceId } = useAuth();
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("");
  const [hostname, setHostname] = useState("");
  const [purpose, setPurpose] = useState("");

  const queryPrefix = workspaceResourcePrefix(workspaceId, "egress-approvals");

  const approvals = useQuery({
    queryKey: [...queryPrefix, statusFilter],
    queryFn: () => listEgressApprovals({ status: statusFilter || undefined, limit: 100 }),
    enabled: Boolean(workspaceId),
  });

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: queryPrefix });
  };

  const create = useMutation({
    mutationFn: () =>
      createEgressApproval({
        hostname: hostname.trim(),
        purpose: purpose.trim() || undefined,
      }),
    onError: (error) => toast.error(error.message),
    onSuccess: async () => {
      toast.success("已创建审批请求");
      setHostname("");
      setPurpose("");
      await invalidate();
    },
  });

  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: EgressAuthorizationDecision }) =>
      decideEgressApproval(id, decision),
    onError: (error) => toast.error(error.message),
    onSuccess: async (result) => {
      toast.success(
        result.decision === "allow_once"
          ? "已允许本次访问"
          : result.decision === "allow_always"
            ? "已写入总是允许列表"
            : "已拒绝该主机",
      );
      await invalidate();
    },
  });

  function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!hostname.trim()) {
      toast.error("请输入主机名");
      return;
    }
    create.mutate();
  }

  const items = approvals.data?.items ?? [];

  return (
    <PageFrame>
      <PageIntro
        description="沙箱网络层出站审批：Agent 需要访问外部主机时，在此创建并审批请求，只放行精确主机名，内网/环回/云元数据地址始终被代理拒绝。搜索/Deep Research 允许查询哪些来源域名，请在「搜索与研究」页的来源白名单中配置（应用层）。"
        title="Egress 审批"
      />
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-muted bg-muted/25 px-4 py-3 text-xs text-muted-foreground">
        <span className="min-w-0 flex-1">
          本页只管沙箱容器的主机放行（网络层）。网页抓取与搜索的应用层白名单、
          沙箱开关及完整边界请查看统一的
          <Link
            className="mx-1 font-medium text-primary underline-offset-4 hover:underline"
            to={`/w/${workspaceId}/settings/access-approvals`}
          >
            访问与审批
          </Link>
          总览。
        </span>
      </div>
      <div className="flex items-center justify-end gap-2">
        <Button
          disabled={approvals.isFetching}
          onClick={() => void approvals.refetch()}
          size="sm"
          variant="ghost"
        >
          <RefreshCcw className="size-4" />
          刷新
        </Button>
      </div>

      <Surface className="p-4">
        <SectionHeading title="新建审批请求" />
        <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={handleCreate}>
          <div className="min-w-52 flex-1">
            <Label htmlFor="egress-hostname">主机名</Label>
            <Input
              id="egress-hostname"
              onChange={(event) => setHostname(event.target.value)}
              placeholder="api.example.com"
              value={hostname}
            />
          </div>
          <div className="min-w-52 flex-1">
            <Label htmlFor="egress-purpose">用途（可选）</Label>
            <Input
              id="egress-purpose"
              onChange={(event) => setPurpose(event.target.value)}
              placeholder="如：拉取模型评测数据"
              value={purpose}
            />
          </div>
          <Button disabled={create.isPending} type="submit">
            <Plus className="size-4" />
            创建请求
          </Button>
        </form>
      </Surface>

      <Surface className="mt-4 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <SectionHeading title="审批队列" />
          <div className="flex flex-wrap gap-1.5">
            {STATUS_FILTERS.map((option) => (
              <Button
                key={option.value}
                className={statusFilter === option.value ? "bg-muted" : ""}
                onClick={() => setStatusFilter(option.value)}
                size="sm"
                type="button"
                variant="ghost"
              >
                {option.label}
              </Button>
            ))}
          </div>
        </div>

        {approvals.isPending ? (
          <LoadingState label="正在读取审批请求…" />
        ) : approvals.isError ? (
          <ErrorState
            message={approvals.error.message}
            onRetry={() => void approvals.refetch()}
          />
        ) : items.length === 0 ? (
          <EmptyState
            description={statusFilter ? "该状态下暂无审批请求" : "暂无审批请求，Agent 需要外网时会出现在这里"}
            title="没有审批请求"
          />
        ) : (
          <ul className="mt-3 divide-y">
            {items.map((request) => (
              <li key={request.id} className="flex flex-col gap-2 py-3 first:pt-1 last:pb-1">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="text-sm font-semibold">{request.hostname}</code>
                  <StatePill status={request.status} />
                  {request.allow_always && (
                    <Badge variant="outline">总是允许</Badge>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  <span>能力：{request.capability}</span>
                  <span>请求者：{request.requested_by}</span>
                  <span>创建：{formatTime(request.created_at)}</span>
                  <span>过期：{formatTime(request.expires_at)}</span>
                  {request.decided_by && (
                    <span>决策：{request.decision}（{request.decided_by}）</span>
                  )}
                </div>
                {request.request_context && (
                  <pre className="max-h-24 overflow-auto rounded-lg bg-muted/50 p-2 text-[11px] leading-4 text-muted-foreground">
                    {JSON.stringify(request.request_context, null, 2)}
                  </pre>
                )}
                <DecisionButtons
                  busy={decide.isPending}
                  onDecide={(decision) => decide.mutate({ id: request.id, decision })}
                  request={request}
                />
              </li>
            ))}
          </ul>
        )}
      </Surface>

      {approvals.data && approvals.data.total > (approvals.data?.items.length ?? 0) && (
        <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
          <CircleAlert className="size-3.5" />
          仅显示前 {approvals.data.items.length} 条（共 {approvals.data.total} 条）
        </div>
      )}
    </PageFrame>
  );
}
