import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Plus, RefreshCcw } from "lucide-react";
import { useParams } from "react-router-dom";
import { toast } from "sonner";

import {
  createEgressApproval,
  decideEgressApproval,
  listEgressApprovals,
} from "@/api/egress";
import {
  getFetchUserPolicy,
  getWebFetchSettings,
  listFetchAuthorizations,
  updateFetchUserPolicy,
  updateWebFetchSettings,
} from "@/api/fetch-authorizations";
import { listSandboxNetAudit } from "@/api/sandbox-net";
import { UnifiedAllowlistEditor } from "@/components/shared/domain-allowlist-editor";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { workspaceResourcePrefix } from "@/lib/query-keys";
import type { WebFetchChannel } from "@/types/fetch-authorization";
import type {
  EgressAuthorizationDecision,
  EgressAuthorizationRequest,
} from "@/types/egress";

const FETCH_CHANNEL_LABELS: Record<WebFetchChannel, string> = {
  sandbox: "沙箱隔离抓取",
  remote: "远程抓取 Provider",
  hosted: "Qwen 托管抓取",
};

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
      <Button disabled={busy} onClick={() => onDecide("allow_once")} size="sm" variant="outline">
        允许一次
      </Button>
      <Button
        disabled={busy}
        onClick={() => onDecide("allow_always")}
        size="sm"
        title="写入工作区出站允许列表"
        variant="outline"
      >
        总是允许
      </Button>
      <Button disabled={busy} onClick={() => onDecide("deny")} size="sm" variant="outline">
        拒绝
      </Button>
    </div>
  );
}

