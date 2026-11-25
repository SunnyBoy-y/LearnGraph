import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { expect, it, vi } from 'vitest'
import { listSessionPage, type SessionPage, type SessionSummary } from '@/api/session-browser'
import { listProjects, restoreSession } from '@/api/workflow'
import type { Session } from '@/types/sessions'
import { ArchivedSessionsPage } from './archived-sessions-page'

vi.mock('@/api/session-browser', () => ({ listSessionPage: vi.fn() }))
vi.mock('@/api/workflow', () => ({
  listProjects: vi.fn(),
  restoreSession: vi.fn(),
}))

it('opens archived history from settings, loads another page, and removes a restored session', async () => {
  const branch = { id: 'branch', workspace_id: 'w1', title: '旧分支', status: 'archived', project_id: 'project' } as SessionSummary
  const extra = { ...branch, id: 'extra', title: '另一会话' }
  const restored = { ...branch, status: 'active' } as Session
  const page = (items: SessionSummary[], cursor: string | null): SessionPage => ({
    items, has_more: Boolean(cursor), next_cursor: cursor, has_previous: false, previous_cursor: null,
  })
  vi.mocked(listProjects).mockResolvedValue([{ id: 'project', title: '项目甲' } as Awaited<ReturnType<typeof listProjects>>[number]])
  let archived = true
  vi.mocked(listSessionPage).mockImplementation(async (_scope, params) =>
    params?.cursor || !archived ? page([extra], null) : page([branch], 'next'),
  )
  vi.mocked(restoreSession).mockImplementation(async () => {
    archived = false
    return restored
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/w/w1/settings/archived-sessions']}>
        <Routes><Route path="/w/:workspaceId/settings/archived-sessions" element={<ArchivedSessionsPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  expect(await screen.findByRole('link', { name: '旧分支' })).toHaveAttribute('href', '/w/w1/chat/branch')
  expect(await screen.findByText('项目甲')).toBeInTheDocument()
  expect(listSessionPage).toHaveBeenCalledWith(expect.objectContaining({ archived_only: true, roots_only: false }), {}, expect.anything())
  fireEvent.click(screen.getByRole('button', { name: '加载更多' }))
  expect(await screen.findByRole('link', { name: '另一会话' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '恢复会话 旧分支' }))
  await screen.findByRole('button', { name: '恢复会话 另一会话' })
  await vi.waitFor(() => expect(screen.queryByRole('link', { name: '旧分支' })).not.toBeInTheDocument())
  expect(restoreSession).toHaveBeenCalledWith('branch', expect.anything())
  client.clear()
})
