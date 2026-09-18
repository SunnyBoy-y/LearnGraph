import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { listSettings, updateSetting } from "@/api/settings";
import {
  FEATURE_MODEL_DEFAULT,
  SearchableFeatureModelSelect,
  featureModelValue,
  parseFeatureModelValue,
  useFeatureModelChoices,
  withConfiguredModelChoice,
} from "@/components/shared/feature-model-select";
import { currentWorkspaceQueryKey, workspaceQueryKey } from "@/lib/query-keys";
import {
  LEARNING_PACKAGE_MODEL_SETTING_KEY,
  readChatFeatureModelSetting,
} from "@/lib/workspace-settings";
import type { WorkspaceSetting } from "@/types/settings";

/**
 * 「教学包生成模型」选择器（节点学习页的教材 / 互动实验 / 小剧场 / 闯关测评）。
 *
 * 写入的就是工作区设置「功能模型 → 教学包生成模型」那一份 WorkspaceSetting
 * （`learning.package_model`，{provider_id, model_id} 成对或同时为 null）。
 * 后端 `generate_raw()` 通过 `feature_model_target()` 读同一份设置，未配置时回落
 * 对话模型。自包含的取数与写回让「设置页」和「内容准备弹窗」两处共用同一实现，
 * 不会出现两处各写一份、字段形状还不一致的情况。
 */
export function LearningModelSelect({
  workspaceId,
  disabled,
  ariaLabel = "教学包生成模型",
}: {
  workspaceId?: string;
  disabled?: boolean;
  ariaLabel?: string;
}) {
  const queryClient = useQueryClient();
  // 必须与设置页用同一个 queryKey，否则两处会各存一份缓存、互相看不到改动。
  const settingsQueryKey = workspaceId
    ? workspaceQueryKey(workspaceId, "settings")
    : currentWorkspaceQueryKey("settings");
  const settings = useQuery({
    queryKey: settingsQueryKey,
    queryFn: listSettings,
    staleTime: 30_000,
  });
  const { choices, providers } = useFeatureModelChoices();
  const current = readChatFeatureModelSetting(
    settings.data,
    LEARNING_PACKAGE_MODEL_SETTING_KEY,
  );
  const value = featureModelValue(current.provider_id, current.model_id);
  const options = useMemo(
    () => [
      { value: FEATURE_MODEL_DEFAULT, label: "跟随对话模型" },
      // 已保存但 discovery 不再列出的模型必须继续可见，否则打开选择器会显示成
      // 「跟随对话模型」，与后端真正解析到的模型不符。
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
      updateSetting(LEARNING_PACKAGE_MODEL_SETTING_KEY, next),
    onSuccess: (setting) => {
      queryClient.setQueryData<WorkspaceSetting[]>(settingsQueryKey, (existing) => [
        ...(existing ?? []).filter((item) => item.key !== setting.key),
        setting,
      ]);
      toast.success("教学包生成模型已更新");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <SearchableFeatureModelSelect
      ariaLabel={ariaLabel}
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
      value={value}
    />
  );
}
