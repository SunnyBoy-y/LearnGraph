import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { AdvancedSettings } from './settings-primitives';
import { useSettingsDirty } from '@/lib/settings-dirty';
import { Link } from 'react-router-dom';
import { discoverProviderModels, getMemoryEnhancement, listProviderCatalog, listProviders, listSettings, updateMemoryEnhancement, updateSetting } from "@/api";
import { FEATURE_MODEL_DEFAULT, FEATURE_MODEL_FOLLOW_CONVERSATION, SearchableFeatureModelSelect, featureModelValue, parseFeatureModelValue, useFeatureModelChoices, withConfiguredModelChoice, } from "@/components/shared/feature-model-select";
import { ErrorState, LoadingState } from '@/components/shared/page-elements';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, } from "@/components/ui/alert-dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from "@/components/ui/select";
import { useAuth } from "@/features/auth/auth-context-value";
import { LearningModelSelect } from "@/features/learning/learning-model-select";
import { workspaceQueryKey } from "@/lib/query-keys";
import { CHAT_AUTO_TITLE_MODEL_SETTING_KEY, CHAT_DICTATION_CLEANUP_MODEL_SETTING_KEY, CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY, PRACTICE_EXERCISE_MODEL_SETTING_KEY, readChatFeatureModelSetting } from "@/lib/workspace-settings";
import type { MemoryEnhancementUpdateRequest } from "@/types/memory";
import type { WorkspaceSetting } from "@/types/settings";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { SettingRow, SettingsSection } from './settings-primitives';
function useTaskModelSettings() {
    const auth = useAuth();
    const requestOptions = { headers: { 'X-Workspace-ID': auth.workspaceId } };
    const queryClient = useQueryClient();
    const settingsQueryKey = workspaceQueryKey(auth.workspaceId, "settings");
    const settings = useQuery({
        queryKey: settingsQueryKey,
        queryFn: listSettings,
        staleTime: 30000,
    });
    const providers = useQuery({
        queryKey: workspaceQueryKey(auth.workspaceId, "providers"),
        queryFn: listProviders,
        staleTime: 30000,
    });
    const { choices: featureModelChoices } = useFeatureModelChoices();
    const memoryEnhancement = useQuery({
        queryKey: workspaceQueryKey(auth.workspaceId, "memory-enhancement"),
        queryFn: getMemoryEnhancement,
        staleTime: 30000,
    });
    const [embeddingModelDraft, setEmbeddingModelDraft] = useState('');
    useSettingsDirty('embedding-model-draft', Boolean(embeddingModelDraft.trim() && embeddingModelDraft.trim() !== memoryEnhancement.data?.embedding.model_id));
    const [pendingEmbeddingSwitch, setPendingEmbeddingSwitch] = useState<MemoryEnhancementUpdateRequest | null>(null);
    const saveMemoryEnhancement = useMutation({
    onMutate: () => auth.workspaceId,
        mutationFn: (updates: MemoryEnhancementUpdateRequest) => updateMemoryEnhancement(updates, requestOptions),
        onError: (error) => toast.error(error.message),
        onSuccess: (view, updates, workspaceId) => {
            if (updates.embedding?.provider_id !== undefined || (updates.embedding?.model_id && updates.embedding.model_id === embeddingModelDraft.trim())) setEmbeddingModelDraft('');
            queryClient.setQueryData(workspaceQueryKey(workspaceId!, "memory-enhancement"), view);
            toast.success(view.cache_invalidated
                ? "已切换 Embedding 模型：语义召回已降级，需重建索引"
                : "记忆设置已更新");
        },
    });
    const embeddingSwitchInvalidates = (patch: MemoryEnhancementUpdateRequest): boolean => {
        const current = memoryEnhancement.data;
        if (!current || !(current.indexed_memories > 0))
            return false;
        const target = patch.embedding;
        if (!target)
            return false;
        const providerChanged = target.provider_id !== undefined &&
            target.provider_id !== current.embedding.provider_id;
        const modelChanged = target.model_id !== undefined &&
            target.model_id !== current.embedding.model_id;
        return providerChanged || modelChanged;
    };
    const applyEmbeddingPatch = (patch: MemoryEnhancementUpdateRequest) => {
        if (embeddingSwitchInvalidates(patch)) {
            setPendingEmbeddingSwitch(patch);
            return;
        }
        saveMemoryEnhancement.mutate(patch);
    };
    const providerCatalog = useQuery({
        queryKey: ["provider-catalog"],
        queryFn: listProviderCatalog,
        staleTime: 30000,
    });
    const embeddingProviders = useMemo(() => {
        const roleByType = new Map((providerCatalog.data ?? []).map((item) => [item.provider_type, item.role]));
        const rows = (providers.data ?? []).filter((provider) => provider.enabled && roleByType.get(provider.provider_type) === "embedding");
        // A previously configured provider (for example an OpenAI-compatible chat
        // row that also serves /embeddings) stays selectable after this list
        // narrowed to the dedicated embedding role.
        const configuredId = memoryEnhancement.data?.embedding.provider_id;
        if (configuredId && !rows.some((row) => row.id === configuredId)) {
            const configured = (providers.data ?? []).find((row) => row.id === configuredId);
            if (configured)
                rows.push(configured);
        }
        return rows;
    }, [providerCatalog.data, providers.data, memoryEnhancement.data]);
    const selectedEmbeddingProvider = embeddingProviders.find((provider) => provider.id === memoryEnhancement.data?.embedding.provider_id);
    const embeddingDiscovery = useQuery({
        queryKey: workspaceQueryKey(auth.workspaceId, "settings-embedding-models", selectedEmbeddingProvider?.id),
        queryFn: () => discoverProviderModels(selectedEmbeddingProvider!.id),
        staleTime: 30000,
        enabled: Boolean(selectedEmbeddingProvider?.enabled),
    });
    const embeddingModels = (embeddingDiscovery.data?.models ?? []).filter((model) => model.enabled !== false);
    const configuredEmbeddingModelId = memoryEnhancement.data?.embedding.model_id ?? "";
    const visibleEmbeddingModels = configuredEmbeddingModelId &&
        !embeddingModels.some((model) => model.id === configuredEmbeddingModelId)
        ? [
            ...embeddingModels,
            {
                id: configuredEmbeddingModelId,
                roles: ["embedding"],
                streaming: false,
                remote: true,
                enabled: false,
            },
        ]
        : embeddingModels;
    const saveFeatureModel = useMutation({
        onMutate: () => settingsQueryKey,
        mutationFn: ({ key, provider_id, model_id, }: {
            key: string;
            provider_id: string | null;
            model_id: string | null;
        }) => updateSetting(key, { provider_id, model_id }, requestOptions),
        onError: (error) => toast.error(error.message),
        onSuccess: (setting, _variables, originKey) => {
            queryClient.setQueryData<WorkspaceSetting[]>(originKey!, (current) => [
                ...(current ?? []).filter((item) => item.key !== setting.key),
                setting,
            ]);
            if (setting.key === CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY) {
                queryClient.removeQueries({ queryKey: ["suggested-prompts"] });
            }
            toast.success("功能模型设置已更新");
        },
    });
    const autoTitleModel = useMemo(() => readChatFeatureModelSetting(settings.data, CHAT_AUTO_TITLE_MODEL_SETTING_KEY), [settings.data]);
    const suggestedPromptsModel = useMemo(() => readChatFeatureModelSetting(settings.data, CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY), [settings.data]);
    const dictationCleanupModel = useMemo(() => readChatFeatureModelSetting(settings.data, CHAT_DICTATION_CLEANUP_MODEL_SETTING_KEY), [settings.data]);
    const exerciseModel = useMemo(() => readChatFeatureModelSetting(settings.data, PRACTICE_EXERCISE_MODEL_SETTING_KEY), [settings.data]);
    const memoryExtractionCfg = memoryEnhancement.data?.extraction;
    const memoryFollowsConversation = memoryExtractionCfg?.follow_conversation === true;
    const memoryModelValue = memoryFollowsConversation
        ? FEATURE_MODEL_FOLLOW_CONVERSATION
        : featureModelValue(memoryExtractionCfg?.provider_id || null, memoryExtractionCfg?.model_id || null);
    const memoryModelChoices = withConfiguredModelChoice(featureModelChoices, memoryExtractionCfg?.provider_id ?? "", memoryExtractionCfg?.model_id ?? "", (providers.data ?? []).find((item) => item.id === memoryExtractionCfg?.provider_id)?.display_name);
    return { embeddingModelDraft, setEmbeddingModelDraft, settings, memoryEnhancement, providers, saveFeatureModel, featureModelChoices, autoTitleModel, suggestedPromptsModel, dictationCleanupModel, exerciseModel, saveMemoryEnhancement, memoryModelChoices, memoryModelValue, auth, embeddingProviders, applyEmbeddingPatch, selectedEmbeddingProvider, embeddingDiscovery, visibleEmbeddingModels, pendingEmbeddingSwitch, setPendingEmbeddingSwitch };
}
export function TaskModelSettings() {
    const { workspaceId } = useAuth();
    return <TaskModelSettingsForm key={workspaceId}/>;
}
function TaskModelSettingsForm() {
    const { embeddingModelDraft, setEmbeddingModelDraft, settings, memoryEnhancement, providers, saveFeatureModel, featureModelChoices, autoTitleModel, suggestedPromptsModel, dictationCleanupModel, exerciseModel, saveMemoryEnhancement, memoryModelChoices, memoryModelValue, auth, embeddingProviders, applyEmbeddingPatch, selectedEmbeddingProvider, embeddingDiscovery, visibleEmbeddingModels, pendingEmbeddingSwitch, setPendingEmbeddingSwitch } = useTaskModelSettings();
    const optionsFor = (configured: typeof autoTitleModel) => withConfiguredModelChoice(featureModelChoices, configured.provider_id ?? '', configured.model_id ?? '', providers.data?.find(item => item.id === configured.provider_id)?.display_name).map(choice => ({ value: choice.value, label: choice.label + (featureModelChoices.some(item => item.value === choice.value) ? '' : '（当前配置，未发现或已停用）') }));
    if (settings.isPending || memoryEnhancement.isPending || providers.isPending)
        return <LoadingState />;
    const readError = settings.error ?? memoryEnhancement.error ?? providers.error;
    if (readError)
        return <ErrorState message={readError.message}/>;
    return (<SettingsSection title="任务模型分配" description="自动保存到当前工作区；各任务保留原有回退规则。"><SettingRow title="生成标题模型" description="首条用户消息后的自动命名"><SearchableFeatureModelSelect ariaLabel="生成标题模型" disabled={saveFeatureModel.isPending || providers.isPending} onValueChange={(value) => {
            const parsed = parseFeatureModelValue(value);
            saveFeatureModel.mutate({ key: CHAT_AUTO_TITLE_MODEL_SETTING_KEY, ...parsed });
        }} options={[{ value: "default", label: "跟随对话模型" }, ...optionsFor(autoTitleModel)]} placeholder="跟随对话模型" value={featureModelValue(autoTitleModel.provider_id, autoTitleModel.model_id)}/></SettingRow><SettingRow title="生成下一步问题提示模型" description="空会话与每轮完成后的追问建议"><SearchableFeatureModelSelect ariaLabel="生成下一步问题提示模型" disabled={saveFeatureModel.isPending || providers.isPending} onValueChange={(value) => {
            const parsed = parseFeatureModelValue(value);
            saveFeatureModel.mutate({ key: CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY, ...parsed });
        }} options={[{ value: "default", label: "跟随对话模型" }, ...optionsFor(suggestedPromptsModel)]} placeholder="跟随对话模型" value={featureModelValue(suggestedPromptsModel.provider_id, suggestedPromptsModel.model_id)}/></SettingRow><SettingRow title="语音转写整理模型" description="听写时的语气词去除与错字修正；建议选择低价的快速模型"><SearchableFeatureModelSelect ariaLabel="语音转写整理模型" disabled={saveFeatureModel.isPending || providers.isPending} onValueChange={(value) => {
            const parsed = parseFeatureModelValue(value);
            saveFeatureModel.mutate({ key: CHAT_DICTATION_CLEANUP_MODEL_SETTING_KEY, ...parsed });
        }} options={[{ value: "default", label: "跟随对话模型" }, ...optionsFor(dictationCleanupModel)]} placeholder="跟随对话模型" value={featureModelValue(dictationCleanupModel.provider_id, dictationCleanupModel.model_id)}/></SettingRow><SettingRow title="练习出题与判分模型" description="复习与练习中心的出题与简答题判分；建议选择支持思考的强模型，未配置时跟随对话模型"><SearchableFeatureModelSelect ariaLabel="练习出题与判分模型" disabled={saveFeatureModel.isPending || providers.isPending} onValueChange={(value) => {
            const parsed = parseFeatureModelValue(value);
            saveFeatureModel.mutate({ key: PRACTICE_EXERCISE_MODEL_SETTING_KEY, ...parsed });
        }} options={[{ value: "default", label: "跟随对话模型" }, ...optionsFor(exerciseModel)]} placeholder="跟随对话模型" value={featureModelValue(exerciseModel.provider_id, exerciseModel.model_id)}/></SettingRow><SettingRow title="教学包生成模型" description="节点学习页的教材、互动实验、小剧场与闯关测评共用；结构化输出很长，建议选择输出上限足够大的模型"><LearningModelSelect settingsEditor workspaceId={auth.workspaceId}/></SettingRow><SettingRow title="记忆整理模型" description="记忆整理未指定模型时使用工作区默认模型；后台会话摘要需要明确指定模型。"><SearchableFeatureModelSelect ariaLabel="记忆整理模型" disabled={saveMemoryEnhancement.isPending || memoryEnhancement.isPending || providers.isPending} onValueChange={(value) => {
            const follow_conversation = value === FEATURE_MODEL_FOLLOW_CONVERSATION;
            const parsed = parseFeatureModelValue(value);
            const patch = { provider_id: follow_conversation ? "" : (parsed.provider_id ?? ""), model_id: follow_conversation ? "" : (parsed.model_id ?? ""), follow_conversation };
            saveMemoryEnhancement.mutate({ extraction: patch, summarization: patch });
        }} options={[{ value: FEATURE_MODEL_DEFAULT, label: "未配置" }, { value: FEATURE_MODEL_FOLLOW_CONVERSATION, label: "跟随对话模型" }, ...memoryModelChoices.map((choice) => ({ value: choice.value, label: choice.label + (featureModelChoices.some(item => item.value === choice.value) ? '' : '（当前配置，未发现或已停用）') }))]} placeholder="未配置" value={memoryModelValue}/></SettingRow><div className="rounded-xl border p-4">
            <p className="text-sm font-medium">Embedding 模型</p>
            <p className="mt-1 text-xs text-muted-foreground">
              语义检索召回使用；在
              <Link className="underline underline-offset-2" to={`/w/${auth.workspaceId}/settings/models`}>
                Provider 管理
              </Link>
              的「Embedding」分类下创建服务
            </p>
            <Select disabled={saveMemoryEnhancement.isPending ||
            memoryEnhancement.isPending ||
            providers.isPending} onValueChange={(provider_id) => {
            const provider = embeddingProviders.find((item) => item.id === provider_id);
            const defaultModel = provider?.capabilities.default_embedding_model_id;
            applyEmbeddingPatch({
                embedding: {
                    provider_id: provider_id === "none" ? "" : provider_id,
                    model_id: typeof defaultModel === "string" ? defaultModel : "",
                },
            });
        }} value={memoryEnhancement.data?.embedding.provider_id || "none"}>
              <SelectTrigger aria-label="Embedding Provider" className="mt-3">
                <SelectValue placeholder="选择 Embedding Provider"/>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">未配置</SelectItem>
                {memoryEnhancement.data?.embedding.provider_id && !selectedEmbeddingProvider ? <SelectItem value={memoryEnhancement.data.embedding.provider_id} disabled>当前连接已删除或不可访问</SelectItem> : null}
                {embeddingProviders.map((item) => (<SelectItem key={item.id} value={item.id} disabled={!item.enabled}>
                    {item.display_name}{!item.enabled ? "（已停用）" : ""}
                  </SelectItem>))}
              </SelectContent>
            </Select>
            <SearchableFeatureModelSelect ariaLabel="Embedding 模型" disabled={saveMemoryEnhancement.isPending || memoryEnhancement.isPending || !selectedEmbeddingProvider?.enabled || embeddingDiscovery.isPending || visibleEmbeddingModels.length === 0} onValueChange={(model_id) => applyEmbeddingPatch({ embedding: { model_id } })} options={visibleEmbeddingModels.map((model) => ({ value: model.id, label: `${model.id}${model.enabled === false ? "（当前配置，未发现或已停用）" : ""}` }))} placeholder={!selectedEmbeddingProvider ? "请先选择 Embedding Provider" : embeddingDiscovery.isPending ? "正在发现 Embedding 模型…" : visibleEmbeddingModels.length === 0 ? "该 Provider 尚无已启用的已发现模型" : "选择 Embedding 模型"} value={memoryEnhancement.data?.embedding.model_id || ""}/>


            {embeddingDiscovery.error ? <p role="alert" className="mt-3 text-xs text-destructive">无法发现检索模型：{embeddingDiscovery.error.message}。可重试，或在高级区域手动输入模型 ID。<Button size="sm" variant="ghost" onClick={() => void embeddingDiscovery.refetch()} disabled={embeddingDiscovery.isFetching}>重新发现模型</Button></p> : null}
            <AdvancedSettings title="手动配置检索模型" description="用于服务尚未发现的模型 ID；保存时保留原有切换确认。"><SettingRow title="Embedding 模型 ID"><div className="flex flex-wrap gap-2"><Input aria-label="手动 Embedding 模型 ID" value={embeddingModelDraft} placeholder={memoryEnhancement.data?.embedding.model_id || '输入模型 ID'} onChange={event => setEmbeddingModelDraft(event.target.value)}/><Button size="sm" variant="outline" disabled={!embeddingModelDraft.trim() || !selectedEmbeddingProvider?.enabled || saveMemoryEnhancement.isPending} onClick={() => applyEmbeddingPatch({ embedding: { model_id: embeddingModelDraft.trim() } })}>保存模型</Button></div></SettingRow></AdvancedSettings>
            <Link className="mt-3 inline-block text-xs underline underline-offset-4" to={`/w/${auth.workspaceId}/settings/memory`}>管理检索索引</Link>
            <AlertDialog open={pendingEmbeddingSwitch !== null} onOpenChange={(open) => {
            if (!open)
                setPendingEmbeddingSwitch(null);
        }}>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>
                    切换 Embedding 模型会使现有向量缓存失效
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    新模型下的记忆需要重新生成索引（切换后已索引数量会归零，
                    语义召回暂时降级，可一键重建）；旧模型的缓存可一并清理。
                    记忆内容本身不会丢失。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>取消</AlertDialogCancel>
                  <AlertDialogAction onClick={() => {
            const patch = pendingEmbeddingSwitch;
            setPendingEmbeddingSwitch(null);
            if (patch)
                saveMemoryEnhancement.mutate(patch);
        }}>
                    确认切换
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div></SettingsSection>);
}
