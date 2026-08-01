import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Palette, RotateCcw, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { listSettings, updateSetting } from "@/api";
import { clearSelectionExplanations } from "@/features/chat/selection-explanation";
import {
  ErrorState,
  LoadingState,
  PageFrame,
  PageIntro,
  SectionHeading,
  Surface,
} from "@/components/shared/page-elements";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { WorkspaceSetting } from "@/types/settings";
import {
  BASE_STYLE_OPTIONS,
  CHARACTERISTIC_FIELDS,
  CHAT_RESPONSE_STYLE_SETTING_KEY,
  DEFAULT_RESPONSE_STYLE,
  getResponseStyleFromSettings,
  LEVEL_OPTIONS,
  type BaseStyle,
  type ResponseStyleConfig,
  type StyleLevel,
} from "@/lib/response-style";

function stylesEqual(a: ResponseStyleConfig, b: ResponseStyleConfig): boolean {
  return (
    a.base_style === b.base_style &&
    a.warmth === b.warmth &&
    a.enthusiasm === b.enthusiasm &&
    a.headings_and_lists === b.headings_and_lists &&
    a.emoji === b.emoji &&
    a.verbosity === b.verbosity
  );
}

export function PersonalizationPage() {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: listSettings });
  const saved = useMemo(
    () => getResponseStyleFromSettings(settings.data),
    [settings.data],
  );
  const [draft, setDraft] = useState<ResponseStyleConfig>(saved);

  useEffect(() => {
    setDraft(saved);
  }, [saved]);

  const dirty = !stylesEqual(draft, saved);

  const save = useMutation({
    mutationFn: (value: ResponseStyleConfig) =>
      updateSetting(CHAT_RESPONSE_STYLE_SETTING_KEY, value),
    onError: (error: Error) => toast.error(error.message),
    onSuccess: (setting) => {
      queryClient.setQueryData<WorkspaceSetting[]>(["settings"], (current) => [
        ...(current ?? []).filter((item) => item.key !== setting.key),
        setting,
      ]);
      toast.success("个性化设置已保存");
    },
  });

  function updateField<K extends keyof ResponseStyleConfig>(
    key: K,
    value: ResponseStyleConfig[K],
  ) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  if (settings.isPending) {
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    );
  }
  if (settings.isError) {
    return (
      <PageFrame>
        <ErrorState message={settings.error.message} />
      </PageFrame>
    );
  }

  const baseOption = BASE_STYLE_OPTIONS.find(
    (option) => option.value === draft.base_style,
  );

  return (
    <PageFrame>
      <PageIntro
        description="设置 LearnGraph 回复你的风格和语调。这不会影响模型能力、安全规则或工具可用性；用户当前轮明确指定的格式与语气优先。"
        eyebrow="Personalization"
        title="个性化"
      />

      <Surface className="p-5">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <SectionHeading
            description="基础人格决定默认表达策略，可被当前对话中的明确要求覆盖"
            title="基本风格和语调"
          />
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button
              disabled={stylesEqual(draft, DEFAULT_RESPONSE_STYLE) || save.isPending}
              onClick={() => setDraft({ ...DEFAULT_RESPONSE_STYLE })}
              size="sm"
              type="button"
              variant="outline"
            >
              <RotateCcw className="size-4" />
              恢复默认
            </Button>
            <Button
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate(draft)}
              size="sm"
              type="button"
            >
              {save.isPending ? "保存中…" : "保存"}
            </Button>
          </div>
        </div>

        <div className="mt-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(16rem,20rem)]">
          <div className="space-y-3">
            <Label htmlFor="base-style">基础风格</Label>
            <Select
              onValueChange={(value) =>
                updateField("base_style", value as BaseStyle)
              }
              value={draft.base_style}
            >
              <SelectTrigger className="w-full max-w-md" id="base-style">
                <SelectValue placeholder="选择风格" />
              </SelectTrigger>
              <SelectContent>
                {BASE_STYLE_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs leading-5 text-muted-foreground">
              {baseOption?.description}
            </p>
          </div>
          <div className="rounded-xl border bg-muted/30 p-4">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Sparkles className="size-4 text-primary" />
              当前选择
            </div>
            <p className="mt-2 text-sm font-semibold">
              {baseOption?.label ?? draft.base_style}
            </p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              只改变“如何表达”，不改变“能做什么”。代码、JSON、邮件等成品优先遵循任务体裁。
            </p>
          </div>
        </div>
      </Surface>

      <Surface className="mt-5 p-5">
        <SectionHeading
          description="在基本风格和语调的基础上选择额外的自定义项。调节语气时尽量不改变回答完整度。"
          title="特征"
        />
        <div className="mt-6 space-y-4">
          {CHARACTERISTIC_FIELDS.map((field) => (
            <div
              className="flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-center sm:justify-between"
              key={field.key}
            >
              <div className="min-w-0">
                <p className="text-sm font-medium">{field.label}</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {field.description}
                </p>
              </div>
              <Select
                onValueChange={(value) =>
                  updateField(field.key, Number(value) as StyleLevel)
                }
                value={String(draft[field.key])}
              >
                <SelectTrigger
                  aria-label={field.label}
                  className="w-full sm:w-44"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LEVEL_OPTIONS.map((option) => (
                    <SelectItem
                      key={option.value}
                      value={String(option.value)}
                    >
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ))}
        </div>
      </Surface>

      <Surface className="mt-5 p-5">
        <div className="flex items-start gap-3">
          <Palette className="mt-0.5 size-5 shrink-0 text-primary" />
          <div className="min-w-0 text-sm leading-6 text-muted-foreground">
            <p className="font-medium text-foreground">生效范围</p>
            <p className="mt-1">
              保存后立即作用于本工作区后续的学习对话（含重试）。自动标题、问题建议与结构化
              JSON 生成任务不受人格设置影响。若对话中明确要求某种语气或格式，以当前请求为准。
            </p>
          </div>
        </div>
      </Surface>

      <Surface className="mt-5 p-5">
        <div className="flex items-start gap-3">
          <Trash2 className="mt-0.5 size-5 shrink-0 text-destructive" />
          <div className="min-w-0 text-sm leading-6 text-muted-foreground">
            <p className="font-medium text-foreground">清除本设备学习痕迹</p>
            <p className="mt-1">
              划词解释记录（选中文本及上下文）保存在浏览器本地，按当前登录账户和工作区隔离。
              点击下方按钮将清除本账户在本设备上的所有划词历史记录，此操作不可撤销。
            </p>
            <Button
              className="mt-3"
              onClick={() => {
                clearSelectionExplanations();
                toast.success("已清除本设备上的划词学习痕迹");
              }}
              size="sm"
              type="button"
              variant="destructive"
            >
              <Trash2 className="size-4" />
              清除记录
            </Button>
          </div>
        </div>
      </Surface>
    </PageFrame>
  );
}
