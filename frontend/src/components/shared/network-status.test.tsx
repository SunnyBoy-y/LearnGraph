import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { NetworkStatus } from './network-status'
import { networkQualityStore } from '@/lib/network-quality'
import { offlineOutbox } from '@/lib/offline-outbox'

describe('NetworkStatus', () => {
  beforeEach(() => {
    networkQualityStore.reset()
    offlineOutbox.clearAll()
    localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    networkQualityStore.reset()
    offlineOutbox.clearAll()
  })

  it('stays hidden when network is good and outbox is empty', () => {
    const { container } = render(<NetworkStatus />)
    expect(container.firstChild).toBeNull()
  })

  it('does not render weak-network banner when latency exceeds threshold', () => {
    const { container } = render(<NetworkStatus />)
    act(() => {
      networkQualityStore.recordSample({ ok: true, durationMs: 2_200 })
    })
    expect(container.firstChild).toBeNull()
  })

  it('renders outbox retry and dismiss controls when queued sync fails', async () => {
    const { apiClient } = await import('@/api/client')
    vi.spyOn(apiClient, 'patch').mockRejectedValue(new Error('HTTP 409 conflict'))

    render(<NetworkStatus />)
    act(() => {
      offlineOutbox.enqueue({
        label: '保存节点',
        method: 'PATCH',
        path: '/graphs/g1/nodes/n1',
        body: { label: 'x' },
        workspaceId: 'default',
      })
    })
    await act(async () => {
      await offlineOutbox.flush()
    })

    expect(screen.getByRole('status').textContent).toContain('未能自动同步')
    fireEvent.click(screen.getByRole('button', { name: '忽略' }))
    expect(screen.queryByRole('status')).toBeNull()
  })
})
