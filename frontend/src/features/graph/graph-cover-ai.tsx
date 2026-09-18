import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { Ban, Image as ImageIcon, PenTool, Sparkles } from "lucide-react";
import { toast } from "sonner";

import {
  cancelAIGraphCover,
  draftAIGraphCover,
  getAIGraphCoverStatus,
  startAIGraphCover,
} from "@/api/graphs";
import { listProviders } from "@/api/providers";
import { listSettings, updateSetting } from "@/api/settings";
import { Button } from "@/components/ui/button";
import {
  FEATURE_MODEL_DEFAULT,
  SearchableFeatureModelSelect,
  featureModelValue,
  parseFeatureModelValue,
  useFeatureModelChoices,
  withConfiguredModelChoice,
} from "@/components/shared/feature-model-select";
import { providerCapabilityString } from "@/lib/model-choices";
import { workspaceQueryKey } from "@/lib/query-keys";
import {
  FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY,
  GRAPH_COVER_ENGINE_SETTING_KEY,
  GRAPH_COVER_MODEL_SETTING_KEY,
  readChatFeatureModelSetting,
  readGraphCoverEngine,
} from "@/lib/workspace-settings";
import type { GraphCoverDraftSource, GraphCoverEngine } from "@/types/graphs";
import type { WorkspaceSetting } from "@/types/settings";

/**
 * 「AI 生成封面」：两阶段、两引擎。
 *
 * 阶段一（拟草案）只花文本模型的钱，产出交给用户读和改；阶段二（确认生成）才
 * 提交后台任务，关掉弹窗/切页/刷新浏览器都不影响，完成后书架自动刷新。
 *
 * 引擎可选且可配置：默认引擎写在 workspace setting `graph.cover_engine` 里；
 * 矢量引擎的模型写在 `graph.cover_model`（与「教学包生成模型」同形）；位图引擎
 * 的模型就是「功能模型 → 图片生成」那一份 `models.functional_defaults.image_generation`，
 * 与后端 `image_provider_for_workspace()` 解析的是同一个来源。
 *
 * 后台任务的"还在跑"状态有两条来源：图谱列表接口的 `cover_ai_active`（刷新后
 * 仍然成立），以及这里对单个图谱的任务轮询（只在需要时开启）。
 */

/** 图片生成 Provider 的挑选口径与对话页一致：openai_images 或声明了
 * image_generation 角色，且已配置默认图片模型。 */
function useImageModelChoices() {
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: listProviders,
    staleTime: 30_000,
  });
  const choices = useMemo(() => {
    const items: Array<{ value: string; label: string }> = [];
    for (const provider of providers.data ?? []) {
      if (!provider.enabled) continue;
      const isImage =
        provider.provider_type === "openai_images" ||
        providerCapabilityString(provider, "provider_role") === "image_generation";
      if (!isImage) continue;
      const defaultModel = providerCapabilityString(
        provider,
        "default_image_generation_model_id",
      );
      if (!defaultModel) continue;
      items.push({
        value: `${provider.id}::${defaultModel}`,
        label: `${provider.display_name} · ${defaultModel}`,
      });
    }
    return items;
  }, [providers.data]);
  return { choices, providers: providers.data ?? [] };
}

function readFunctionalImageTarget(settings: WorkspaceSetting[] | undefined) {
  const raw = settings?.find(
    (item) => item.key === FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY,
  )?.value as Record<string, unknown> | undefined;
  const target = raw?.image_generation as
    | { provider_id?: string; model_id?: string }
    | undefined;
  const providerId = target?.provider_id?.trim();
  const modelId = target?.model_id?.trim();
  if (!providerId || !modelId) return { providerId: null, modelId: null };
  return { providerId, modelId };
}

