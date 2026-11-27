import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { generateOpeningPrompts, type OpeningPromptsResponse } from '@/api/opening-prompts'
import { workspaceQueryKey } from '@/lib/query-keys'
import { NewChatSuggestions } from './new-chat-welcome'

export interface MemoryOpeningSuggestionsProps {
  workspaceId: string
  projectId?: string
  providerId?: string
  modelId?: string
  enabled: boolean
  disabled: boolean
  hidden?: boolean
  mode?: 'question' | 'goal'
  onSelect: (content: string) => void
}

const exampleGoals = [
  '两周内掌握 Python 数据分析，能独立清洗数据并做出一张图表。',
  '学会用 JavaScript 写小工具，独立完成一个待办清单。',
  '掌握英语口语中的常见表达，能完成三分钟的自我介绍。',
].map((content, index) => ({ id: `example-goal-${index}`, content }))

export function MemoryOpeningSuggestions({
  workspaceId,
  projectId,
  providerId,
  modelId,
  enabled,
  disabled,
  hidden = false,
  mode = 'question',
  onSelect,
}: MemoryOpeningSuggestionsProps) {
  const goal = mode === 'goal'
  const exhaustedPool = useRef<string | undefined>(undefined)
  const suggestions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memories', 'opening-prompts', projectId ?? null, ...(goal ? ['goal'] : [])),
    queryFn: ({ signal }) => {
      const exhausted = exhaustedPool.current
      exhaustedPool.current = undefined
      return generateOpeningPrompts({
        provider_id: providerId,
        model_id: modelId,
        project_id: projectId ?? null,
        count: 3,
        ...(goal ? { mode: 'goal' } : {}),
        ...(exhausted ? { exhausted_pool_id: exhausted } : {}),
      }, { signal, headers: { 'X-Workspace-ID': workspaceId } })
    },
    enabled,
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })

  const [rotation, setRotation] = useState<{ response?: OpeningPromptsResponse; group: number }>({ group: 0 })
  const response = suggestions.data
  const group = rotation.response === response ? rotation.group : 0
  const pool = response?.pool?.length ? response.pool : response?.prompts ?? []
  const groups = Math.ceil(pool.length / 3)
  const refreshAt = suggestions.dataUpdatedAt + (response?.metadata?.refresh_after_seconds ?? 0) * 1000
  const [clock, setClock] = useState(Date.now)
  const remaining = Math.max(0, Math.ceil((refreshAt - Math.max(clock, suggestions.dataUpdatedAt)) / 1000))
  const coolingDown = groups === 1 && remaining > 0
  useEffect(() => {
    if (!enabled || !coolingDown) return
    const timer = window.setTimeout(() => setClock(Date.now()), Math.min(1000, Math.max(0, refreshAt - Date.now())))
    return () => window.clearTimeout(timer)
  }, [enabled, coolingDown, clock, refreshAt])

  const refresh = () => {
    if (suggestions.isFetching || disabled) return
    if (groups > 1) setRotation({ response, group: (group + 1) % groups })
    const refreshAfter = response?.metadata?.refresh_after_seconds
    const canGenerate = refreshAfter === undefined || Date.now() >= suggestions.dataUpdatedAt + refreshAfter * 1000
    if (canGenerate && (groups <= 1 || group === groups - 1)) {
      exhaustedPool.current = response?.metadata?.request_id
      void suggestions.refetch()
    }
  }

  if (!enabled && !goal) return null
  const visiblePrompts = pool.slice(group * 3, group * 3 + 3)
  return <NewChatSuggestions
    mode={mode}
    prompts={goal && !visiblePrompts.length ? exampleGoals : visiblePrompts}
    pending={suggestions.isFetching}
    disabled={disabled}
    hidden={hidden}
    onRefresh={enabled ? refresh : undefined}
    refreshHint={coolingDown ? `请在 ${remaining} 秒后再换` : undefined}
    onSelect={onSelect}
  />
}
