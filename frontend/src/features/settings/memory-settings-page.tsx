import {
getMemoryEnhancement,
getMemoryPolicy,
listProviderCatalog,
listProviders,
listSessions,
pruneMemoryEmbeddings,
reindexMemoryEmbeddings,
updateMemoryEnhancement,
updateMemoryPolicy
} from "@/api";
import {
ErrorState,
LoadingState,
PageFrame,
PageIntro,
SectionHeading,
Surface
} from "@/components/shared/page-elements";
import { SessionCombobox } from "@/components/shared/session-combobox";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useAuth } from "@/features/auth/auth-context-value";
import { MemorySettingsExportPanel } from "@/features/memory/components/memory-settings-export-panel";
import { workspaceQueryKey } from "@/lib/query-keys";
import type {
MemoryEmbeddingStaleInfo,
MemoryEnhancement,
MemoryEnhancementUpdateRequest,
MemoryPolicyUpdateRequest,
} from "@/types/memory";
import { useMutation,useQuery,useQueryClient } from "@tanstack/react-query";
import {
AlertTriangle,
RefreshCcw,
Share2,
Trash2
} from "lucide-react";
import { useEffect,useMemo,useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { AdvancedSettings,SettingRow,SettingsSection } from './settings-primitives';
function useMemorySettingsPage() {
const auth = useAuth();
const requestOptions = { headers: { 'X-Workspace-ID': auth.workspaceId } };
const queryClient = useQueryClient();
const providers = useQuery({
    queryKey: workspaceQueryKey(auth.workspaceId, "providers"),
    queryFn: listProviders,
    staleTime: 30_000,
  });
const memoryEnhancement = useQuery({
    queryKey: workspaceQueryKey(auth.workspaceId, "memory-enhancement"),
    queryFn: getMemoryEnhancement,
    staleTime: 30_000,
  });
const saveMemoryEnhancement = useMutation({
    onMutate: () => auth.workspaceId,
    mutationFn: (updates: MemoryEnhancementUpdateRequest) =>
      updateMemoryEnhancement(updates, requestOptions),
    onError: (error) => toast.error(error.message),
    onSuccess: (view, _updates, workspaceId) => {
      queryClient.setQueryData(workspaceQueryKey(workspaceId!, "memory-enhancement"), view);
      toast.success(
        view.cache_invalidated
          ? "已切换 Embedding 模型：语义召回已降级，需重建索引"
          : "记忆设置已更新",
      );
    },
  });
const reindexEmbeddings = useMutation({
    onMutate: () => auth.workspaceId,
    mutationFn: ({ pruneStale = false }: { pruneStale?: boolean } = {}) =>
      reindexMemoryEmbeddings(pruneStale, requestOptions),
    onError: (error) => toast.error(error.message),
    onSuccess: async (result, _variables, workspaceId) => {
      const pruneNote =
        result.stale_freed > 0 ? `，清理旧缓存 ${result.stale_freed} 条` : "";
      const truncatedNote = result.truncated
        ? "（记忆过多，仅处理部分，请再次重建）"
        : "";
      toast.success(
        `索引完成：新嵌入 ${result.embedded} 条 / 已有 ${result.already_indexed} 条${pruneNote}${truncatedNote}`,
      );
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId!, "memory-enhancement") });
    },
  });
const pruneEmbeddings = useMutation({
    onMutate: () => auth.workspaceId,
    mutationFn: () => pruneMemoryEmbeddings(requestOptions),
    onError: (error) => toast.error(error.message),
    onSuccess: async (result, _variables, workspaceId) => {
      toast.success(
        result.freed_count > 0
          ? `已清理旧模型缓存 ${result.freed_count} 条`
          : "没有可清理的旧模型缓存",
      );
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId!, "memory-enhancement") });
    },
  });
const cacheInvalidated = memoryEnhancement.data?.cache_invalidated ?? null;
const staleModelKeys: MemoryEmbeddingStaleInfo[] =
    memoryEnhancement.data?.stale_model_keys ?? [];
const dismissCacheInvalidated = () => {
    const current = memoryEnhancement.data;
    if (current) {
      queryClient.setQueryData<MemoryEnhancement>(workspaceQueryKey(auth.workspaceId, "memory-enhancement"), {
        ...current,
        cache_invalidated: null,
      });
    }
  };
const providerCatalog = useQuery({
    queryKey: ["provider-catalog"],
    queryFn: listProviderCatalog,
    staleTime: 30_000,
  });
const memoryPolicy = useQuery({
    queryKey: workspaceQueryKey(auth.workspaceId, "memory-policy"),
    queryFn: () => getMemoryPolicy(),
    staleTime: 30_000,
  });
const sessions = useQuery({
    queryKey: workspaceQueryKey(auth.workspaceId, "sessions"),
    queryFn: listSessions,
    staleTime: 30_000,
  });
