import { describe, expect, it } from 'vitest'

import {
  completeMemoryProcess, failMemoryProcess, getMemoryProcess,
  recordMemoryProcessStep, startMemoryProcess,
} from './memory-process'

describe('memory Agent execution timeline', () => {
  it('only displays tools reported by the server', () => {
    const id = startMemoryProcess({ workspaceId: 'real-events', text: '记住偏好' })
    expect(getMemoryProcess('real-events')?.steps.map((s) => s.kind)).toEqual(['request'])
    recordMemoryProcessStep('real-events', id, {
      id: 'tool-1', kind: 'tool', label: '工具调用：读取现有记忆', status: 'running',
    })
    expect(getMemoryProcess('real-events')?.steps[1].status).toBe('running')
    recordMemoryProcessStep('real-events', id, {
      id: 'tool-1', kind: 'tool', label: '工具调用：读取现有记忆', status: 'completed', detail: '已读取 2 条记忆',
    })
    const steps = getMemoryProcess('real-events')?.steps ?? []
    expect(steps).toHaveLength(2)
    expect(steps[1].detail).toBe('已读取 2 条记忆')
    expect(steps[1].completedAt).toBeDefined()
  })

  it('retains failed tool attempts and shows authoritative pending counts', () => {
    const id = startMemoryProcess({ workspaceId: 'recovery', text: '改偏好' })
    recordMemoryProcessStep('recovery', id, {
      id: 'tool-1', kind: 'tool', label: '工具调用：更新记忆', status: 'failed', detail: '未知目标',
    })
    completeMemoryProcess('recovery', id, {
      status: 'ok', auto_committed: 1, drafts_created: 2,
      affected_memory_ids: ['m1'], profile_status: 'ready', message: '另一条草稿待确认。',
    })
    const process = getMemoryProcess('recovery')!
    expect(process.status).toBe('completed')
    expect(process.steps.find((s) => s.id === 'tool-1')?.status).toBe('failed')
    expect(process.steps.at(-1)?.detail).toContain('提交 1 条，待确认 1 条')
  })

  it('ignores late events from previous runs and preserves completed writes on failure', () => {
    const old = startMemoryProcess({ workspaceId: 'late-events', text: 'first' })
    const current = startMemoryProcess({ workspaceId: 'late-events', text: 'second' })
    recordMemoryProcessStep('late-events', old, {
      id: 'old-tool', kind: 'tool', label: '过期事件', status: 'completed',
    })
    recordMemoryProcessStep('late-events', current, {
      id: 'write', kind: 'tool', label: '更新记忆', status: 'completed', detail: '提交 1 条',
    })
    failMemoryProcess('late-events', current, '连接中断')
    expect(getMemoryProcess('late-events')?.steps.some((s) => s.id === 'old-tool')).toBe(false)
    expect(getMemoryProcess('late-events')?.steps.find((s) => s.id === 'write')?.status).toBe('completed')
    expect(getMemoryProcess('late-events')?.status).toBe('failed')
    expect(getMemoryProcess('other-workspace')).toBeNull()
  })
})
