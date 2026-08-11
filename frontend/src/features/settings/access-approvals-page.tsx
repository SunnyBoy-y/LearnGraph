import { useQuery } from "@tanstack/react-query";
import {
  FileSearch,
  Globe,
  Network,
  Search,
  ShieldCheck,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { getWebFetchSettings } from "@/api/fetch-authorizations";
import { listEgressApprovals } from "@/api/egress";
import { listSettings } from "@/api/settings";
import {
  FetchDomainAllowlistEditor,
  ResearchDomainAllowlistEditor,
} from "@/components/shared/domain-allowlist-editor";
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
import { Button } from "@/components/ui/button";
import { workspaceResourcePrefix } from "@/lib/query-keys";
import type { WorkspaceSetting } from "@/types/settings";

function domainsOf(settings: WorkspaceSetting[] | undefined, key: string): string[] {
  const raw = settings?.find((item) => item.key === key)?.value;
  if (!raw || typeof raw !== "object") return [];
  const domains = (raw as { allowed_domains?: unknown }).allowed_domains;
  return Array.isArray(domains)
    ? domains.filter((item): item is string => typeof item === "string")
    : [];
}

/**
 * 访问与审批总览（A-1）：按「应用层 / 网络层」聚合三类授权边界，
 * 一站式查看与编辑网页抓取白名单、搜索来源白名单，并展示沙箱出站待审批队列。
 */
export function AccessApprovalsPage() {
  const { workspaceId = "" } = useParams();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: listSettings,
  });
  const fetchSettings = useQuery({
    queryKey: ["fetch-authorization-settings"],
    queryFn: getWebFetchSettings,
  });
  const egressPrefix = workspaceResourcePrefix(workspaceId, "egress-approvals");
  const pending = useQuery({
    queryKey: [...egressPrefix, "pending"],
    queryFn: () => listEgressApprovals({ status: "pending", limit: 100 }),
    enabled: Boolean(workspaceId),
  });

  const searchDomains = domainsOf(settings.data, "research.policy");
  const fetchDomains = domainsOf(settings.data, "web_fetch.policy");
  const pendingItems = pending.data?.items ?? [];
  const fetchRuntime = fetchSettings.data;

  const channelLabel =
    fetchRuntime?.sandbox_effective && fetchRuntime.effective_channel === "sandbox"
      ? "沙箱隔离抓取"
      : fetchRuntime?.effective_channel === "remote"
        ? "远程抓取 Provider"
        : fetchRuntime?.effective_channel === "hosted"
          ? "Qwen 托管抓取"
          : "未配置可用抓取通道";

  const loading =
    settings.isPending || fetchSettings.isPending || pending.isPending;
  const error = settings.error ?? fetchSettings.error ?? pending.error;

  return (
    <PageFrame>
      <PageIntro
        description="统一查看本工作区的访问授权边界。授权分两层：应用层（搜索/Deep Research 来源白名单、网页抓取白名单）决定「允许哪些域名被查询或抓取」；网络层（沙箱出站 Egress）决定「沙箱容器能访问哪些主机」。两层相互独立，分别审批。"
        eyebrow="Access & approvals"
        title="访问与审批"
      />
      {loading ? (
        <LoadingState />
      ) : error ? (
        <ErrorState message={error.message} />
      ) : (
        <>
          <Surface className="overflow-hidden">
            <div className="border-b p-5">
              <SectionHeading
                description="先判断要放行的是「应用操作」还是「网络访问」，再决定配置哪一层。"
                title="分层边界"
              />
            </div>
            <div className="grid gap-px bg-border sm:grid-cols-3">
              <div className="bg-background p-5">
                <div className="flex items-center gap-2">
                  <Search className="size-4 text-primary" />
                  <p className="text-sm font-semibold">应用层 · 搜索来源</p>
                </div>
                <p className="mt-2 text-xs leading-5 text-muted-foreground">
                  普通联网搜索与 Deep Research 可查询的来源域名
                  （research.policy）。只过滤来源，不授予网络权限。
                </p>
              </div>
              <div className="bg-background p-5">
                <div className="flex items-center gap-2">
                  <Network className="size-4 text-primary" />
                  <p className="text-sm font-semibold">应用层 · 网页抓取</p>
                </div>
                <p className="mt-2 text-xs leading-5 text-muted-foreground">
                  网页抓取可抓取的域名（web_fetch.policy 工作区级，叠加个人
                  「以后都允许」列表）。放行的是抓取操作本身。
                </p>
              </div>
              <div className="bg-background p-5">
                <div className="flex items-center gap-2">
                  <Globe className="size-4 text-primary" />
                  <p className="text-sm font-semibold">网络层 · 沙箱出站</p>
                </div>
                <p className="mt-2 text-xs leading-5 text-muted-foreground">
                  沙箱容器可访问的外部主机（Egress 审批）。内网、环回、云元数据
                  地址始终被代理拒绝，放行后仍逐次重分类。
                </p>
              </div>
            </div>
          </Surface>

          <Surface className="overflow-hidden">
            <div className="border-b p-5">
              <SectionHeading title="当前状态" />
            </div>
            <div className="grid gap-px bg-border sm:grid-cols-3">
              <div className="bg-background p-5">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium">搜索来源白名单</p>
                  <StatePill
                    label={searchDomains.length ? "已配置" : "未设置"}
                    status={searchDomains.length ? "healthy" : "degraded"}
                  />
                </div>
                <p className="mt-2 text-2xl font-semibold">{searchDomains.length}</p>
                <p className="mt-1 text-[11px] text-muted-foreground">个来源域名</p>
                <Button asChild className="mt-3" size="xs" variant="outline">
                  <Link to={`/w/${workspaceId}/settings/research`}>
                    <FileSearch className="size-3" />
                    管理 Provider 与来源
                  </Link>
                </Button>
              </div>
              <div className="bg-background p-5">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium">网页抓取</p>
                  <StatePill
                    label={fetchDomains.length ? "已配置" : "未设置"}
                    status={fetchDomains.length ? "healthy" : "degraded"}
                  />
                </div>
                <p className="mt-2 text-2xl font-semibold">{fetchDomains.length}</p>
                <p className="mt-1 text-[11px] text-muted-foreground">
                  个工作区抓取域名 · 生效通道：{channelLabel}
                  {fetchRuntime?.sandbox_enabled ? " · 沙箱开" : " · 沙箱关"}
                </p>
                <Button asChild className="mt-3" size="xs" variant="outline">
                  <Link to={`/w/${workspaceId}/settings/providers`}>
                    抓取通道与优先级
                  </Link>
                </Button>
              </div>
              <div className="bg-background p-5">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium">沙箱出站审批</p>
                  <StatePill
                    label={pendingItems.length ? `${pendingItems.length} 条待审批` : "无待办"}
                    status={pendingItems.length ? "warning" : "healthy"}
                  />
                </div>
                <p className="mt-2 text-2xl font-semibold">{pendingItems.length}</p>
                <p className="mt-1 text-[11px] text-muted-foreground">条待审批请求</p>
                <Button asChild className="mt-3" size="xs" variant="outline">
                  <Link to={`/w/${workspaceId}/settings/egress`}>
                    <ShieldCheck className="size-3" />
                    查看审批队列
                  </Link>
                </Button>
              </div>
            </div>
          </Surface>

          <Surface className="space-y-5 p-5">
            <SectionHeading
              description="网页抓取可抓取的精确公共 DNS 域名（工作区级）。"
              title="网页抓取白名单"
            />
            <FetchDomainAllowlistEditor />
          </Surface>

          <Surface className="space-y-5 p-5">
            <SectionHeading
              description="普通联网搜索与 Deep Research 可使用的工作区来源域名。"
              title="搜索与 Deep Research 来源白名单"
            />
            <ResearchDomainAllowlistEditor />
          </Surface>

          <Surface className="overflow-hidden">
            <div className="border-b p-5">
              <SectionHeading
                description="网络层沙箱出站审批队列中的待办请求。"
                title="待审批出站请求"
              />
            </div>
            {pendingItems.length ? (
              <div className="divide-y">
                {pendingItems.slice(0, 6).map((item) => (
                  <div
                    className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm"
                    key={item.id}
                  >
                    <code className="rounded bg-muted px-2 py-1 font-mono text-xs">
                      {item.hostname}
                    </code>
                    <span className="text-xs text-muted-foreground">
                      {item.capability}
                    </span>
                    <span className="ml-auto text-xs text-muted-foreground">
                      {new Date(item.created_at).toLocaleString()}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-5">
                <EmptyState
                  description="没有待审批的出站请求。"
                  title="队列为空"
                />
              </div>
            )}
            <div className="border-t p-4">
              <Button asChild size="sm" variant="outline">
                <Link to={`/w/${workspaceId}/settings/egress`}>
                  前往 Egress 审批（新建 / 全部记录）
                </Link>
              </Button>
            </div>
          </Surface>
        </>
      )}
    </PageFrame>
  );
}
