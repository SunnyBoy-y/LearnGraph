import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { checkUpdates, getAppVersion, getUpdateStatus, startUpdate, type UpdateStatus } from '@/api/updates'
import { SidebarVersion } from './sidebar-version'

vi.mock('@/api/updates', async original => ({
  ...await original<typeof import('@/api/updates')>(),
  getAppVersion: vi.fn(), getUpdateStatus: vi.fn(), checkUpdates: vi.fn(), startUpdate: vi.fn(),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
const status: UpdateStatus = {
  enabled: true, current: { version: 'v0.5' }, has_update: true, jobs: [],
  latest: { version: 'v0.6', notes: '', published_at: '', url: 'https://github.com/SunnyBoy-y/LearnGraph/releases/tag/v0.6' },
}
function mount(admin = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><SidebarVersion isSystemAdmin={admin} /></QueryClientProvider>)
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getAppVersion).mockResolvedValue({ version: 'v0.5', runtime_mode: 'docker' })
  vi.mocked(getUpdateStatus).mockResolvedValue(structuredClone(status))
  vi.mocked(checkUpdates).mockResolvedValue(status)
  vi.mocked(startUpdate).mockResolvedValue({ id: 'job', status: 'queued', kind: 'update', message: '排队中', created_at: '', target_version: 'v0.6' })
})
it('shows the update dot and installs the selected release after confirmation', async () => {
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /有新版本可用/ }))
  expect(screen.getByRole('link', { name: '查看更新日志' })).toHaveAttribute('href', status.latest!.url)
  fireEvent.click(screen.getByRole('button', { name: '立即更新' }))
  expect(startUpdate).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '确认更新' }))
  await waitFor(() => expect(startUpdate).toHaveBeenCalledWith({ version: 'v0.6' }, expect.anything()))
})
it.each(['source', undefined] as const)('hides upgrades for %s runtime even for admins', async runtime_mode => {
  vi.mocked(getAppVersion).mockResolvedValue({ version: 'v0.5', runtime_mode })
  mount()
  fireEvent.click(await screen.findByRole('button', { name: '版本与更新：v0.5' }))
  expect(getUpdateStatus).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: '立即更新' })).toBeNull()
})
it('does not request privileged update data for ordinary users', async () => {
  mount(false)
  fireEvent.click(await screen.findByRole('button', { name: '版本与更新：v0.5' }))
  expect(getUpdateStatus).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: '立即更新' })).toBeNull()
})
it('hides the update button when up to date and supports manual checking', async () => {
  vi.mocked(getUpdateStatus).mockResolvedValue({ ...status, has_update: false })
  mount()
  await waitFor(() => expect(getUpdateStatus).toHaveBeenCalled())
  fireEvent.click(await screen.findByRole('button', { name: '版本与更新：v0.5' }))
  expect(await screen.findByText('已是最新版本')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '立即更新' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '检查更新' }))
  expect(await screen.findByRole('button', { name: '立即更新' })).toBeInTheDocument()
})
it('keeps the app version available when the updater is offline', async () => {
  vi.mocked(getUpdateStatus).mockRejectedValue(new Error('offline'))
  mount()
  fireEvent.click(await screen.findByRole('button', { name: '版本与更新：v0.5' }))
  expect(await screen.findByText('暂时无法获取更新状态，请稍后重试。')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '立即更新' })).toBeNull()
})
it('prevents duplicate updates during an active job', async () => {
  vi.mocked(getUpdateStatus).mockResolvedValue({ ...status, jobs: [{ id: 'job', status: 'applying', kind: 'update', message: '正在替换容器', created_at: '', target_version: 'v0.6' }] })
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /有新版本可用/ }))
  expect(screen.getByRole('button', { name: '正在更新…' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '检查更新' })).toBeDisabled()
  expect(screen.getByRole('status')).toHaveTextContent('正在替换容器')
})
