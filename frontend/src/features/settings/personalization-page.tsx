import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { AppearanceSettings } from './appearance-settings';
import { useSettingsDirty } from '@/lib/settings-dirty';
import { listSettings, updateSetting } from "@/api";
import { ErrorState, LoadingState, PageFrame, PageIntro, SectionHeading, Surface, } from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from "@/components/ui/select";
import { useAuth } from "@/features/auth/auth-context-value";
import { workspaceQueryKey } from "@/lib/query-keys";
import { BASE_STYLE_OPTIONS, CHARACTERISTIC_FIELDS, CHAT_RESPONSE_STYLE_SETTING_KEY, DEFAULT_RESPONSE_STYLE, getResponseStyleFromSettings, LEVEL_OPTIONS, type BaseStyle, type ResponseStyleConfig, type StyleLevel, } from "@/lib/response-style";
import type { WorkspaceSetting } from "@/types/settings";
function stylesEqual(a: ResponseStyleConfig, b: ResponseStyleConfig): boolean {
    return (a.base_style === b.base_style &&
        a.warmth === b.warmth &&
        a.enthusiasm === b.enthusiasm &&
        a.headings_and_lists === b.headings_and_lists &&
        a.emoji === b.emoji &&
        a.verbosity === b.verbosity);
}
export function PersonalizationPage() {
    const { workspaceId } = useAuth();
    const queryClient = useQueryClient();
    const settings = useQuery({
        queryKey: workspaceQueryKey(workspaceId, "settings"),
        queryFn: listSettings,
    });
    const saved = useMemo(() => getResponseStyleFromSettings(settings.data), [settings.data]);
    const [draft, setDraft] = useState<ResponseStyleConfig>(saved);
    const previousSaved = useRef(saved);
    const previousWorkspace = useRef(workspaceId);
    useEffect(() => {
        const previous = previousSaved.current;
        const switched = previousWorkspace.current !== workspaceId;
        previousSaved.current = saved;
        previousWorkspace.current = workspaceId;
        setDraft(current => switched || stylesEqual(current, previous) ? saved : current);
    }, [saved, workspaceId]);
    const dirty = !stylesEqual(draft, saved);
    useSettingsDirty("response-style", dirty);
    const save = useMutation({
        mutationFn: (value: ResponseStyleConfig) => updateSetting(CHAT_RESPONSE_STYLE_SETTING_KEY, value),
        onError: (error: Error) => toast.error(error.message),
        onSuccess: (setting) => {
            queryClient.setQueryData<WorkspaceSetting[]>(workspaceQueryKey(workspaceId, "settings"), (current) => [
                ...(current ?? []).filter((item) => item.key !== setting.key),
                setting,
            ]);
            toast.success("个性化设置已保存");
        },
    });
    function updateField<K extends keyof ResponseStyleConfig>(key: K, value: ResponseStyleConfig[K]) {
        setDraft((current) => ({ ...current, [key]: value }));
    }
    if (settings.isPending) {
        return (<PageFrame>
        <LoadingState />
      </PageFrame>);
    }
    if (settings.isError) {
        return (<PageFrame>
        <ErrorState message={settings.error.message}/>
      </PageFrame>);
    }
    const baseOption = BASE_STYLE_OPTIONS.find((option) => option.value === draft.base_style);
    return (<PageFrame>
      <PageIntro description="调整外观与回答风格。明确的对话指令优先。" eyebrow="Personalization" title="通用与个性化"/>

      <AppearanceSettings />
      <Surface className="p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <SectionHeading description="基础人格决定默认表达策略，可被当前对话中的明确要求覆盖" title="回答风格"/>
          <div className="flex shrink-0 flex-wrap items-center gap-2"><span role="status" className="text-xs text-muted-foreground">{dirty ? "有未保存的修改" : "已保存"}</span>
            <Button disabled={stylesEqual(draft, DEFAULT_RESPONSE_STYLE) || save.isPending} onClick={() => setDraft({ ...DEFAULT_RESPONSE_STYLE })} size="sm" type="button" variant="outline">
              <RotateCcw className="size-4"/>
              恢复默认
            </Button>
            <Button disabled={!dirty || save.isPending} onClick={() => save.mutate(draft)} size="sm" type="button">
              {save.isPending ? "保存中…" : "保存"}
            </Button>
          </div>
        </div>

        <div className="mt-4">
          <div className="space-y-3">
            <Label htmlFor="base-style">基础风格</Label>
            <Select onValueChange={(value) => updateField("base_style", value as BaseStyle)} value={draft.base_style}>
              <SelectTrigger className="w-full max-w-md" id="base-style">
                <SelectValue placeholder="选择风格"/>
              </SelectTrigger>
              <SelectContent>
                {BASE_STYLE_OPTIONS.map((option) => (<SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>))}
              </SelectContent>
            </Select>
            <p className="text-xs leading-5 text-muted-foreground">
              {baseOption?.description}
            </p>
          </div>
        </div>

        <div className="mt-4 divide-y">
          {CHARACTERISTIC_FIELDS.map((field) => (<div className="settings-row border-b last:border-b-0" key={field.key}>
              <div className="min-w-0">
                <p className="text-sm font-medium">{field.label}</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {field.description}
                </p>
              </div>
              <Select onValueChange={(value) => updateField(field.key, Number(value) as StyleLevel)} value={String(draft[field.key])}>
                <SelectTrigger aria-label={field.label} className="w-full sm:w-44">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LEVEL_OPTIONS.map((option) => (<SelectItem key={option.value} value={String(option.value)}>
                      {option.label}
                    </SelectItem>))}
                </SelectContent>
              </Select>
            </div>))}
        </div>
      </Surface>

      <p className="text-xs leading-5 text-muted-foreground">保存后影响本工作区后续学习对话（含重试）；自动标题、问题建议与结构化 JSON 任务不受影响。当前对话明确的格式与语气要求优先。</p>


    </PageFrame>);
}
