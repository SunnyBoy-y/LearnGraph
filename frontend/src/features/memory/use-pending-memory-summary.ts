import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

import { listMemoryDrafts, listMemories } from '@/api'
import type { MemoryEntry } from '@/types/memory'

type PendingGroup = 'drafts' | 'needs_confirmation' | 'disputed' | 'inferred'

export function matchesPendingGroup(item: MemoryEntry, group: PendingGroup): boolean {
  if (group === 'inferred') return item.assertion_type === 'inferred'
  return item.lifecycle_status === group
}

/**
 * 待确认记忆的分组计数。
 *
 * 与 MemoryPendingConfirmationPanel 共用同一组 query key（['memory-pending'] /
 * ['memory-drafts']），因此页面徽标、报告底部提醒与面板之间不会重复请求。
 */
export function usePendingMemorySummary() {
  const active = useQuery({
    queryKey: ['memory-pending'],
    queryFn: () => listMemories({ state: 'active', include_content: true }),
  })
  const drafts = useQuery({
    queryKey: ['memory-drafts'],
    queryFn: () => listMemoryDrafts({ status: 'PENDING' }),
  })

  return useMemo(() => {
    const items = active.data ?? []
    const draftItems = drafts.data ?? []
    const counts = {
      drafts: draftItems.length,
      needs_confirmation: items.filter((item) => matchesPendingGroup(item, 'needs_confirmation'))
        .length,
      disputed: items.filter((item) => matchesPendingGroup(item, 'disputed')).length,
      inferred: items.filter((item) => matchesPendingGroup(item, 'inferred')).length,
    }
    return {
      counts,
      total: counts.drafts + counts.needs_confirmation + counts.disputed + counts.inferred,
    }
  }, [active.data, drafts.data])
}
