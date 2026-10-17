import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { toast } from "sonner";
import { listSettings, updateSetting } from "@/api";
import { Surface, LoadingState, ErrorState } from "@/components/shared/page-elements";
import { Switch } from "@/components/ui/switch";
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import { isVoiceDebugPanelEnabled, VOICE_DEBUG_PANEL_SETTING_KEY, } from "@/lib/workspace-settings";
import type { WorkspaceSetting } from "@/types/settings";
import { AdvancedSettings } from './settings-primitives';
export function VoiceDebugSettings() {
    const { workspaceId } = useAuth();
    const queryClient = useQueryClient();
    const settings = useQuery({ queryKey: workspaceQueryKey(workspaceId, "settings"), queryFn: listSettings });
    const voiceDebugEnabled = isVoiceDebugPanelEnabled(settings.data);
    const voiceDebugSave = useMutation({
        onMutate: () => workspaceId,
        mutationFn: (enabled: boolean) => updateSetting(VOICE_DEBUG_PANEL_SETTING_KEY, enabled, { headers: { "X-Workspace-ID": workspaceId } }),
        onError: (error: Error) => toast.error(error.message),
        onSuccess: (setting, _variables, originWorkspaceId) => {
            queryClient.setQueryData<WorkspaceSetting[]>(workspaceQueryKey(originWorkspaceId!, "settings"), (current) => [
                ...(current ?? []).filter((item) => item.key !== setting.key),
                setting,
            ]);
            toast.success("语音调试面板设置已更新");
        },
    });
    if (settings.isPending) return <LoadingState/>;
    if (settings.error) return <ErrorState message={settings.error.message}/>;
    return <AdvancedSettings title="高级诊断" description="在对话侧栏查看语音各环节耗时"><Surface className="mt-5 p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <Gauge className="mt-0.5 size-5 shrink-0 text-primary"/>
            <div className="min-w-0 text-sm leading-6 text-muted-foreground">
              <p className="font-medium text-foreground">语音调试面板</p>
              <p className="mt-1">
                在对话侧栏显示识别、模型生成、合成与播放耗时。
              </p>
            </div>
          </div>
          <Switch checked={voiceDebugEnabled} aria-label="语音调试面板" disabled={voiceDebugSave.isPending || !settings.data} onCheckedChange={(checked) => voiceDebugSave.mutate(checked)}/>
        </div>
      </Surface></AdvancedSettings>;
}
