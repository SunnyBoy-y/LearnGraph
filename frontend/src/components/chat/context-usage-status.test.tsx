import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { compactSessionContext } from '@/api/sessions'
import { ContextUsageStatus } from './context-usage-status'
import type { SessionContextUsage } from '@/types/sessions'

vi.mock('@/api/sessions', () => ({ compactSessionContext: vi.fn() }))
const usage: SessionContextUsage = { session_id: 'one', estimated_tokens: 17000, context_window_tokens: 100000, input_budget_tokens: 80000, compaction_threshold_tokens: 20000, remaining_tokens: 3000, used_ratio: .85, compaction_ratio: .25, message_count: 2, compacted_message_count: 0 }

describe('composer usage status', () => {
  it('shows actual window occupancy and opens on hover, retaining the panel when the pointer enters it', async () => {
    render(<ContextUsageStatus usage={usage} sessionId="one" messages={[]} />)
    const trigger = screen.getByRole('button', { name: '上下文用量与会话统计' })
    expect(trigger).toHaveTextContent('上下文 17%')
    expect(trigger.querySelector('svg')).toBeNull()
    fireEvent.pointerEnter(trigger, { pointerType: 'mouse' })
    const panel = await screen.findByRole('dialog', { name: '上下文用量详情' })
    fireEvent.pointerLeave(trigger)
    fireEvent.pointerEnter(panel)
    expect(panel).toHaveTextContent('窗口占用17%')
    expect(panel).toHaveTextContent('输入 token—')
    fireEvent.pointerLeave(panel)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
  it('supports click and keyboard dismissal, and forwards the selected model to compaction', async () => {
    const user = userEvent.setup()
    const onCompacted = vi.fn()
    vi.mocked(compactSessionContext).mockResolvedValue({ skipped: false, estimated_tokens_before: 17000, estimated_tokens_after: 10000 } as Awaited<ReturnType<typeof compactSessionContext>>)
    render(<ContextUsageStatus usage={usage} sessionId="one" providerId="provider" modelId="model" agentMode messages={[]} onCompacted={onCompacted} />)
    await user.click(screen.getByRole('button', { name: '上下文用量与会话统计' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '立即压缩上下文' }))
    expect(compactSessionContext).toHaveBeenCalledWith('one', { provider_id: 'provider', model_id: 'model', agent_mode: true })
    await waitFor(() => expect(onCompacted).toHaveBeenCalledOnce())
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
  it('keeps manual compaction disabled below its threshold and opens by keyboard focus', async () => {
    const user = userEvent.setup()
    render(<ContextUsageStatus usage={{ ...usage, used_ratio: .2 }} sessionId="one" messages={[]} hasMoreHistory />)
    await user.tab()
    expect(await screen.findByRole('dialog')).toHaveTextContent('仅统计已加载的消息')
    expect(screen.getByRole('button', { name: '立即压缩上下文' })).toBeDisabled()
  })
})