const [policySessionId, setPolicySessionId] = useState("");
useEffect(() => {
    if (sessions.data && !sessions.data.some((item) => item.id === policySessionId)) {
      setPolicySessionId(sessions.data[0]?.id ?? "");
    }
  }, [policySessionId, sessions.data]);
const sessionPolicy = useQuery({
    queryKey: workspaceQueryKey(auth.workspaceId, "memory-policy", policySessionId),
    queryFn: () => getMemoryPolicy(policySessionId),
    staleTime: 30_000,
    enabled: Boolean(policySessionId) && Boolean(sessions.data?.some((item) => item.id === policySessionId)),
  });
const savePolicy = useMutation({
    onMutate: () => auth.workspaceId,
    mutationFn: (patch: MemoryPolicyUpdateRequest) => updateMemoryPolicy(patch, requestOptions),
    onError: (error) => toast.error(error.message),
    onSuccess: async (_result, _variables, workspaceId) => {
      toast.success("共同记忆策略已持久化");
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId!, "memory-policy") });
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId!, "sessions") });
    },
  });
const embeddingProviders = useMemo(() => {
    const roleByType = new Map(
      (providerCatalog.data ?? []).map((item) => [item.provider_type, item.role]),
    );
    const rows = (providers.data ?? []).filter(
      (provider) =>
        provider.enabled && roleByType.get(provider.provider_type) === "embedding",
    );
    // A previously configured provider (for example an OpenAI-compatible chat
    // row that also serves /embeddings) stays selectable after this list
    // narrowed to the dedicated embedding role.
    const configuredId = memoryEnhancement.data?.embedding.provider_id;
    if (configuredId && !rows.some((row) => row.id === configuredId)) {
      const configured = (providers.data ?? []).find(
        (row) => row.id === configuredId,
      );
      if (configured) rows.push(configured);
    }
    return rows;
  }, [providerCatalog.data, providers.data, memoryEnhancement.data]);
const selectedEmbeddingProvider = embeddingProviders.find(
    (provider) => provider.id === memoryEnhancement.data?.embedding.provider_id,
  );
