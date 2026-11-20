import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { apiClient } from '@/api/client'
import type { ChatGoal } from '@/api/chat-goals'
import { renderWithProviders } from '@/test/render'
import { ChatGoalPanel, useChatGoal } from './chat-goal-panel'

const running: ChatGoal = {
  id: 'goal-1', objective: 'Produce a report', status: 'running',
  rounds_used: 1, max_rounds: 6, summary: 'Draft saved', last_message_id: 'm-1', updated_at: '2026-01-01',
}

function SessionGoals() {
  const goal = useChatGoal('workspace-1', 'session-1')
  return goal.data ? <ChatGoalPanel workspaceId="workspace-1" sessionId="session-1" goal={goal.data} readOnly={false} /> : null
}

describe('chat goal panel', () => {
  it('reads the persisted status and updates it with session-scoped controls', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue(running)
    const post = vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ ...running, status: 'paused' })
      .mockResolvedValueOnce(null)
    renderWithProviders(<SessionGoals />)
    expect(await screen.findByText('Produce a report')).toBeVisible()
    expect(screen.getByText('1/6 轮')).toBeVisible()
    expect(get).toHaveBeenCalledWith('/sessions/session-1/goal', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    fireEvent.click(screen.getByRole('button', { name: '暂停长程任务' }))
    expect(await screen.findByText('已暂停')).toBeVisible()
    expect(screen.getByRole('button', { name: '继续长程任务' })).toBeVisible()
    expect(post).toHaveBeenNthCalledWith(1, '/sessions/session-1/goal/control', { goal_id: 'goal-1', action: 'pause' })
    fireEvent.click(screen.getByRole('button', { name: '清除长程任务' }))
    await waitFor(() => expect(screen.queryByLabelText('长程任务')).not.toBeInTheDocument())
    expect(post).toHaveBeenNthCalledWith(2, '/sessions/session-1/goal/control', { goal_id: 'goal-1', action: 'clear' })
  })

  it('does not offer resume after the execution budget is used up', () => {
    renderWithProviders(<ChatGoalPanel workspaceId="workspace-1" sessionId="session-1"
      goal={{ ...running, status: 'budget_exhausted', rounds_used: 6 }} readOnly={false} />)
    expect(screen.getByText('轮次已用尽')).toBeVisible()
    expect(screen.queryByRole('button', { name: '继续长程任务' })).not.toBeInTheDocument()
  })
})