function CoverImageModelSelect({
  workspaceId,
  disabled,
}: {
  workspaceId: string;
  disabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const settingsKey = workspaceQueryKey(workspaceId, "settings");
  const settings = useQuery({
    queryKey: settingsKey,
    queryFn: listSettings,
    staleTime: 30_000,
  });
  const { choices } = useImageModelChoices();
  const current = readFunctionalImageTarget(settings.data);
  const save = useMutation({
    mutationFn: (value: { provider_id: string | null; model_id: string | null }) => {
      // 整值替换：只动 image_generation 分支，其余功能模型原样保留。
      const existing = (
        settings.data?.find(
          (item) => item.key === FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY,
        )?.value ?? {}
      ) as Record<string, unknown>;
      return updateSetting(FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY, {
        ...existing,
        image_generation:
          value.provider_id && value.model_id
            ? { provider_id: value.provider_id, model_id: value.model_id }
            : null,
      });
    },
    onSuccess: (setting) => {
      queryClient.setQueryData<WorkspaceSetting[]>(settingsKey, (items) => [
        ...(items ?? []).filter((item) => item.key !== setting.key),
        setting,
      ]);
      toast.success("封面使用的图片模型已更新");
    },
    onError: (error: Error) => toast.error(error.message),
  });
  return (
    <select
      aria-label="封面图片模型"
      className="h-9 rounded-lg border bg-background px-2 text-sm"
      disabled={disabled || save.isPending}
      onChange={(event) => {
        const value = event.target.value;
        if (!value) {
          save.mutate({ provider_id: null, model_id: null });
          return;
        }
        const [providerId, modelId] = value.split("::");
        save.mutate({ provider_id: providerId, model_id: modelId });
      }}
      value={current.providerId ? `${current.providerId}::${current.modelId}` : ""}
    >
      <option value="">未指定（用第一个可用的图片 Provider）</option>
      {choices.map((choice) => (
        <option key={choice.value} value={choice.value}>
          {choice.label}
        </option>
      ))}
    </select>
  );
}

function CoverTextModelSelect({
  workspaceId,
  disabled,
}: {
  workspaceId: string;
  disabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const settingsKey = workspaceQueryKey(workspaceId, "settings");
  const settings = useQuery({
    queryKey: settingsKey,
    queryFn: listSettings,
    staleTime: 30_000,
  });
  const { choices, providers } = useFeatureModelChoices();
  const current = readChatFeatureModelSetting(
    settings.data,
    GRAPH_COVER_MODEL_SETTING_KEY,
  );
  const options = useMemo(
    () => [
      { value: FEATURE_MODEL_DEFAULT, label: "跟随对话模型" },
      ...withConfiguredModelChoice(
        choices,
        current.provider_id ?? "",
        current.model_id ?? "",
        providers.find((item) => item.id === current.provider_id)?.display_name,
      ).map((choice) => ({ value: choice.value, label: choice.label })),
    ],
    [choices, current.model_id, current.provider_id, providers],
  );
  const save = useMutation({
    mutationFn: (next: { provider_id: string | null; model_id: string | null }) =>
      updateSetting(GRAPH_COVER_MODEL_SETTING_KEY, next),
    onSuccess: (setting) => {
      queryClient.setQueryData<WorkspaceSetting[]>(settingsKey, (items) => [
        ...(items ?? []).filter((item) => item.key !== setting.key),
        setting,
      ]);
      toast.success("封面生成模型已更新");
    },
    onError: (error: Error) => toast.error(error.message),
  });
  return (
    <SearchableFeatureModelSelect
      ariaLabel="封面生成模型（矢量）"
      disabled={disabled || save.isPending}
      onValueChange={(next) => {
        const parsed = parseFeatureModelValue(next);
        save.mutate({
          provider_id: parsed.provider_id ?? null,
          model_id: parsed.model_id ?? null,
        });
      }}
      options={options}
      placeholder="跟随对话模型"
      value={featureModelValue(current.provider_id, current.model_id)}
    />
  );
}

export function GraphCoverAIEditor({
  graphId,
  busy,
}: {
  graphId: string;
  busy?: boolean;
}) {
  const { workspaceId = "" } = useParams();
  const queryClient = useQueryClient();
  const settingsKey = workspaceQueryKey(workspaceId, "settings");
  const settings = useQuery({
    queryKey: settingsKey,
    queryFn: listSettings,
    staleTime: 30_000,
  });
  const [engine, setEngine] = useState<GraphCoverEngine>("svg");
  const [engineTouched, setEngineTouched] = useState(false);
  const [hint, setHint] = useState("");
  const [draft, setDraft] = useState("");
  const [draftSource, setDraftSource] = useState<GraphCoverDraftSource>("model");

  // 默认引擎来自工作区设置；用户在本弹窗里改过就以本次选择为准。
  useEffect(() => {
    if (engineTouched) return;
    setEngine(readGraphCoverEngine(settings.data));
  }, [engineTouched, settings.data]);

  const jobKey = workspaceQueryKey(workspaceId, "graph-cover-ai", graphId);
  const job = useQuery({
    queryKey: jobKey,
    queryFn: () => getAIGraphCoverStatus(graphId),
    refetchInterval: (query) => (query.state.data?.active ? 3000 : false),
  });
  const active = Boolean(job.data?.active);
  /** 同一个任务只结算一次：避免每次重新取数都重复提示/重复刷新。 */
  const settledJobId = useRef<string | null>(null);

  // 任务结束的那一刻刷新书架与图谱；失败由常驻的角标组件负责提示，这里不重复。
  useEffect(() => {
    const data = job.data;
    if (!data || data.active || !data.id) return;
    if (data.status !== "ready" && data.status !== "failed") return;
    if (settledJobId.current === data.id) return;
    settledJobId.current = data.id;
    void queryClient.invalidateQueries({
      queryKey: workspaceQueryKey(workspaceId, "graphs"),
    });
    void queryClient.invalidateQueries({
      queryKey: workspaceQueryKey(workspaceId, "graph", graphId),
    });
  }, [graphId, job.data, queryClient, workspaceId]);

  const draftMutation = useMutation({
    mutationFn: () => draftAIGraphCover(graphId, { engine, hint }),
    onSuccess: (value) => {
      setDraft(value.prompt);
      setDraftSource(value.prompt_source);
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const startMutation = useMutation({
    mutationFn: () =>
      startAIGraphCover(graphId, {
        engine,
        prompt: draft,
        prompt_source: draftSource,
      }),
    onSuccess: (value) => {
      queryClient.setQueryData(jobKey, value);
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "graphs"),
      });
      toast.success("已提交后台生成，完成后封面会自动更新");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const cancelMutation = useMutation({
    mutationFn: () => cancelAIGraphCover(graphId),
    onSuccess: (value) => {
      queryClient.setQueryData(jobKey, value);
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "graphs"),
      });
      toast.success("已取消本次生成");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const engineLocked = busy || active || draftMutation.isPending || startMutation.isPending;
  const failure = !active && job.data?.status === "failed" ? job.data.error : null;

  return (
    <div className="space-y-3 rounded-lg border p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Sparkles className="size-4" />
        AI 生成（会产生模型费用）
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={engineLocked}
          onClick={() => {
            setEngine("svg");
            setEngineTouched(true);
            void updateSetting(GRAPH_COVER_ENGINE_SETTING_KEY, "svg");
          }}
          size="sm"
          variant={engine === "svg" ? "default" : "outline"}
        >
          <PenTool className="size-4" />
          矢量（LLM 直接画）
        </Button>
        <Button
          disabled={engineLocked}
          onClick={() => {
            setEngine("image");
            setEngineTouched(true);
            void updateSetting(GRAPH_COVER_ENGINE_SETTING_KEY, "image");
          }}
          size="sm"
          variant={engine === "image" ? "default" : "outline"}
        >
          <ImageIcon className="size-4" />
          位图（生图模型）
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        {engine === "svg"
          ? "矢量引擎只调用文本模型，不产生图片模型费用；产出必须通过静态 SVG 安全校验。"
          : "位图引擎调用图片模型，观感更细腻，但可能出现糊图或错字，仅作装饰封面。"}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">模型</span>
        {engine === "svg" ? (
          <CoverTextModelSelect disabled={engineLocked} workspaceId={workspaceId} />
        ) : (
          <CoverImageModelSelect disabled={engineLocked} workspaceId={workspaceId} />
        )}
      </div>
      <input
        className="h-9 w-full rounded-lg border bg-background px-2 text-sm"
        disabled={engineLocked}
        maxLength={500}
        onChange={(event) => setHint(event.target.value)}
        placeholder="想让封面表达什么？（留空 = 让模型按图谱内容自动拟）"
        value={hint}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button
          disabled={engineLocked}
          onClick={() => draftMutation.mutate()}
          size="sm"
          variant="outline"
        >
          {draftMutation.isPending
            ? "正在拟草案…"
            : draft
              ? "重新拟草案"
              : "拟草案"}
        </Button>
        {active ? (
          <Button
            disabled={cancelMutation.isPending}
            onClick={() => cancelMutation.mutate()}
            size="sm"
            variant="ghost"
          >
            <Ban className="size-4" />
            取消生成
          </Button>
        ) : null}
      </div>
      {draft ? (
        <div className="space-y-2">
          <textarea
            className="h-28 w-full resize-none rounded-lg border bg-background p-2 text-sm"
            disabled={engineLocked}
            maxLength={2000}
            onChange={(event) => {
              setDraft(event.target.value);
              setDraftSource("user_edited");
            }}
            value={draft}
          />
          <p className="text-xs text-muted-foreground">
            {draftSource === "fallback"
              ? "草案由系统拼接（模型代写暂不可用），可以随意改写。"
              : draftSource === "user_edited"
                ? "已按你的修改生成。"
                : "草案由模型代写，可以随意改写。"}
          </p>
          <Button
            disabled={engineLocked || !draft.trim()}
            onClick={() => startMutation.mutate()}
            size="sm"
          >
            <Sparkles className="size-4" />
            {startMutation.isPending ? "正在提交…" : "确认并生成"}
          </Button>
        </div>
      ) : null}
      {active ? (
        <p className="text-xs" role="status">
          {job.data?.status === "running" ? "正在出图…" : "已提交，等待后台处理…"}
          　可以关闭这个窗口，生成完成后书架会自动更新。
        </p>
      ) : null}
      {failure ? (
        <p className="text-xs text-destructive" role="alert">
          {failure}
          {draft ? "　可以直接点「确认并生成」重试。" : ""}
        </p>
      ) : null}
    </div>
  );
}

/** 书架上的「生成中」角标：兼作关闭弹窗后的后台任务轮询与结果提示。 */
export function GraphCoverAIBadge({ graphId }: { graphId: string }) {
  const { workspaceId = "" } = useParams();
  const queryClient = useQueryClient();
  const job = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "graph-cover-ai", graphId),
    queryFn: () => getAIGraphCoverStatus(graphId),
    refetchInterval: (query) => (query.state.data?.active ? 3000 : false),
  });
  // 每个任务只结算一次：站内重新取数不能让同一条失败反复弹提示。
  const settledJobId = useRef<string | null>(null);
  useEffect(() => {
    const data = job.data;
    if (!data || data.active || !data.id) return;
    if (data.status !== "ready" && data.status !== "failed") return;
    if (settledJobId.current === data.id) return;
    settledJobId.current = data.id;
    if (data.status === "failed") {
      toast.error(data.error ?? "封面生成失败");
    }
    void queryClient.invalidateQueries({
      queryKey: workspaceQueryKey(workspaceId, "graphs"),
    });
  }, [job.data, queryClient, workspaceId]);
  if (!job.data?.active) return null;
  return (
    <span className="rounded bg-secondary px-1.5 py-0.5 text-[10px] text-secondary-foreground">
      生成中
    </span>
  );
}
