import { useMemo } from 'react'

import {
  FEATURE_MODEL_DEFAULT,
  FEATURE_MODEL_FOLLOW_CONVERSATION,
  SearchableFeatureModelSelect,
  featureModelValue,
  parseFeatureModelValue,
  useFeatureModelChoices,
  withConfiguredModelChoice,
} from '@/components/shared/feature-model-select'
import type {
  MemoryEnhancementUpdateRequest,
  MemoryExtractionSettings,
} from '@/types/memory'

/**
 * 「记忆整理 / 学习报告模型」选择器。
 *
 * 写入的就是工作区设置「功能模型 → 记忆整理模型」那一份 WorkspaceSetting
 * （`memory.enhancement` 的 extraction + summarization 两个 section），因为
 * `MemoryProfileService._model_selection()` 优先读 summarization、其次读
 * extraction —— 学习记忆报告的整篇重写用的正是这一对 provider/model。
 *
 * 两个 section 必须一起写：只改一个会让「抽取用的模型」和「摘要用的模型」
 * 分叉，刷新报告时按 summarization 优先解析，用户改的却不是它。
 */
export function MemoryModelSelect({
  config,
  disabled,
  onChange,
}: {
  config: MemoryExtractionSettings | undefined
  disabled?: boolean
  onChange: (patch: MemoryEnhancementUpdateRequest) => void
}) {
  const { choices, providers } = useFeatureModelChoices()
  const providerId = config?.provider_id ?? ''
  const modelId = config?.model_id ?? ''
  const value = config?.follow_conversation
    ? FEATURE_MODEL_FOLLOW_CONVERSATION
    : featureModelValue(providerId || null, modelId || null)
  const options = useMemo(
    () => [
      { value: FEATURE_MODEL_DEFAULT, label: '未配置' },
      { value: FEATURE_MODEL_FOLLOW_CONVERSATION, label: '跟随对话模型' },
      // 已保存但 discovery 不再列出的模型必须继续可见，否则打开弹窗会显示
      // 「未配置」，与后端实际解析到的模型不符。
      ...withConfiguredModelChoice(
        choices,
        providerId,
        modelId,
        providers.find((item) => item.id === providerId)?.display_name,
      ).map((choice) => ({ value: choice.value, label: choice.label })),
    ],
    [choices, modelId, providerId, providers],
  )

  return (
    <SearchableFeatureModelSelect
      ariaLabel="记忆整理模型"
      disabled={disabled}
      onValueChange={(next) => {
        const followConversation = next === FEATURE_MODEL_FOLLOW_CONVERSATION
        const parsed = parseFeatureModelValue(next)
        const patch = {
          provider_id: followConversation ? '' : (parsed.provider_id ?? ''),
          model_id: followConversation ? '' : (parsed.model_id ?? ''),
          follow_conversation: followConversation,
        }
        onChange({ extraction: patch, summarization: patch })
      }}
      options={options}
      placeholder="未配置"
      value={value}
    />
  )
}
