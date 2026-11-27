import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { MessageActions } from '@/components/ai-elements/message'
import { CopyMessageAction } from '@/components/shared/copy-feedback'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AuthContext, type AuthContextValue } from '@/features/auth/auth-context-value'
import { RailConversationMessage } from '@/features/chat/rail-conversation'
import { beginStreamStats, recordStreamDelta } from '@/features/chat/stream-stats'
import { renderWithProviders } from '@/test/render'
import type { Message } from '@/types/sessions'
import { StreamStatsAction, StreamStatsBadge } from './stream-stats-badge'

const sampleTrace = {
  first_token_ms: 3500,
  output_tokens: 16,
  generation_duration_ms: 4571,
  input_tokens: 1000,
  cached_input_tokens: 90,
}

const testAuthValue: AuthContextValue = {
  authenticated: true,
  username: 'tester',
  avatarUrl: null,
  updateProfile: () => undefined,
  workspaceId: 'ws-1',
  userId: 'user-1',
  isSystemAdmin: false,
  effectivePermissions: [],
  workspaceName: '测试工作区',
  mustChangePassword: false,
  setWorkspaceId: async () => undefined,
  login: async () => ({ workspaceId: 'ws-1', mustChangePassword: false }),
  demoLogin: async () => ({ workspaceId: 'ws-1', mustChangePassword: false }),
  register: async () => ({ workspaceId: 'ws-1', mustChangePassword: false }),
  changePassword: async () => undefined,
  deleteAccount: async () => undefined,
  logout: async () => undefined,
}

