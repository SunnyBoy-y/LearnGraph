import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/test/render'
import { downloadFullBackup, restoreFullBackup } from '@/api/migrations'
import { SiteBackupPanel } from './site-backup-panel'

const state = vi.hoisted(() => ({ isSystemAdmin: true }))
vi.mock('@/features/auth/auth-context-value', () => ({ useAuth: () => state }))
vi.mock('@/api/migrations', () => ({ downloadFullBackup: vi.fn(), restoreFullBackup: vi.fn() }))
vi.mock('@/lib/native-download', () => ({ nativeDownloadSupported: () => false, saveBlobViaNative: async () => false }))

beforeEach(() => {
  vi.clearAllMocks()
  state.isSystemAdmin = true
})

describe('site backup authorization and recovery UI', () => {
  it('hides export and recovery from workspace administrators without system admin access', () => {
    state.isSystemAdmin = false
    renderWithProviders(<SiteBackupPanel />)
    expect(screen.queryByRole('button', { name: '下载加密全站备份' })).not.toBeInTheDocument()
    expect(screen.getByText('全站备份与恢复仅系统管理员可操作。')).toBeInTheDocument()
  })

  it('does not download before entering the current password and matching backup passwords', async () => {
    vi.mocked(downloadFullBackup).mockRejectedValue(new Error('test request stopped'))
    renderWithProviders(<SiteBackupPanel />)
    fireEvent.click(screen.getByRole('button', { name: '下载加密全站备份' }))
    const confirm = screen.getByRole('button', { name: '验证并下载' })
    expect(confirm).toBeDisabled()
    expect(downloadFullBackup).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('当前管理员密码'), { target: { value: 'admin password' } })
    fireEvent.change(screen.getByLabelText('备份密码', { exact: true }), { target: { value: 'backup-password-2026' } })
    fireEvent.change(screen.getByLabelText('再次输入备份密码'), { target: { value: 'different' } })
    expect(confirm).toBeDisabled()
    fireEvent.change(screen.getByLabelText('再次输入备份密码'), { target: { value: 'backup-password-2026' } })
    fireEvent.click(confirm)
    await waitFor(() => expect(downloadFullBackup).toHaveBeenCalledWith('admin password', 'backup-password-2026'))
    await waitFor(() => expect(screen.getByLabelText('当前管理员密码')).toHaveValue(''))
  })

  it('clears passwords when the dialog is cancelled', () => {
    renderWithProviders(<SiteBackupPanel />)
    fireEvent.click(screen.getByRole('button', { name: '下载加密全站备份' }))
    fireEvent.change(screen.getByLabelText('当前管理员密码'), { target: { value: 'secret' } })
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    fireEvent.click(screen.getByRole('button', { name: '下载加密全站备份' }))
    expect(screen.getByLabelText('当前管理员密码')).toHaveValue('')
    expect(screen.getByLabelText('备份密码', { exact: true })).toHaveValue('')
  })

  it('reports validated staging without claiming that the site has been restored', async () => {
    vi.mocked(restoreFullBackup).mockResolvedValue({ job_id: 'job', source_workspace_id: null, tables: 10, records: 42, files: 3, memory_files: 2, mode: 'staged', restore_path: '/data/site-restore/job.zip', restart_required: true })
    renderWithProviders(<SiteBackupPanel />)
    fireEvent.click(screen.getByRole('button', { name: '校验全站备份并暂存恢复' }))
    const file = new File(['archive'], 'site.zip', { type: 'application/zip' })
    await userEvent.upload(screen.getByLabelText('全站备份 ZIP'), file)
    fireEvent.change(screen.getByLabelText('当前管理员密码'), { target: { value: 'current-admin' } })
    fireEvent.change(screen.getByLabelText('备份密码'), { target: { value: 'backup-password' } })
    const submit = screen.getByRole('button', { name: '验证并暂存' })
    expect(submit).toBeEnabled()
    // jsdom 30's native required-file validation does not see user-event's
    // FileList. Exercise the submit handler after checking the state gate.
    fireEvent.submit(submit.closest('form')!)
    await waitFor(() => expect(restoreFullBackup).toHaveBeenCalledWith(file, 'current-admin', 'backup-password'))
    expect(await screen.findByRole('status')).toHaveTextContent('当前数据尚未覆盖')
    expect(screen.getByRole('status')).toHaveTextContent('python -m app.services.site_restore')
  })
})
