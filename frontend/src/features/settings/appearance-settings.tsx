import { listSettings, updateSetting } from "@/api";
import { ErrorState, LoadingState } from '@/components/shared/page-elements';
import { Switch } from "@/components/ui/switch";
import { currentWorkspaceQueryKey } from "@/lib/query-keys";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { SettingRow, SettingsSection } from './settings-primitives';
function useAppearanceSettings() {
    const queryClient = useQueryClient();
    const settingsQueryKey = currentWorkspaceQueryKey("settings");
    const settings = useQuery({
        queryKey: settingsQueryKey,
        queryFn: listSettings,
        staleTime: 30000,
    });
    const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
    const save = useMutation({
        mutationFn: (value: unknown) => updateSetting("ui.preferences", value),
        onError: (error) => toast.error(error.message),
        onSuccess: () => {
            toast.success("工作区设置已更新");
            void queryClient.invalidateQueries({ queryKey: settingsQueryKey });
        },
    });
    const current = useMemo(() => settings.data?.find((item) => item.key === "ui.preferences")?.value, [settings.data]);
    useEffect(() => {
        if (!current || typeof current !== "object" || !("theme" in current))
            return;
        const isDark = (current as {
            theme?: unknown;
        }).theme === "dark";
        setDark(isDark);
        document.documentElement.classList.toggle("dark", isDark);
        try {
            window.localStorage.setItem("lg-theme", isDark ? "dark" : "light");
        }
        catch {
            // Class still applied even if storage is unavailable.
        }
    }, [current]);
    function toggleTheme(value: boolean) {
        setDark(value);
        document.documentElement.classList.toggle("dark", value);
        try {
            window.localStorage.setItem("lg-theme", value ? "dark" : "light");
        }
        catch {
            // Class still applied even if storage is unavailable.
        }
        save.mutate({
            ...(typeof current === "object" && current && !Array.isArray(current)
                ? current
                : {}),
            theme: value ? "dark" : "light",
        });
    }
    return { settings, dark, save, toggleTheme, current };
}
export function AppearanceSettings() {
    const { settings, dark, save, toggleTheme } = useAppearanceSettings();
    if (settings.isPending)
        return <LoadingState />;
    const readError = settings.error;
    if (readError)
        return <ErrorState message={readError.message}/>;
    return (<SettingsSection title="外观" description=""><SettingRow title="深色主题" description="长时间学习时可切换；会写入工作区外观偏好"><Switch aria-label="深色主题" checked={dark} disabled={save.isPending} onCheckedChange={toggleTheme}/></SettingRow></SettingsSection>);
}