return { memoryEnhancement, memoryPolicy, providers, auth, savePolicy, sessions, setPolicySessionId, policySessionId, sessionPolicy, saveMemoryEnhancement, selectedEmbeddingProvider, cacheInvalidated, reindexEmbeddings, dismissCacheInvalidated, staleModelKeys, pruneEmbeddings };
}
export function MemorySettingsPage() {
    const { memoryEnhancement, memoryPolicy, providers, auth, savePolicy, sessions, setPolicySessionId, policySessionId, sessionPolicy, saveMemoryEnhancement, selectedEmbeddingProvider, cacheInvalidated, reindexEmbeddings, dismissCacheInvalidated, staleModelKeys, pruneEmbeddings } = useMemorySettingsPage();
    if (memoryEnhancement.isPending || memoryPolicy.isPending)
        return <LoadingState />;
    const readError = memoryEnhancement.error ?? memoryPolicy.error;
    if (readError)
        return <ErrorState message={readError.message}/>;
    const embeddingReady = Boolean(selectedEmbeddingProvider?.enabled && memoryEnhancement.data?.embedding.model_id);
    return (<PageFrame><PageIntro title="记忆与检索" description="管理长期记忆的读取、整理与检索。会话摘要在对话与学习中设置。"/><SettingsSection title="记忆总览" description=""><p className="text-sm">当前工作区：{auth.workspaceName || auth.workspaceId}</p>{memoryPolicy.data ? <p className="text-sm">共同记忆：{memoryPolicy.data.workspace_enabled ? "已开启" : "已关闭"}</p> : null}{memoryEnhancement.data ? <p className="text-sm text-muted-foreground">活跃记忆 {memoryEnhancement.data.active_memories} 条 · 已索引 {memoryEnhancement.data.indexed_memories} 条</p> : null}</SettingsSection><Surface className="p-5">
        <SectionHeading description="分别控制是否读取已有记忆、是否从当前会话学习；临时会话可同时关闭" title="记忆策略"/>
        <div className="mt-4 space-y-5">
          <div className="rounded-xl border p-4">
            <SettingRow title="工作区共同记忆" description="总开关关闭后，读取与学习停止；子设置保留，重新开启后恢复。"><Switch aria-label="工作区共同记忆" checked={memoryPolicy.data?.workspace_enabled ?? false} disabled={memoryPolicy.isPending || savePolicy.isPending} onCheckedChange={(workspace_enabled) => savePolicy.mutate({ workspace_enabled })}/></SettingRow>
            <div className="mt-4 space-y-3 border-t pt-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">在回答中读取已有记忆</span>
                <Switch aria-label="工作区读取记忆" checked={memoryPolicy.data?.workspace_recall_enabled ?? true} disabled={!memoryPolicy.data?.workspace_enabled || memoryPolicy.isPending || savePolicy.isPending} onCheckedChange={(workspace_recall_enabled) => savePolicy.mutate({ workspace_recall_enabled })}/>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">从新对话中整理记忆</span>
                <Switch aria-label="工作区学习记忆" checked={memoryPolicy.data?.workspace_learning_enabled ?? true} disabled={!memoryPolicy.data?.workspace_enabled || memoryPolicy.isPending || savePolicy.isPending} onCheckedChange={(workspace_learning_enabled) => savePolicy.mutate({ workspace_learning_enabled })}/>
              </div>
            </div>
          </div>
          <div className="rounded-xl border p-4">
            <div className="flex items-center gap-3">
              <Share2 className="size-5 shrink-0 text-primary"/>
              <div className="min-w-0">
                <p className="text-sm font-medium">会话策略</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  为单个会话单独开启或关闭共同记忆
                </p>
              </div>
            </div>
            {sessions.isPending ? <p role="status" className="mt-3 text-xs text-muted-foreground">正在读取会话策略…</p> : sessions.error ? <p role="alert" className="mt-3 text-xs text-destructive">会话列表读取失败：{sessions.error.message}</p> : sessions.data?.length ? (<>
                <SessionCombobox className="mt-3" onChange={setPolicySessionId} sessions={sessions.data} value={policySessionId}/>
                <div className="mt-3 flex items-center justify-between gap-3">
                  <span className="text-xs text-muted-foreground">
                    {sessionPolicy.isError
                ? `Session 策略读取失败：${sessionPolicy.error.message}`
                : sessionPolicy.isPending ? "正在读取策略…" : sessionPolicy.data?.effective_enabled
                    ? "当前有效"
                    : "当前隔离"}
                  </span>
                  <Switch aria-label="Session 共同记忆" checked={sessionPolicy.data?.session_enabled ?? false} disabled={!memoryPolicy.data?.workspace_enabled || memoryPolicy.isError || sessionPolicy.isError ||
                !policySessionId ||
                sessionPolicy.isPending ||
                savePolicy.isPending} onCheckedChange={(session_enabled) => savePolicy.mutate({
                session_id: policySessionId,
                session_enabled,
            })}/>
                </div>
                <div className="mt-3 space-y-3 border-t pt-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs text-muted-foreground">读取已有记忆</span>
                    <Switch aria-label="Session 读取记忆" checked={sessionPolicy.data?.session_recall_enabled ?? true} disabled={!memoryPolicy.data?.workspace_enabled || memoryPolicy.isError || sessionPolicy.isError || !sessionPolicy.data?.session_enabled || memoryPolicy.data?.workspace_recall_enabled === false || !policySessionId || sessionPolicy.isPending || savePolicy.isPending} onCheckedChange={(session_recall_enabled) => savePolicy.mutate({
                session_id: policySessionId,
                session_recall_enabled,
            })}/>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-xs text-muted-foreground">贡献新的记忆</span>
                    <Switch aria-label="Session 学习记忆" checked={sessionPolicy.data?.session_learning_enabled ?? true} disabled={!memoryPolicy.data?.workspace_enabled || memoryPolicy.isError || sessionPolicy.isError || !sessionPolicy.data?.session_enabled || memoryPolicy.data?.workspace_learning_enabled === false || !policySessionId || sessionPolicy.isPending || savePolicy.isPending} onCheckedChange={(session_learning_enabled) => savePolicy.mutate({
                session_id: policySessionId,
                session_learning_enabled,
            })}/>
                  </div>
                </div>
              </>) : (<p className="mt-3 text-xs text-muted-foreground">
                当前没有 Session；创建会话后可设置独立策略。
              </p>)}
          </div>
        </div>
      </Surface><SettingsSection title="记忆整理与写入" description=""><SettingRow title="记忆整理" description="对话空闲后自动提炼长期记忆；需先开启工作区的记忆学习。"><Switch aria-label="记忆整理" checked={memoryEnhancement.data?.extraction.enabled ?? false} disabled={!memoryPolicy.data?.workspace_enabled || memoryPolicy.data?.workspace_learning_enabled === false || memoryEnhancement.isPending || saveMemoryEnhancement.isPending} onCheckedChange={(enabled) => saveMemoryEnhancement.mutate({ extraction: { enabled } })}/></SettingRow><div className="mt-3 flex items-center justify-between gap-3 border-t pt-3">
              <span className="text-xs text-muted-foreground">
                高置信新记忆自动写入 Active Memory
              </span>
              <Switch aria-label="高置信新记忆自动写入 Active Memory" checked={memoryEnhancement.data?.extraction.auto_commit ?? true} disabled={!memoryPolicy.data?.workspace_enabled || memoryPolicy.data?.workspace_learning_enabled === false || !memoryEnhancement.data?.extraction.enabled || memoryEnhancement.isPending || saveMemoryEnhancement.isPending} onCheckedChange={(auto_commit) => saveMemoryEnhancement.mutate({ extraction: { auto_commit } })}/>
            </div></SettingsSection><SettingsSection title="检索配置" description=""><SettingRow title="语义检索（Embedding）" description="按语义相似度增强召回；未配置模型时使用默认召回。"><Switch aria-label="语义检索" checked={memoryEnhancement.data?.embedding.enabled ?? false} disabled={memoryEnhancement.isPending || saveMemoryEnhancement.isPending} onCheckedChange={(enabled) => saveMemoryEnhancement.mutate({ embedding: { enabled } })}/></SettingRow>{memoryEnhancement.data?.embedding.enabled && !providers.isPending && (!selectedEmbeddingProvider?.enabled || !memoryEnhancement.data.embedding.model_id) ? <p role="status" className="mt-3 text-sm text-muted-foreground">尚未配置可用的 Embedding 模型，当前保持默认召回。</p> : null}<Button asChild variant="outline" size="sm"><Link to={`/w/${auth.workspaceId}/settings/models?tab=tasks`}>配置检索模型</Link></Button></SettingsSection><SettingsSection title="索引管理" description="重建索引与清理旧模型向量缓存不会删除记忆内容。">{cacheInvalidated ? (<div className="mt-3 rounded-lg border border-amber-300 bg-amber-50/70 px-3 py-2 text-xs text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                <p className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0"/>
                  <span>
                    已切换到{" "}
                    <code className="font-mono">
                      {cacheInvalidated.current_model_key}
                    </code>
                    ：新模型下的记忆尚未索引（已索引 0 /{" "}
                    {memoryEnhancement.data?.active_memories ?? 0}），语义召回暂时降级。
                    旧模型缓存{" "}
                    <code className="font-mono">
                      {cacheInvalidated.previous_model_key}
                    </code>{" "}
                    共 {cacheInvalidated.previous_indexed_count} 条待清理。
                  </span>
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button disabled={reindexEmbeddings.isPending || pruneEmbeddings.isPending || !embeddingReady || !memoryEnhancement.data?.embedding.enabled} onClick={() => reindexEmbeddings.mutate({ pruneStale: true })} size="sm" variant="outline">
                    <RefreshCcw className={reindexEmbeddings.isPending
                ? "size-4 animate-spin"
                : "size-4"}/>
                    重建索引并清理旧缓存
                  </Button>
                  <Button disabled={reindexEmbeddings.isPending} onClick={dismissCacheInvalidated} size="sm" variant="ghost">
                    稍后处理
                  </Button>
                </div>
              </div>) : null}<div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t pt-3">
              <div className="min-w-0">
                <p className="text-xs text-muted-foreground">
                  已索引 {memoryEnhancement.data?.indexed_memories ?? 0} /{" "}
                  {memoryEnhancement.data?.active_memories ?? 0} 条活跃记忆
                  {memoryEnhancement.data?.current_model_key ? (<>
                      {" · "}当前模型{" "}
                      <code className="font-mono">
                        {memoryEnhancement.data.current_model_key}
                      </code>
                    </>) : null}
                </p>
                {staleModelKeys.length > 0 ? (<p className="mt-0.5 text-[10px] text-muted-foreground">
                    旧模型缓存待清理：{" "}
                    {staleModelKeys
                .map((item) => `${item.model_key} ${item.indexed_count} 条`)
                .join("、")}
                  </p>) : null}
              </div>
              <div className="flex flex-none items-center gap-2">
                <Button disabled={pruneEmbeddings.isPending || reindexEmbeddings.isPending ||
            staleModelKeys.length === 0 ||
            !(memoryEnhancement.data?.embedding.enabled ?? false)} onClick={() => pruneEmbeddings.mutate()} size="sm" variant="outline">
                  <Trash2 className="size-3.5"/>
                  清理旧缓存
                </Button>
                <Button disabled={!embeddingReady || reindexEmbeddings.isPending || pruneEmbeddings.isPending ||
            !(memoryEnhancement.data?.embedding.enabled ?? false)} onClick={() => reindexEmbeddings.mutate({ pruneStale: false })} size="sm" variant="outline">
                  <RefreshCcw className={reindexEmbeddings.isPending
            ? "size-4 animate-spin"
            : "size-4"}/>
                  重建索引
                </Button>
              </div>
            </div></SettingsSection><SettingsSection title="记忆管理" description=""><Button asChild variant="outline"><Link to={`/w/${auth.workspaceId}/memory`}>管理可恢复记忆</Link></Button></SettingsSection><AdvancedSettings title="高级检索设置" description="架构状态、事件清单与重放校验"><MemorySettingsExportPanel /></AdvancedSettings></PageFrame>);
}