describe('StreamStatsBadge and StreamStatsAction', () => {
  it('hides standalone completed stats when showCompleted is false and renders stats in the action bar', async () => {
    window.localStorage.setItem('learngraph.showStreamStats', '1')
    window.localStorage.setItem('learngraph.showCacheHit', '1')
    const user = userEvent.setup()

    const { container } = render(
      <TooltipProvider>
        <div>
          <StreamStatsBadge
            messageId="msg-completed"
            providerTrace={sampleTrace}
            showCompleted={false}
            status="completed"
          />
          <MessageActions>
            <CopyMessageAction label="复制全文" text="回复正文" />
            <StreamStatsAction
              messageId="msg-completed"
              providerTrace={sampleTrace}
              status="completed"
            />
          </MessageActions>
        </div>
      </TooltipProvider>,
    )

    expect(container.querySelector('details')).toBeNull()
    expect(container.querySelector('.message-stream-stats')).toBeNull()
    expect(container.querySelector('.message-stream-stats__badge')).toBeNull()
    expect(screen.getByText('生成详情')).toHaveClass('sr-only')

    const copyBtn = screen.getByRole('button', { name: '复制全文' })
    const statsBtn = screen.getByRole('button', { name: '生成详情' })
    expect(statsBtn).toHaveAttribute('data-size', copyBtn.getAttribute('data-size'))
    expect(statsBtn).toHaveAttribute('data-variant', copyBtn.getAttribute('data-variant'))

    await user.click(statsBtn)

    const dialog = await screen.findByRole('dialog', { name: '生成详情' })
    expect(dialog).toHaveTextContent('生成详情')
    expect(dialog).toHaveTextContent('本次回复')
    expect(dialog).toHaveTextContent('首字延迟3.5s')
    expect(dialog).toHaveTextContent('生成速度3.5tok/s')
    expect(dialog).toHaveTextContent('输出 Token16')
    expect(dialog).toHaveTextContent('缓存命中率9%')
    const fill = dialog.querySelector('.message-stream-popover__bar-fill') as HTMLElement | null
    expect(fill).not.toBeNull()
    expect(fill!.style.transform).toBe('scaleX(0.09)')
    expect(dialog.querySelector('.message-stream-stats__badge')).toBeNull()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('opens on hover, stays open when moving into the popover, and pins on click', async () => {
    window.localStorage.setItem('learngraph.showStreamStats', '1')
    const user = userEvent.setup()

    render(
      <StreamStatsAction
        messageId="msg-hover"
        providerTrace={{ first_token_ms: 420, output_tokens: 48, generation_duration_ms: 1200 }}
        status="completed"
      />,
    )

    const trigger = screen.getByRole('button', { name: '生成详情' })
    fireEvent.pointerEnter(trigger, { pointerType: 'mouse' })

    const dialog = await screen.findByRole('dialog', { name: '生成详情' })
    expect(dialog).toHaveTextContent('首字延迟420ms')
    expect(dialog).toHaveTextContent('生成速度40.0tok/s')
    expect(dialog).toHaveTextContent('输出 Token48')

    // Moving from trigger into popover keeps it open; leaving popover closes it.
    fireEvent.pointerLeave(trigger)
    fireEvent.pointerEnter(dialog)
    await new Promise((resolve) => setTimeout(resolve, 220))
    expect(screen.getByRole('dialog', { name: '生成详情' })).toBeInTheDocument()
    fireEvent.pointerLeave(dialog)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    // Hovering then clicking pins the popover open even after pointer leaves.
    fireEvent.pointerEnter(trigger, { pointerType: 'mouse' })
    await screen.findByRole('dialog', { name: '生成详情' })
    await user.click(trigger)
    fireEvent.pointerLeave(trigger)
    await new Promise((resolve) => setTimeout(resolve, 220))
    expect(screen.getByRole('dialog', { name: '生成详情' })).toBeInTheDocument()

    // Clicking again dismisses the pinned popover.
    await user.click(trigger)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('opens on keyboard focus and hides cache row when cache-hit setting is disabled', async () => {
    window.localStorage.setItem('learngraph.showStreamStats', '1')
    const user = userEvent.setup()

    render(
      <StreamStatsAction
        messageId="msg-kbd"
        providerTrace={sampleTrace}
        status="completed"
      />,
    )

    await user.tab()
    const dialog = await screen.findByRole('dialog', { name: '生成详情' })
    expect(dialog).toHaveTextContent('输出 Token16')
    expect(dialog).not.toHaveTextContent('缓存命中率')
  })

  it('keeps rendering live stream indicator while streaming and renders nothing when disabled', () => {
    const now = Date.now()
    beginStreamStats('msg-live', now - 900)
    recordStreamDelta('msg-live', '你好世界')

    const { rerender, container } = render(
      <StreamStatsBadge
        messageId="msg-live"
        providerTrace={{}}
        showCompleted={false}
        status="streaming"
      />,
    )
    expect(container.querySelector('.message-stream-stats--loading')).not.toBeNull()

    rerender(
      <StreamStatsAction
        messageId="msg-live"
        providerTrace={{}}
        status="streaming"
      />,
    )
    expect(screen.queryByRole('button', { name: '生成详情' })).not.toBeInTheDocument()

    rerender(
      <StreamStatsAction
        messageId="msg-off"
        providerTrace={sampleTrace}
        status="completed"
      />,
    )
    expect(screen.queryByRole('button', { name: '生成详情' })).not.toBeInTheDocument()
  })

  it('wraps completed stats in MessageActions when showCompleted is true by default', async () => {
    window.localStorage.setItem('learngraph.showStreamStats', '1')
    window.localStorage.setItem('learngraph.showCacheHit', '1')
    const user = userEvent.setup()

    const { container } = render(
      <StreamStatsBadge
        messageId="msg-wrap"
        providerTrace={sampleTrace}
        status="completed"
      />,
    )

    expect(container.querySelector('details')).toBeNull()
    expect(container.querySelector('.message-stream-stats')).toBeNull()
    const statsBtn = screen.getByRole('button', { name: '生成详情' })
    expect(statsBtn.parentElement).toHaveClass('flex', 'items-center', 'gap-1')

    await user.click(statsBtn)
    const dialog = await screen.findByRole('dialog', { name: '生成详情' })
    expect(dialog).toHaveTextContent('缓存命中率9%')
  })

  it('consolidates stats inside the single action bar in RailConversationMessage', () => {
    window.localStorage.setItem('learngraph.showStreamStats', '1')
    const message: Message = {
      id: 'rail-msg-1',
      session_id: 'sess-1',
      role: 'assistant',
      content: '这是回答内容',
      parts: [{ id: 'p-1', type: 'text', status: 'completed', content: '这是回答内容' }],
      version: 1,
      status: 'completed',
      provider_trace: sampleTrace,
      created_at: '2026-11-25T00:00:00Z',
      updated_at: '2026-11-25T00:00:00Z',
    }

    const { container } = renderWithProviders(
      <AuthContext.Provider value={testAuthValue}>
        <TooltipProvider>
          <RailConversationMessage message={message} />
        </TooltipProvider>
      </AuthContext.Provider>,
    )

    expect(container.querySelector('.message-stream-stats')).toBeNull()
    expect(container.querySelector('details')).toBeNull()
    const copyBtn = screen.getByRole('button', { name: '复制全文' })
    const statsBtn = screen.getByRole('button', { name: '生成详情' })
    expect(copyBtn.parentElement).toBe(statsBtn.parentElement)
  })
})
