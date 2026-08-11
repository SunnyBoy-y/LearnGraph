import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import {
  getWebFetchSettings,
  updateWebFetchSettings,
} from "@/api/fetch-authorizations";
import type { WebFetchChannel } from "@/types/fetch-authorization";
import {
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

const FETCH_CHANNEL_LABELS: Record<WebFetchChannel, string> = {
  sandbox: "沙箱隔离抓取（隔离容器）",
  remote: "远程抓取 Provider（Crawl4AI / Firecrawl）",
  hosted: "Qwen 托管抓取（Responses 工具）",
};

const FETCH_CHANNEL_HINTS: Record<WebFetchChannel, string> = {
  sandbox: "在隔离容器中抓取并解析网页，不接触主机进程；需要已配置网页抓取白名单。",
  remote: "通过已配置的 Crawl4AI / Firecrawl 远程抓取服务抓取网页。",
  hosted: "由 Qwen 模型云端执行网页抓取与正文提取，按工具调用计费。",
};

/**
 * 常驻「网页抓取」设置卡片（Provider 管理页顶部）。
 * 工作区级：沙箱抓取开关 + 抓取通道优先级，保存到 web_fetch.runtime。
 */
export function WebFetchSettingsCard() {
  const queryClient = useQueryClient();
  const { workspaceId = "" } = useParams();
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
  const effectiveLabel =
    data?.sandbox_effective && data.effective_channel === "sandbox"
      ? "沙箱隔离抓取"
      : data?.effective_channel === "remote"
        ? "远程抓取 Provider"
        : data?.effective_channel === "hosted"
          ? "Qwen 托管抓取"
          : "当前无可用抓取通道";
  return (
    <Surface className="overflow-hidden">
      <div className="border-b p-5">
        <SectionHeading title="网页抓取" />
        <p className="mt-1 text-sm text-muted-foreground">
          抓取已授权网页的通道与优先级（工作区级，对所有会话生效）。
        </p>
      </div>
      <div className="space-y-5 p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <p className="text-sm font-medium">沙箱隔离抓取</p>
            <p className="text-sm text-muted-foreground">
              开启后优先在隔离容器中抓取网页，避免在主机进程解析不可信 HTML。
            </p>
            {data && !data.global_sandbox_gate ? (
              <p className="text-xs text-destructive" role="alert">
                全局沙箱抓取开关（LEARNGRAPH_SANDBOX_WEB_FETCH_ENABLED）已关闭，
                即使开启本开关也不会使用沙箱通道。
              </p>
            ) : null}
            {data && data.sandbox_enabled && data.global_sandbox_gate && data.allowlist_count === 0 ? (
              <p className="text-xs text-muted-foreground">
                尚未配置网页抓取白名单域名，沙箱通道暂不可用。
              </p>
            ) : null}
          </div>
          <Switch
            checked={sandboxEnabled}
            disabled={save.isPending}
            onCheckedChange={setSandboxEnabled}
          />
        </div>
        <div className="space-y-3">
          <p className="text-sm font-medium">抓取优先级（自上而下依次尝试，不可用时自动回退）</p>
          {(["sandbox", "remote", "hosted"] as const).map((channel) => {
            const rank = priority.indexOf(channel) + 1;
            return (
              <div
                className="flex items-center justify-between gap-4 rounded-xl border border-muted bg-muted/20 px-4 py-3"
                key={channel}
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium">{FETCH_CHANNEL_LABELS[channel]}</p>
                  <p className="text-xs text-muted-foreground">{FETCH_CHANNEL_HINTS[channel]}</p>
                </div>
                <Select
                  disabled={save.isPending}
                  onValueChange={(value) =>
                    setPriority(withRank(channel, Number(value)))
                  }
                  value={String(rank)}
                >
                  <SelectTrigger className="w-36">
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
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <StatePill
              label={`当前生效通道：${effectiveLabel}`}
              status={data?.effective_channel ? "healthy" : "failed"}
            />
            {data?.sandbox_effective ? (
              <span className="text-xs text-muted-foreground">
                沙箱通道已就绪（白名单 {data.allowlist_count} 个域名）
              </span>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild size="sm" variant="outline">
              <Link to={`/w/${workspaceId}/settings/access-approvals`}>
                网页抓取白名单与总览
              </Link>
            </Button>
            <Button
              disabled={!dirty || save.isPending}
              onClick={() =>
                save.mutate({ sandbox_enabled: sandboxEnabled, priority })
              }
              size="sm"
            >
              {save.isPending ? "保存中…" : "保存设置"}
            </Button>
          </div>
        </div>
      </div>
    </Surface>
  );
}
