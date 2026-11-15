import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SessionPageSentinel, VirtualSessionList } from './virtual-session-list'
import type { SessionNavigator } from './use-session-navigator'

it('mounts a bounded window for 10,000 loaded records', async () => {
  const rows = Array.from({ length: 10_000 }, (_, index) => ({ key: `s${index}` }))
  const view = render(<div className="sidebar-nav__scroll" style={{ height: 640, overflow: 'auto' }}>
    <VirtualSessionList rows={rows} workspaceId="virtual-test" renderRow={row => <button>{row.key}</button>} />
  </div>)
  await waitFor(() => expect(view.container.querySelectorAll('[data-virtual-key]').length).toBeGreaterThan(0))
  expect(view.container.querySelectorAll('[data-virtual-key]').length).toBeLessThan(80)
  view.unmount()
})

describe('continuation sentinel', () => {
  it('keeps errors local and requires explicit retry after a failure', () => {
    const load = vi.fn(), activate = vi.fn()
    const navigator = { load, activate, scopes: new Map([['recent', { isError: true, isPending: false, isFetching: false, hasNextPage: true }]]) } as unknown as SessionNavigator
    render(<div className="sidebar-nav__scroll"><SessionPageSentinel scope={{ ungrouped: true }} navigator={navigator} /></div>)
    expect(load).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '加载失败，点击重试' }))
    expect(load).toHaveBeenCalledWith({ ungrouped: true }, 'next', true)
  })
})
