import { useRef, useState } from 'react'
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
  onSelect: (content: string) => void
}

export function MemoryOpeningSuggestions({
  workspaceId,
  projectId,
  providerId,
  modelId,
  enabled,
  disabled,
  onSelect,
}: MemoryOpeningSuggestionsProps) {
  const exhaustedPool = useRef<string | undefined>(undefined)
  const suggestions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memories', 'opening-prompts', projectId ?? null),
    queryFn: ({ signal }) => {
      const exhausted = exhaustedPool.current
      exhaustedPool.current = undefined
      return generateOpeningPrompts({
        provider_id: providerId,
        model_id: modelId,
        project_id: projectId ?? null,
        count: 3,
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

  if (!enabled) return null
  return <NewChatSuggestions
    prompts={pool.slice(group * 3, group * 3 + 3)}
    pending={suggestions.isFetching}
    disabled={disabled}
    onRefresh={refresh}
    onSelect={onSelect}
  />
}
