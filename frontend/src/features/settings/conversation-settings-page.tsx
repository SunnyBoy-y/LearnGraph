import { useAuth } from '@/features/auth/auth-context-value';
import { getMemoryEnhancement, listSettings, updateMemoryEnhancement, updateSetting } from "@/api";
import { ErrorState, LoadingState, PageFrame, PageIntro } from '@/components/shared/page-elements';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useShowCacheHit, useShowStreamStats } from "@/features/chat/stream-stats";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { ResponseMode } from "@/lib/session-composer-prefs";
import { CHAT_CONTEXT_USAGE_SETTING_KEY, CHAT_DEFAULT_RESPONSE_MODE_SETTING_KEY, CHAT_SUGGESTED_PROMPTS_SETTING_KEY, CHAT_THINKING_CHAIN_DEFAULT_SETTING_KEY, areChatSuggestedPromptsEnabled, isChatContextUsageEnabled, readChatDefaultResponseMode, readChatThinkingChainDefault } from "@/lib/workspace-settings";
import type { MemoryEnhancementUpdateRequest } from "@/types/memory";
import type { WorkspaceSetting } from "@/types/settings";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AdvancedSettings, SettingRow, SettingsSection } from './settings-primitives';
import { StickerSettingsPanel } from './sticker-settings';
function useConversationSettingsPage() {
 const auth = useAuth();
    const queryClient = useQueryClient();
    const settingsQueryKey = workspaceQueryKey(auth.workspaceId, "settings");
    const settings = useQuery({
        queryKey: settingsQueryKey,
        queryFn: listSettings,
        staleTime: 30000,
    });
    const [showStreamStats, setShowStreamStats] = useShowStreamStats();
    const [showCacheHit, setShowCacheHit] = useShowCacheHit();
    const saveSuggestedPrompts = useMutation({
        mutationFn: (enabled: boolean) => updateSetting(CHAT_SUGGESTED_PROMPTS_SETTING_KEY, { enabled }),
        onError: (error) => toast.error(error.message),
        onSuccess: (setting) => {
            queryClient.setQueryData<WorkspaceSetting[]>(settingsQueryKey, (current) => [
                ...(current ?? []).filter((item) => item.key !== setting.key),
                setting,
            ]);
            queryClient.removeQueries({ queryKey: ["suggested-prompts"] });
            toast.success("对话问题提示设置已更新");
        },
    });
    const saveContextUsage = useMutation({
        mutationFn: (enabled: boolean) => updateSetting(CHAT_CONTEXT_USAGE_SETTING_KEY, { enabled }),
        onError: (error) => toast.error(error.message),
        onSuccess: (setting) => {
            queryClient.setQueryData<WorkspaceSetting[]>(settingsQueryKey, (current) => [
                ...(current ?? []).filter((item) => item.key !== setting.key),
                setting,
            ]);
            toast.success("上下文用量显示设置已更新");
        },
    });
    const saveDefaultResponseMode = useMutation({
        mutationFn: (response_mode: ResponseMode) => updateSetting(CHAT_DEFAULT_RESPONSE_MODE_SETTING_KEY, { response_mode }),
        onError: (error) => toast.error(error.message),
        onSuccess: (setting) => {
            queryClient.setQueryData<WorkspaceSetting[]>(settingsQueryKey, (current) => [
                ...(current ?? []).filter((item) => item.key !== setting.key),
                setting,
            ]);
            toast.success("默认响应模式已更新");
        },
    });
    const saveThinkingChainDefault = useMutation({
        mutationFn: (default_state: "open" | "collapsed") => updateSetting(CHAT_THINKING_CHAIN_DEFAULT_SETTING_KEY, { default_state }),
        onError: (error) => toast.error(error.message),
        onSuccess: (setting) => {
            queryClient.setQueryData<WorkspaceSetting[]>(settingsQueryKey, (current) => [
                ...(current ?? []).filter((item) => item.key !== setting.key),
                setting,
            ]);
            toast.success("思维链默认状态已更新");
        },
    });
    const memoryEnhancement = useQuery({
        queryKey: workspaceQueryKey(auth.workspaceId, "memory-enhancement"),
        queryFn: getMemoryEnhancement,
        staleTime: 30000,
    });
    const saveMemoryEnhancement = useMutation({
        mutationFn: (updates: MemoryEnhancementUpdateRequest) => updateMemoryEnhancement(updates),
        onError: (error) => toast.error(error.message),
        onSuccess: (view) => {
            queryClient.setQueryData(workspaceQueryKey(auth.workspaceId, "memory-enhancement"), view);
            toast.success(view.cache_invalidated
                ? "已切换 Embedding 模型：语义召回已降级，需重建索引"
                : "记忆设置已更新");
        },
    });
    const suggestedPromptsEnabled = areChatSuggestedPromptsEnabled(settings.data);
    const contextUsageEnabled = isChatContextUsageEnabled(settings.data);
    const defaultResponseMode = readChatDefaultResponseMode(settings.data);
    const thinkingChainDefaultOpen = readChatThinkingChainDefault(settings.data);
    return { settings, memoryEnhancement, saveDefaultResponseMode, defaultResponseMode, suggestedPromptsEnabled, saveSuggestedPrompts, saveThinkingChainDefault, thinkingChainDefaultOpen, saveMemoryEnhancement, contextUsageEnabled, saveContextUsage, showStreamStats, setShowStreamStats, showCacheHit, setShowCacheHit };
}
export function ConversationSettingsPage() {
    const { settings, memoryEnhancement, saveDefaultResponseMode, defaultResponseMode, suggestedPromptsEnabled, saveSuggestedPrompts, saveThinkingChainDefault, thinkingChainDefaultOpen, saveMemoryEnhancement, contextUsageEnabled, saveContextUsage, showStreamStats, setShowStreamStats, showCacheHit, setShowCacheHit } = useConversationSettingsPage();
    if (settings.isPending || memoryEnhancement.isPending)
        return <LoadingState />;
    const readError = settings.error ?? memoryEnhancement.error;
    if (readError)
        return <ErrorState message={readError.message}/>;
    return (<PageFrame><PageIntro title="对话与学习" description="设置新对话的默认行为、上下文管理与性能显示。"/><StickerSettingsPanel /><SettingsSection title="默认行为" description=""><SettingRow title="默认响应模式" description="新建对话时的默认状态：极速 / 智能体；已有会话仍记住各自上次选择"><Select disabled={saveDefaultResponseMode.isPending} onValueChange={(value) => saveDefaultResponseMode.mutate(value as ResponseMode)} value={defaultResponseMode}>
              <SelectTrigger aria-label="默认响应模式" className="w-[8.5rem] shrink-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="fast">极速</SelectItem>
                <SelectItem value="agentic">智能体</SelectItem>
              </SelectContent>
            </Select></SettingRow><SettingRow title="生成下一步问题提示" description="关闭后不会请求提示生成"><Switch aria-label="生成下一步问题提示" checked={suggestedPromptsEnabled} disabled={saveSuggestedPrompts.isPending} onCheckedChange={(enabled) => saveSuggestedPrompts.mutate(enabled)}/></SettingRow><SettingRow title="思考过程默认显示状态" description="生成时显示思考过程；最终回答开始时自动收起。"><Select disabled={saveThinkingChainDefault.isPending} onValueChange={(value) => saveThinkingChainDefault.mutate(value as "open" | "collapsed")} value={thinkingChainDefaultOpen ? "open" : "collapsed"}>
              <SelectTrigger aria-label="思维链处理中默认状态" className="w-[8.5rem] shrink-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="open">展开</SelectItem>
                <SelectItem value="collapsed">折叠</SelectItem>
              </SelectContent>
            </Select></SettingRow></SettingsSection><SettingsSection title="上下文管理" description=""><SettingRow title="自动压缩长对话（会话摘要）" description="自动压缩长对话，与长期记忆整理共用模型。"><Switch aria-label="会话摘要" checked={memoryEnhancement.data?.summarization.enabled ?? false} disabled={memoryEnhancement.isPending || saveMemoryEnhancement.isPending} onCheckedChange={(enabled) => saveMemoryEnhancement.mutate({ summarization: { enabled } })}/></SettingRow><SettingRow title="上下文用量显示" description="在输入框显示上下文占用与自动压缩的剩余量。"><Switch aria-label="上下文用量显示" checked={contextUsageEnabled} disabled={saveContextUsage.isPending} onCheckedChange={(enabled) => saveContextUsage.mutate(enabled)}/></SettingRow></SettingsSection><AdvancedSettings title="高级诊断" description="响应性能指标 · 本设备即时生效"><SettingsSection title="响应性能指标" description=""><SettingRow title="显示流式指标" description="在回答中显示生成速度与 Token 用量（本设备）。"><Switch aria-label="显示流式指标" checked={showStreamStats} onCheckedChange={setShowStreamStats}/></SettingRow><SettingRow title="显示缓存命中" description="显示缓存读取比例（本设备）。"><Switch aria-label="显示缓存命中" checked={showCacheHit} onCheckedChange={setShowCacheHit}/></SettingRow></SettingsSection></AdvancedSettings></PageFrame>);
}
