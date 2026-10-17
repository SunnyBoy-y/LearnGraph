import { useSettingsDirty } from '@/lib/settings-dirty';
import { listSettings, updateSetting } from "@/api";
import { ErrorState, LoadingState } from '@/components/shared/page-elements';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { currentWorkspaceQueryKey } from "@/lib/query-keys";
import type { AsrHotword } from "@/lib/workspace-settings";
import { CHAT_ASR_HOTWORDS_SETTING_KEY, CHAT_ASR_LANGUAGE_SETTING_KEY, CHAT_ASR_VAD_ADAPTIVE_SETTING_KEY, CHAT_ASR_VAD_MODE_SETTING_KEY, CHAT_DICTATION_CLEANUP_SETTING_KEY, isAsrVadAdaptive, isChatDictationCleanupEnabled, readAsrHotwords, readAsrLanguage, readAsrVadMode } from "@/lib/workspace-settings";
import type { WorkspaceSetting } from "@/types/settings";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mic } from "lucide-react";
import { useAuth } from "@/features/auth/auth-context-value";
import { useState } from "react";
import { toast } from "sonner";
import { AdvancedSettings, SettingRow, SettingsSection } from './settings-primitives';
function useVoiceInputSettings() {
    const queryClient = useQueryClient();
    const settingsQueryKey = currentWorkspaceQueryKey("settings");
    const requestOptions = { headers: { "X-Workspace-ID": String(settingsQueryKey[1]) } };
    const settings = useQuery({
        queryKey: settingsQueryKey,
        queryFn: listSettings,
        staleTime: 30000,
    });
    const saveDictationCleanup = useMutation({
        onMutate: () => settingsQueryKey,
        mutationFn: (enabled: boolean) => updateSetting(CHAT_DICTATION_CLEANUP_SETTING_KEY, { enabled }, requestOptions),
        onError: (error) => toast.error(error.message),
        onSuccess: (setting, _variables, originKey) => {
            queryClient.setQueryData<WorkspaceSetting[]>(originKey!, (current) => [
                ...(current ?? []).filter((item) => item.key !== setting.key),
                setting,
            ]);
            toast.success("语音转写整理设置已更新");
        },
    });
    const saveAsrLanguage = useMutation({
        onMutate: () => settingsQueryKey,
        mutationFn: (language: string) => updateSetting(CHAT_ASR_LANGUAGE_SETTING_KEY, language, requestOptions),
        onError: (error) => toast.error(error.message),
        onSuccess: (setting, _variables, originKey) => {
            queryClient.setQueryData<WorkspaceSetting[]>(originKey!, (current) => [
                ...(current ?? []).filter((item) => item.key !== setting.key),
                setting,
            ]);
            toast.success("语音识别语言已更新");
        },
    });
    const saveAsrVadMode = useMutation({
        onMutate: () => settingsQueryKey,
        mutationFn: (mode: string) => updateSetting(CHAT_ASR_VAD_MODE_SETTING_KEY, mode, requestOptions),
        onError: (error) => toast.error(error.message),
        onSuccess: (setting, _variables, originKey) => {
            queryClient.setQueryData<WorkspaceSetting[]>(originKey!, (current) => [
                ...(current ?? []).filter((item) => item.key !== setting.key),
                setting,
            ]);
            toast.success("VAD 分工模式已更新");
        },
    });
    const saveAsrVadAdaptive = useMutation({
        onMutate: () => settingsQueryKey,
        mutationFn: (enabled: boolean) => updateSetting(CHAT_ASR_VAD_ADAPTIVE_SETTING_KEY, enabled, requestOptions),
        onError: (error) => toast.error(error.message),
        onSuccess: (setting, _variables, originKey) => {
            queryClient.setQueryData<WorkspaceSetting[]>(originKey!, (current) => [
                ...(current ?? []).filter((item) => item.key !== setting.key),
                setting,
            ]);
            toast.success("自适应底噪基线设置已更新");
        },
    });
    const saveAsrHotwords = useMutation({
        onMutate: () => settingsQueryKey,
        mutationFn: (hotwords: AsrHotword[]) => updateSetting(CHAT_ASR_HOTWORDS_SETTING_KEY, hotwords, requestOptions),
        onError: (error) => toast.error(error.message),
        onSuccess: (setting, _variables, originKey) => {
            queryClient.setQueryData<WorkspaceSetting[]>(originKey!, (current) => [
                ...(current ?? []).filter((item) => item.key !== setting.key),
                setting,
            ]);
            toast.success("ASR 热词表已更新");
        },
    });
    const dictationCleanupEnabled = isChatDictationCleanupEnabled(settings.data);
    const asrLanguage = readAsrLanguage(settings.data);
    const asrVadMode = readAsrVadMode(settings.data);
    const asrVadAdaptive = isAsrVadAdaptive(settings.data);
    const asrHotwords = readAsrHotwords(settings.data);
    const [hotwordDraft, setHotwordDraft] = useState("");
    useSettingsDirty("asr-hotwords", Boolean(hotwordDraft.trim()));
    return { settings, dictationCleanupEnabled, saveDictationCleanup, asrLanguage, saveAsrLanguage, asrVadMode, saveAsrVadMode, asrVadAdaptive, saveAsrVadAdaptive, asrHotwords, saveAsrHotwords, setHotwordDraft, hotwordDraft };
}
export function VoiceInputSettings() {
    const { workspaceId } = useAuth();
    return <VoiceInputSettingsForm key={workspaceId}/>;
}
function VoiceInputSettingsForm() {
    const { settings, dictationCleanupEnabled, saveDictationCleanup, asrLanguage, saveAsrLanguage, asrVadMode, saveAsrVadMode, asrVadAdaptive, saveAsrVadAdaptive, asrHotwords, saveAsrHotwords, setHotwordDraft, hotwordDraft } = useVoiceInputSettings();
    if (settings.isPending)
        return <LoadingState />;
    const readError = settings.error;
    if (readError)
        return <ErrorState message={readError.message}/>;
    return (<SettingsSection title="语音输入与端点检测" description=""><SettingRow title="语音转写智能整理" description="转录时用模型去除语气词并修正错字；每段语音会产生少量模型调用"><Switch aria-label="语音转写智能整理" checked={dictationCleanupEnabled} disabled={saveDictationCleanup.isPending} onCheckedChange={(enabled) => saveDictationCleanup.mutate(enabled)}/></SettingRow><div className="rounded-xl border p-4">
            <div className="flex items-center gap-3">
              <Mic className="size-5 shrink-0 text-primary"/>
              <div className="min-w-0">
                <p className="text-sm font-medium">语音输入（ASR）</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  麦克风听写的语言选择、热词表与本机/云端 VAD 分工
                </p>
              </div>
            </div>
            <div className="mt-3 grid gap-3 pl-8">
              <SettingRow title="识别语言" description="显式选择语言提升单语识别率；自动模式支持中英混说"><Select disabled={saveAsrLanguage.isPending} value={asrLanguage} onValueChange={(value) => saveAsrLanguage.mutate(value)}>
                  <SelectTrigger className="w-36" aria-label="ASR 识别语言">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">自动（混说）</SelectItem>
                    <SelectItem value="zh-CN">中文</SelectItem>
                    <SelectItem value="en-US">英文</SelectItem>
                  </SelectContent>
                </Select></SettingRow>
              <SettingRow title="本机/云端 VAD 分工" description="本机优先让实时模型也走分段上传省计费；云端仅调试用"><Select disabled={saveAsrVadMode.isPending} value={asrVadMode} onValueChange={(value) => saveAsrVadMode.mutate(value)}>
                  <SelectTrigger className="w-36" aria-label="VAD 分工模式">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">自动</SelectItem>
                    <SelectItem value="local_only">本机优先</SelectItem>
                    <SelectItem value="cloud_only">云端</SelectItem>
                  </SelectContent>
                </Select></SettingRow>
              <AdvancedSettings title="高级语音处理"><SettingRow title="自适应底噪基线" description="学习环境底噪动态调整切段阈值，减少噪声被切成段"><Switch aria-label="自适应底噪基线" checked={asrVadAdaptive} disabled={saveAsrVadAdaptive.isPending} onCheckedChange={(enabled) => saveAsrVadAdaptive.mutate(enabled)}/></SettingRow></AdvancedSettings>
              <div className="grid gap-1">
                <p className="text-sm">热词表</p>
                <p className="text-xs text-muted-foreground">
                  术语/人名/代码标识符；paraformer-realtime-v2 原生支持，其他模型可能忽略
                </p>
                <div className="flex flex-wrap items-center gap-1.5">
                  {asrHotwords.map((hotword, index) => (<span className="inline-flex items-center gap-1 rounded-full border bg-muted px-2 py-0.5 text-xs" key={`${hotword.text}-${index}`}>
                      {hotword.text}
                      <button disabled={saveAsrHotwords.isPending} aria-label={`删除热词 ${hotword.text}`} className="text-muted-foreground hover:text-destructive" onClick={() => saveAsrHotwords.mutate(asrHotwords.filter((item) => item.text !== hotword.text))} type="button">
                        ×
                      </button>
                    </span>))}
                  <input disabled={saveAsrHotwords.isPending} aria-label="新增 ASR 热词" className="h-7 w-44 rounded-md border bg-transparent px-2 text-xs outline-none focus:border-primary" onChange={(event) => setHotwordDraft(event.target.value)} onKeyDown={(event) => {
            if (event.nativeEvent.isComposing || saveAsrHotwords.isPending) return;
            if (event.key !== "Enter")
                return;
            const text = hotwordDraft.trim();
            if (!text)
                return;
            if (asrHotwords.some((item) => item.text === text)) {
                setHotwordDraft("");
                return;
            }
            saveAsrHotwords.mutate([...asrHotwords, { text }], { onSuccess: () => setHotwordDraft("") });
        }} placeholder="输入热词后回车" value={hotwordDraft}/>
                </div>
              </div>
            </div>
          </div></SettingsSection>);
}
