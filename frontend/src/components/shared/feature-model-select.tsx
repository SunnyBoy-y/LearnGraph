import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'

import { discoverProviderModels, listProviders } from '@/api'
import { fuzzyModelMatch, providerCapabilityString } from '@/lib/model-choices'
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import type { Provider, ProviderModel } from '@/types/providers'

/**
 * Shared "feature model" picker: one provider+model choice control for every
 * workspace setting that routes a background feature to its own model
 * (自动标题、追问提示、语音清理、练习出题、记忆整理 / 学习报告).
 *
 * The helpers used to live privately inside the workspace settings page; the
 * memory settings dialog now needs the same picker, so they are shared here to
 * keep both surfaces byte-identical (same option building, same sentinels).
 */

/** Provider types that can serve text-generation feature models. */
export const MODEL_PROVIDER_TYPES = new Set([
  'openai_responses',
  'openai_compatible_chat',
  'qwen',
  'codex_chatgpt',
  'deepseek_chat',
  'anthropic_messages',
  'ollama',
])

/** Sentinel value for "no explicit model" in a feature-model select. */
export const FEATURE_MODEL_DEFAULT = 'default'
/** Sentinel value for "follow the conversation model" where that is supported. */
export const FEATURE_MODEL_FOLLOW_CONVERSATION = 'follow-conversation'

export type FeatureModelChoice = {
  value: string
  label: string
  providerId: string
  modelId: string
}

export function featureModelOptions(
  provider: Provider | undefined,
  discovered: ProviderModel[] | undefined,
) {
  if (!provider) return [] as ProviderModel[]
  const byId = new Map((discovered ?? []).map((model) => [model.id, model]))
  const configured = providerCapabilityString(provider, 'default_model')
  if (configured && !byId.has(configured)) {
    byId.set(configured, {
      id: configured,
      roles: ['llm'],
      streaming: true,
      remote: true,
    })
  }
  return [...byId.values()]
}

export function featureModelValue(providerId: string | null, modelId: string | null) {
  if (!providerId || !modelId) return FEATURE_MODEL_DEFAULT
  return `${providerId}::${modelId}`
}

export function parseFeatureModelValue(value: string): {
  provider_id: string | null
  model_id: string | null
} {
  if (!value || value === FEATURE_MODEL_DEFAULT) {
    return { provider_id: null, model_id: null }
  }
  const [providerId, modelId] = value.split('::')
  if (!providerId || !modelId) {
    return { provider_id: null, model_id: null }
  }
  return { provider_id: providerId, model_id: modelId }
}

/**
 * A configured model that discovery no longer lists must stay visible in the
 * select instead of silently falling back to "未配置" — otherwise opening the
 * dialog would misrepresent what is actually stored.
 */
export function withConfiguredModelChoice(
  choices: FeatureModelChoice[],
  providerId: string,
  modelId: string,
  providerLabel?: string,
): FeatureModelChoice[] {
  if (!providerId || !modelId) return choices
  const value = featureModelValue(providerId, modelId)
  if (value === FEATURE_MODEL_DEFAULT) return choices
  if (choices.some((choice) => choice.value === value)) return choices
  return [
    ...choices,
    { value, label: `${providerLabel || providerId} · ${modelId}`, providerId, modelId },
  ]
}

/**
 * Every selectable `provider::model` pair for the current workspace, discovered
 * from enabled Providers. Shares the `['providers']` cache entry with the
 * workspace settings page, so opening a picker does not re-fetch the catalog.
 */
export function useFeatureModelChoices(): {
  choices: FeatureModelChoice[]
  providers: Provider[]
} {
  const providersQuery = useQuery({
    queryKey: ['providers'],
    queryFn: listProviders,
    staleTime: 30_000,
  })
  const modelProviders = useMemo(
    () =>
      (providersQuery.data ?? []).filter(
        (provider) =>
          provider.enabled &&
          provider.remote_capability &&
          MODEL_PROVIDER_TYPES.has(provider.provider_type),
      ),
    [providersQuery.data],
  )
  const discoveredByProvider = useQuery({
    queryKey: [
      'feature-model-discovery',
      modelProviders.map((item) => item.id).join(','),
    ],
    queryFn: async () => {
      const entries = await Promise.all(
        modelProviders.map(async (provider) => {
          try {
            const models = await discoverProviderModels(provider.id)
            return [provider.id, models.models] as const
          } catch {
            return [provider.id, [] as ProviderModel[]] as const
          }
        }),
      )
      return Object.fromEntries(entries) as Record<string, ProviderModel[]>
    },
    enabled: modelProviders.length > 0,
  })
  const choices = useMemo(() => {
    const result: FeatureModelChoice[] = []
    for (const provider of modelProviders) {
      const models = featureModelOptions(
        provider,
        discoveredByProvider.data?.[provider.id],
      )
      for (const model of models) {
        result.push({
          value: featureModelValue(provider.id, model.id),
          label: `${provider.display_name} · ${model.id}`,
          providerId: provider.id,
          modelId: model.id,
        })
      }
    }
    return result
  }, [discoveredByProvider.data, modelProviders])

  return { choices, providers: providersQuery.data ?? [] }
}

/** Searchable provider+model picker used by every feature-model setting. */
export function SearchableFeatureModelSelect({
  ariaLabel,
  disabled,
  onValueChange,
  options,
  placeholder,
  value,
}: {
  ariaLabel: string
  disabled?: boolean
  onValueChange: (value: string) => void
  options: Array<{ value: string; label: string }>
  placeholder: string
  value: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const selected = options.find((option) => option.value === value)
  const visible = options.filter((option) => fuzzyModelMatch(`${option.label} ${option.value}`, query))
  return (
    <Popover onOpenChange={(next) => { setOpen(next); if (!next) setQuery('') }} open={open}>
      <PopoverTrigger asChild>
        <button aria-expanded={open} aria-label={ariaLabel} className="mt-3 flex h-9 w-full items-center justify-between rounded-md border bg-background px-3 text-left text-sm outline-none transition-colors hover:bg-muted/50 disabled:pointer-events-none disabled:opacity-50" disabled={disabled} type="button">
          <span className={selected ? 'truncate' : 'truncate text-muted-foreground'}>{selected?.label ?? placeholder}</span>
          <Search className="ml-2 size-4 shrink-0 text-muted-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(28rem,calc(100vw-3rem))] p-0">
        <Command shouldFilter={false}>
          <CommandInput onValueChange={setQuery} placeholder="模糊搜索模型或供应商…" value={query} />
          <CommandList className="max-h-72">
            <CommandEmpty>没有匹配的模型</CommandEmpty>
            {visible.map((option) => (
              <CommandItem key={option.value} onSelect={() => { onValueChange(option.value); setOpen(false) }} value={option.value}>
                <span className="truncate">{option.label}</span>
                {option.value === value ? <span className="ml-auto">✓</span> : null}
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
