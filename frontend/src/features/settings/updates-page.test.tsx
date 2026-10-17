import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { getCurrentUser } from '@/api/control'
import { checkUpdates, getAppVersion, getUpdateStatus, setUpdatePolicy, startUpdate, type UpdateStatus } from '@/api/updates'
import { ApiError } from '@/api/client'
import { UpdatesPage } from './updates-page'

vi.mock('@/api/control', () => ({ getCurrentUser: vi.fn() }))
vi.mock('@/api/updates', async importOriginal => ({
  ...await importOriginal<typeof import('@/api/updates')>(),
  getAppVersion: vi.fn(), getUpdateStatus: vi.fn(), checkUpdates: vi.fn(), startUpdate: vi.fn(), setUpdatePolicy: vi.fn(),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const status: UpdateStatus = {
  enabled: true, current: { version: 'v1.0.0' }, has_update: true, auto_install: false,
  latest: { version: 'v1.1.0', notes: '<script>untrusted release text</script>', url: 'https://github.com/SunnyBoy-y/LearnGraph/releases/tag/v1.1.0', published_at: '' }, jobs: [],
}
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><UpdatesPage /></QueryClientProvider>)
  return client
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getCurrentUser).mockResolvedValue({ is_system_admin: true } as Awaited<ReturnType<typeof getCurrentUser>>)
  vi.mocked(getUpdateStatus).mockResolvedValue(structuredClone(status))
  vi.mocked(getAppVersion).mockResolvedValue({ version: 'v0.5' })
  vi.mocked(startUpdate).mockResolvedValue({ id: 'job', status: 'queued', kind: 'update', message: '排队', created_at: '', target_version: 'v1.1.0' })
})
afterEach(cleanup)

it('keeps the actual application version visible if the updater is offline', async () => {
  vi.mocked(getUpdateStatus).mockRejectedValue(new Error('更新器离线'))
  mount()
  expect(await screen.findByText('v0.5')).toBeInTheDocument()
  expect(screen.queryByText('读取中')).toBeNull()
  expect(screen.getByRole('button', { name: '检查更新' })).toBeDisabled()
})

it('reports an old backend without mislabeling a permanent 404 as a reconnect', async () => {
  vi.mocked(getUpdateStatus).mockRejectedValue(new ApiError({ status: 404, code: 'not_found', message: 'Not Found', method: 'GET', url: '/updates/status' }))
  mount()
  await screen.findByText('当前后端尚未部署版本管理接口，请更新应用容器。')
  expect(screen.getByText('v0.5')).toBeInTheDocument()
  expect(screen.queryByText(/正在重连/)).toBeNull()
})

it('does not request instance configuration for a non-admin', async () => {
  vi.mocked(getCurrentUser).mockResolvedValue({ is_system_admin: false } as Awaited<ReturnType<typeof getCurrentUser>>)
  mount()
  await screen.findByText('仅系统管理员可管理应用版本。')
  expect(getUpdateStatus).not.toHaveBeenCalled()
})

it('submits an upgrade only after the user confirms', async () => {
  mount()
  fireEvent.click(await screen.findByRole('button', { name: '立即更新' }))
  expect(startUpdate).not.toHaveBeenCalled()
  fireEvent.click(await screen.findByRole('button', { name: '确认切换' }))
  await waitFor(() => expect(startUpdate).toHaveBeenCalledWith({ version: 'v1.1.0' }, expect.anything()))
})

it('renders release notes as text and checks for updates', async () => {
  vi.mocked(checkUpdates).mockResolvedValue(status)
  mount()
  expect(await screen.findByText('<script>untrusted release text</script>')).toBeInTheDocument()
  expect(document.querySelector('script')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '检查更新' }))
  await waitFor(() => expect(checkUpdates).toHaveBeenCalledTimes(1))
})

it('requires confirmation before enabling unattended installation', async () => {
  mount()
  fireEvent.click(await screen.findByRole('switch', { name: '自动安装正式版本' }))
  expect(setUpdatePolicy).not.toHaveBeenCalled()
  fireEvent.click(await screen.findByRole('button', { name: '启用自动安装' }))
  await waitFor(() => expect(setUpdatePolicy).toHaveBeenCalledWith(true, expect.anything()))
})

it('disables installation while a task runs and shows reconnect progress', async () => {
  vi.mocked(getUpdateStatus).mockResolvedValue({ ...status, jobs: [{ id: 'job', status: 'applying', kind: 'update', target_version: 'v1.1.0', message: '正在替换容器', created_at: '' }] })
  mount()
  expect(await screen.findByRole('button', { name: '立即更新' })).toBeDisabled()
  expect(screen.getByText('正在替换容器', { selector: '[role="status"] p' })).toBeInTheDocument()
})

it('submits the selected retained deployment for rollback', async () => {
  vi.mocked(getUpdateStatus).mockResolvedValue({ ...status, current: { version: 'v1.1.0' }, has_update: false,
    jobs: [{ id: 'retained', status: 'succeeded', kind: 'update', target_version: 'v1.1.0', previous_version: 'v1.0.0', message: '完成', created_at: '' }] })
  mount()
  fireEvent.click(await screen.findByRole('button', { name: '回退到 v1.0.0' }))
  fireEvent.click(await screen.findByRole('button', { name: '确认切换' }))
  await waitFor(() => expect(startUpdate).toHaveBeenCalledWith({ rollback_id: 'retained' }, expect.anything()))
})