/** 网页抓取通道设置（工作区级：沙箱开关 + 通道优先级）。 */
function FetchChannelSettings() {
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({
    queryKey: ["fetch-authorization-settings"],
    queryFn: getWebFetchSettings,
  });
  const save = useMutation({
    mutationFn: updateWebFetchSettings,
    onSuccess: (next) => {
      toast.success("网页抓取设置已保存");
      queryClient.setQueryData(["fetch-authorization-settings"], next);
    },
    onError: (error) => toast.error(error.message),
  });
  const [sandboxEnabled, setSandboxEnabled] = useState(true);
  const [priority, setPriority] = useState<WebFetchChannel[]>([
    "sandbox",
    "remote",
    "hosted",
  ]);
  const data = settingsQuery.data;
  useEffect(() => {
    if (!data) return;
    setSandboxEnabled(data.sandbox_enabled);
    setPriority(data.priority);
  }, [data]);
  const dirty =
    !data ||
    data.sandbox_enabled !== sandboxEnabled ||
    JSON.stringify(data.priority) !== JSON.stringify(priority);
  const withRank = (channel: WebFetchChannel, rank: number) => {
    if (rank < 0) return priority;
    const others = priority.filter((item) => item !== channel);
    if (rank === 0) {
      if (others.length === 0) {
        toast.error("至少保留一个抓取通道");
        return priority;
      }
      return others;
    }
    const next = [...others];
    next.splice(Math.min(rank - 1, next.length), 0, channel);
    return next;
  };
  const effectiveLabel = data?.sandbox_effective && data.effective_channel === "sandbox"
    ? "沙箱隔离抓取"
    : data?.effective_channel === "remote"
      ? "远程抓取 Provider"
      : data?.effective_channel === "hosted"
        ? "Qwen 托管抓取"
        : "当前无可用抓取通道";
  if (settingsQuery.isPending) return <LoadingState label="正在读取抓取设置…" />;
  if (settingsQuery.isError) return <ErrorState message={settingsQuery.error.message} />;
  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1">
          <p className="text-sm font-medium">沙箱隔离抓取</p>
          <p className="text-xs text-muted-foreground">
            优先在隔离容器中抓取网页，避免在主机进程解析不可信 HTML。
          </p>
          {data && !data.global_sandbox_gate ? (
            <p className="text-xs text-destructive" role="alert">
              全局沙箱抓取开关（LEARNGRAPH_SANDBOX_WEB_FETCH_ENABLED）已关闭。
            </p>
          ) : null}
          {data && data.sandbox_enabled && data.global_sandbox_gate && data.allowlist_count === 0 && !data.allow_all ? (
            <p className="text-xs text-muted-foreground">
              尚未配置白名单域名，沙箱通道暂不可用。
            </p>
          ) : null}
        </div>
        <Switch checked={sandboxEnabled} disabled={save.isPending} onCheckedChange={setSandboxEnabled} />
      </div>
      <div className="space-y-3">
        <p className="text-sm font-medium">抓取优先级（自上而下，不可用时自动回退）</p>
        {(["sandbox", "remote", "hosted"] as const).map((channel) => {
          const rank = priority.indexOf(channel) + 1;
          return (
            <div className="flex items-center justify-between gap-4 rounded-xl border border-muted bg-muted/20 px-4 py-3" key={channel}>
              <p className="text-sm font-medium">{FETCH_CHANNEL_LABELS[channel]}</p>
              <Select
                disabled={save.isPending}
                onValueChange={(value) => setPriority(withRank(channel, Number(value)))}
                value={String(rank)}
              >
                <SelectTrigger className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="1">第 1 优先</SelectItem>
                  <SelectItem value="2">第 2 优先</SelectItem>
                  <SelectItem value="3">第 3 优先</SelectItem>
                  <SelectItem value="0">不启用</SelectItem>
                </SelectContent>
              </Select>
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-4">
        <StatePill
          label={`当前生效通道：${effectiveLabel}`}
          status={data?.effective_channel ? "healthy" : "failed"}
        />
        <Button disabled={!dirty || save.isPending} onClick={() => save.mutate({ sandbox_enabled: sandboxEnabled, priority })} size="sm">
          {save.isPending ? "保存中…" : "保存设置"}
        </Button>
      </div>
    </div>
  );
}

/** 出站审批：待审批队列 + 新建请求 + 历史记录。 */
function EgressApprovalQueue() {
  const queryClient = useQueryClient();
  const { workspaceId = "" } = useParams();
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
      createEgressApproval({ hostname: hostname.trim(), purpose: purpose.trim() || undefined }),
    onError: (error) => toast.error(error.message),
    onSuccess: async (request) => {
      if (request.status === "approved") {
        toast.success("该主机在白名单内，已自动放行");
      } else {
        toast.success("已创建审批请求");
      }
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
    <div className="space-y-4">
      <Surface className="p-4">
        <SectionHeading title="新建审批请求" />
        <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={handleCreate}>
          <div className="min-w-52 flex-1">
            <Label htmlFor="egress-hostname">主机名</Label>
            <Input id="egress-hostname" onChange={(event) => setHostname(event.target.value)} placeholder="api.example.com" value={hostname} />
          </div>
          <div className="min-w-52 flex-1">
            <Label htmlFor="egress-purpose">用途（可选）</Label>
            <Input id="egress-purpose" onChange={(event) => setPurpose(event.target.value)} placeholder="如：拉取评测数据" value={purpose} />
          </div>
          <Button disabled={create.isPending} type="submit">
            <Plus className="size-4" />
            创建请求
          </Button>
        </form>
      </Surface>
      <Surface className="p-4">
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
          <ErrorState message={approvals.error.message} onRetry={() => void approvals.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            description={statusFilter ? "该状态下暂无审批请求" : "暂无审批请求。白名单内的主机会自动放行，不会出现在这里"}
            title="没有审批请求"
          />
        ) : (
          <ul className="mt-3 divide-y">
            {items.map((request) => (
              <li key={request.id} className="flex flex-col gap-2 py-3 first:pt-1 last:pb-1">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="text-sm font-semibold">{request.hostname}</code>
                  <StatePill status={request.status} />
                  {request.allow_always && <Badge variant="outline">总是允许</Badge>}
                  {request.decided_by === "system:allowlist" && <Badge variant="outline">白名单自动放行</Badge>}
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  <span>能力：{request.capability}</span>
                  <span>请求者：{request.requested_by}</span>
                  <span>创建：{formatTime(request.created_at)}</span>
                  <span>过期：{formatTime(request.expires_at)}</span>
                </div>
                <DecisionButtons busy={decide.isPending} onDecide={(decision) => decide.mutate({ id: request.id, decision })} request={request} />
              </li>
            ))}
          </ul>
        )}
      </Surface>
      {approvals.data && approvals.data.total > (approvals.data?.items.length ?? 0) && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <CircleAlert className="size-3.5" />
          仅显示前 {approvals.data.items.length} 条（共 {approvals.data.total} 条）
        </div>
      )}
      <div className="flex justify-end">
        <Button disabled={approvals.isFetching} onClick={() => void approvals.refetch()} size="sm" variant="ghost">
          <RefreshCcw className="size-4" />
          刷新
        </Button>
      </div>
    </div>
  );
}

/** 我的个人白名单：聊天授权卡片「以后都允许」写入的域名（仅当前用户）。 */
function PersonalFetchAllowlist() {
  const queryClient = useQueryClient();
  const { workspaceId = "" } = useParams();
  const queryKey = [
    ...workspaceResourcePrefix(workspaceId, "fetch-authorizations"),
    "user-policy",
  ];
  const policy = useQuery({
    queryKey,
    queryFn: getFetchUserPolicy,
    enabled: Boolean(workspaceId),
  });
  const update = useMutation({
    mutationFn: updateFetchUserPolicy,
    onSuccess: (next) => {
      queryClient.setQueryData(queryKey, next);
      toast.success("个人白名单已更新");
    },
    onError: (error) => toast.error(error.message),
  });
  const domains = policy.data?.allowed_domains ?? [];
  const remove = (domain: string) => {
    if (!policy.data) return;
    update.mutate({
      ...policy.data,
      allowed_domains: domains.filter((item) => item !== domain),
    });
  };
  if (policy.isPending) return <LoadingState label="正在读取个人白名单…" />;
  if (policy.isError) {
    return (
      <div className="flex items-center justify-between gap-3 text-sm text-destructive">
        <span>{policy.error.message || "个人白名单读取失败"}</span>
        <Button onClick={() => void policy.refetch()} size="sm" variant="outline">
          重试
        </Button>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <p className="text-xs leading-5 text-muted-foreground">
        聊天授权卡片中选择「以后都允许」的域名写入这里，仅对当前用户生效；
        网页抓取判定时与工作区白名单、统一白名单取并集。在此删除即从个人白名单移除。
      </p>
      {domains.length ? (
        <div className="flex flex-wrap gap-2">
          {domains.map((domain) => (
            <Badge className="gap-1.5 py-1" key={domain} variant="secondary">
              {domain}
              <button
                aria-label={`从个人白名单移除 ${domain}`}
                className="text-muted-foreground hover:text-destructive"
                disabled={update.isPending}
                onClick={() => remove(domain)}
                type="button"
              >
                ×
              </button>
            </Badge>
          ))}
        </div>
      ) : (
        <p className="rounded-lg border border-dashed px-3 py-4 text-center text-sm text-muted-foreground">
          暂无个人白名单域名。在聊天授权卡片中选择「以后都允许」后，域名会出现在这里。
        </p>
      )}
    </div>
  );
}

const FETCH_STATUS_FILTERS: Array<{ value: string; label: string }> = [
  { value: "", label: "全部" },
  { value: "pending", label: "待审批" },
  { value: "approved", label: "已允许" },
  { value: "denied", label: "已拒绝" },
];

const FETCH_STATUS_LABELS: Record<string, string> = {
  pending: "待审批",
  approved: "已允许",
  denied: "已拒绝",
};

function decisionLabel(decision: string | null): string | null {
  if (decision === "allow_once") return "允许一次";
  if (decision === "allow_always") return "已入个人白名单";
  if (decision === "deny") return "已拒绝";
  return null;
}

/** 网页抓取审批记录：聊天内授权卡片的持久化历史（fetch_authorization_requests）。 */
function FetchApprovalHistory() {
  const { workspaceId = "" } = useParams();
  const [statusFilter, setStatusFilter] = useState("");
  const queryPrefix = workspaceResourcePrefix(workspaceId, "fetch-authorizations");
  const history = useQuery({
    queryKey: [...queryPrefix, "history", statusFilter],
    queryFn: () => listFetchAuthorizations({ status: statusFilter || undefined, limit: 100 }),
    enabled: Boolean(workspaceId),
  });
  const items = history.data?.items ?? [];
  return (
    <Surface className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SectionHeading
          description="聊天内网页抓取授权卡片的审批记录，持久化存储；「以后都允许」同时写入个人白名单。"
          title="网页抓取审批记录"
        />
        <div className="flex flex-wrap gap-1.5">
          {FETCH_STATUS_FILTERS.map((option) => (
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
      {history.isPending ? (
        <LoadingState label="正在读取审批记录…" />
      ) : history.isError ? (
        <ErrorState message={history.error.message} onRetry={() => void history.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          description={
            statusFilter
              ? "该状态下暂无审批记录"
              : "暂无审批记录。聊天内抓取需要授权时会生成记录并持久化保存"
          }
          title="没有审批记录"
        />
      ) : (
        <ul className="mt-3 divide-y">
          {items.map((request) => {
            const decision = decisionLabel(request.decision);
            return (
              <li key={request.id} className="flex flex-col gap-2 py-3 first:pt-1 last:pb-1">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="text-sm font-semibold">{request.hostname}</code>
                  <StatePill
                    label={FETCH_STATUS_LABELS[request.status] ?? request.status}
                    status={request.status}
                  />
                  {request.decision === "allow_always" && (
                    <Badge variant="outline">已入个人白名单</Badge>
                  )}
                </div>
                {request.requested_url && request.requested_url !== request.hostname && (
                  <p className="min-w-0 truncate text-xs text-muted-foreground">
                    {request.requested_url}
                  </p>
                )}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  <span>请求者：{request.requested_by}</span>
                  <span>创建：{formatTime(request.created_at)}</span>
                  <span>
                    决定：
                    {request.decided_at
                      ? `${decision ?? request.decision ?? "—"} · ${formatTime(request.decided_at)}`
                      : "—"}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {history.data && history.data.total > items.length && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <CircleAlert className="size-3.5" />
          仅显示前 {items.length} 条（共 {history.data.total} 条）
        </div>
      )}
      <div className="flex justify-end">
        <Button
          disabled={history.isFetching}
          onClick={() => void history.refetch()}
          size="sm"
          variant="ghost"
        >
          <RefreshCcw className="size-4" />
          刷新
        </Button>
      </div>
    </Surface>
  );
}

/** 前端沙箱联网审计：MagicCard / HTML 预览沙箱的免审批网络直连记录。 */
function SandboxNetAuditHistory() {
  const { workspaceId = "" } = useParams();
  const queryPrefix = workspaceResourcePrefix(workspaceId, "sandbox-net-audit");
  const audit = useQuery({
    queryKey: [...queryPrefix, "history"],
    queryFn: () => listSandboxNetAudit({ limit: 100 }),
    enabled: Boolean(workspaceId),
  });
  const items = audit.data?.items ?? [];
  return (
    <Surface className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SectionHeading
          description="聊天内 MagicCard / HTML 预览沙箱发起的网络请求（免审批直连，仅记录目标与方法，不含查询参数与凭据）。"
          title="前端沙箱联网记录"
        />
      </div>
      {audit.isPending ? (
        <LoadingState label="正在读取联网记录…" />
      ) : audit.isError ? (
        <ErrorState message={audit.error.message} onRetry={() => void audit.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          description="暂无联网记录。沙箱内代码发起 fetch 后会在这里留下只读记录。"
          title="没有联网记录"
        />
      ) : (
        <ul className="mt-3 divide-y">
          {items.map((entry) => {
            const details = entry.details ?? {};
            const target = typeof details.target === "string" ? details.target : "—";
            const method = typeof details.method === "string" ? details.method : "GET";
            const status = typeof details.status === "number" ? details.status : null;
            const size =
              typeof details.size_bytes === "number" ? `${details.size_bytes} B` : null;
            return (
              <li key={entry.id} className="flex flex-col gap-1.5 py-3 first:pt-1 last:pb-1">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="text-sm font-semibold">{method}</code>
                  <code className="min-w-0 flex-1 truncate text-sm">{target}</code>
                  <StatePill
                    label={
                      entry.outcome === "denied"
                        ? "已拦截"
                        : status != null
                          ? status < 400
                            ? "成功"
                            : status < 500
                              ? `错误 ${status}`
                              : `失败 ${status}`
                          : entry.outcome
                      }
                    status={
                      entry.outcome === "denied" || (status != null && status >= 400)
                        ? "danger"
                        : "healthy"
                    }
                  />
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  <span>时间：{formatTime(entry.created_at)}</span>
                  {size ? <span>大小：{size}</span> : null}
                  {entry.outcome === "denied" && typeof details.reason === "string" ? (
                    <span>原因：{details.reason}</span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <div className="flex justify-end">
        <Button
          disabled={audit.isFetching}
          onClick={() => void audit.refetch()}
          size="sm"
          variant="ghost"
        >
          <RefreshCcw className="size-4" />
          刷新
        </Button>
      </div>
    </Surface>
  );
}

/**
 * 搜索与抓取：统一白名单（搜索 / 抓取 / 出站一层放行）+ 全放行开关 +
 * 网页抓取通道 + 出站审批。替代原「访问与审批」「Egress 审批」「搜索与研究」。
 */
export function SearchFetchPage() {
  const { workspaceId = "" } = useParams();
  const egressPrefix = workspaceResourcePrefix(workspaceId, "egress-approvals");
  const pending = useQuery({
    queryKey: [...egressPrefix, "pending"],
    queryFn: () => listEgressApprovals({ status: "pending", limit: 100 }),
    enabled: Boolean(workspaceId),
  });
  const pendingItems = pending.data?.items ?? [];
  return (
    <PageFrame>
      <PageIntro
        description="统一管理搜索、网页抓取与沙箱出站的拦截策略与审批。"
        eyebrow="Search, fetch & egress"
        title="搜索与抓取"
      />
      <Surface className="space-y-5 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <SectionHeading
            description="一层白名单：白名单内域名，搜索、抓取与出站均不拦截、无需审批。"
            title="拦截策略"
          />
          <StatePill
            label={
              pending.isLoading
                ? "读取中…"
                : pendingItems.length
                  ? `${pendingItems.length} 条待审批`
                  : "无待审批"
            }
            status={pendingItems.length ? "warning" : "healthy"}
          />
        </div>
        <UnifiedAllowlistEditor />
        <div className="border-t pt-5">
          <SectionHeading
            description="聊天授权卡片中选择「以后都允许」写入的域名，仅对当前用户生效。"
            title="我的个人白名单"
          />
          <div className="mt-3">
            <PersonalFetchAllowlist />
          </div>
        </div>
      </Surface>
      <Surface className="space-y-5 p-5">
        <SectionHeading
          description="网页抓取走哪个通道（工作区级，对所有会话生效）。"
          title="网页抓取通道"
        />
        <FetchChannelSettings />
      </Surface>
      <EgressApprovalQueue />
      <FetchApprovalHistory />
      <SandboxNetAuditHistory />
    </PageFrame>
  );
}
