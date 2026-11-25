import { describe, expect, it } from 'vitest'
import { memoryOccurrenceHint } from './memory-display'
import type { MemoryTemporalSemantics } from '@/types/memory'

describe('memory occurrence display', () => {
  it('keeps the original expression and normalized date distinct from update time', () => {
    const temporal_semantics = { original_expression: '  昨天  ', normalized_display: '2026年10月18日' } as MemoryTemporalSemantics
    expect(memoryOccurrenceHint({ temporal_semantics, event_at: '2026-10-17T16:00:00Z', timezone_name: 'Asia/Shanghai' })).toBe('  昨天   · 2026年10月18日')
  })
  it('renders legacy naive UTC timestamps in the memory timezone', () => {
    expect(memoryOccurrenceHint({ event_at: '2026-10-17T16:00:00', timezone_name: 'Asia/Shanghai' })).toContain('2026/10/18')
    expect(memoryOccurrenceHint({ event_at: null, timezone_name: 'Asia/Shanghai' })).toBeNull()
    expect(memoryOccurrenceHint({ event_at: 'invalid', timezone_name: 'bad-zone' })).toBeNull()
  })
})
