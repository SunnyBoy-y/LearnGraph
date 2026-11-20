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
  vi.resetAllMocks()
  state.isSystemAdmin = true
})

function openBackup() {
  renderWithProviders(<SiteBackupPanel />)
  fireEvent.click(screen.getByRole('button', { name: '下载加密全站备份' }))
  return screen.getByRole('button', { name: '验证并下载' })
}

function fillBackupPasswords(password: string, confirmation = password) {
  fireEvent.change(screen.getByLabelText('当前管理员密码'), { target: { value: 'admin password' } })
  fireEvent.change(screen.getByLabelText('备份密码', { exact: true }), { target: { value: password } })
  fireEvent.change(screen.getByLabelText('再次输入备份密码'), { target: { value: confirmation } })
}

describe('site backup authorization and recovery UI', () => {
  it('hides export and recovery from workspace administrators without system admin access', () => {
    state.isSystemAdmin = false
    renderWithProviders(<SiteBackupPanel />)
    expect(screen.queryByRole('button', { name: '下载加密全站备份' })).not.toBeInTheDocument()
    expect(screen.getByText('全站备份与恢复仅系统管理员可操作。')).toBeInTheDocument()
  })

  it('keeps submit clickable and explains missing passwords without downloading', async () => {
    const confirm = openBackup()
    expect(confirm).toBeEnabled()
    await userEvent.click(confirm)
    expect(screen.getByLabelText('当前管理员密码')).toHaveAccessibleDescription('请输入当前管理员密码。')
    expect(screen.getByLabelText('当前管理员密码')).toHaveFocus()
    expect(screen.getByLabelText('备份密码', { exact: true })).toHaveAccessibleDescription('请输入备份密码。')
    expect(screen.getByLabelText('再次输入备份密码')).toHaveAccessibleDescription('请再次输入备份密码。')
    expect(downloadFullBackup).not.toHaveBeenCalled()
  })

  it('explains a short password and accepts the 12-character boundary after correction', async () => {
    vi.mocked(downloadFullBackup).mockResolvedValue(undefined)
    const confirm = openBackup()
    fillBackupPasswords('12345678901')
    expect(confirm).toBeEnabled()
    await userEvent.click(confirm)
    const password = screen.getByLabelText('备份密码', { exact: true })
    expect(password).toHaveAttribute('aria-invalid', 'true')
    expect(password).toHaveAccessibleDescription('备份密码至少需要 12 位，当前为 11 位。')
    expect(downloadFullBackup).not.toHaveBeenCalled()
    fillBackupPasswords('123456789012')
    expect(password).toHaveAttribute('aria-invalid', 'false')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    await userEvent.click(confirm)
    await waitFor(() => expect(downloadFullBackup).toHaveBeenCalledWith('admin password', '123456789012'))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('explains mismatched passwords and downloads only after they match', async () => {
    vi.mocked(downloadFullBackup).mockRejectedValue(new Error('test request stopped'))
    const confirm = openBackup()
    fillBackupPasswords('backup-password-2026', 'different')
    await userEvent.click(confirm)
    expect(screen.getByLabelText('再次输入备份密码')).toHaveAccessibleDescription('两次输入的备份密码不一致，请重新输入。')
    expect(downloadFullBackup).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('再次输入备份密码'), { target: { value: 'backup-password-2026' } })
    await userEvent.click(confirm)
    await waitFor(() => expect(downloadFullBackup).toHaveBeenCalledWith('admin password', 'backup-password-2026'))
    await waitFor(() => expect(screen.getByLabelText('当前管理员密码')).toHaveValue(''))
  })

  it('reads password-manager values even when no React change event was emitted', async () => {
    vi.mocked(downloadFullBackup).mockResolvedValue(undefined)
    const confirm = openBackup()
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setValue.call(screen.getByLabelText('当前管理员密码'), 'autofilled-admin')
    setValue.call(screen.getByLabelText('备份密码', { exact: true }), 'autofilled-backup')
    setValue.call(screen.getByLabelText('再次输入备份密码'), 'autofilled-backup')
    await userEvent.click(confirm)
    await waitFor(() => expect(downloadFullBackup).toHaveBeenCalledWith('autofilled-admin', 'autofilled-backup'))
  })

  it('prevents repeated submissions while the backup request is pending', async () => {
    let finish!: () => void
    vi.mocked(downloadFullBackup).mockReturnValue(new Promise<void>((resolve) => { finish = resolve }))
    const confirm = openBackup()
    fillBackupPasswords('backup-password-2026')
    await userEvent.click(confirm)
    expect(screen.getByRole('button', { name: '处理中…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled()
    fireEvent.submit(confirm.closest('form')!)
    expect(downloadFullBackup).toHaveBeenCalledTimes(1)
    finish()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('clears passwords and validation errors when the dialog is cancelled', async () => {
    const confirm = openBackup()
    fillBackupPasswords('short', 'different')
    await userEvent.click(confirm)
    expect(screen.getAllByRole('alert')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    fireEvent.click(screen.getByRole('button', { name: '下载加密全站备份' }))
    expect(screen.getByLabelText('当前管理员密码')).toHaveValue('')
    expect(screen.getByLabelText('备份密码', { exact: true })).toHaveValue('')
    expect(screen.getByLabelText('再次输入备份密码')).toHaveValue('')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('explains missing recovery files and credentials without submitting', async () => {
    renderWithProviders(<SiteBackupPanel />)
    fireEvent.click(screen.getByRole('button', { name: '校验全站备份并暂存恢复' }))
    await userEvent.click(screen.getByRole('button', { name: '验证并暂存' }))
    expect(screen.getByLabelText('全站备份 ZIP')).toHaveAccessibleDescription('请选择全站备份 ZIP 文件。')
    expect(screen.getByLabelText('当前管理员密码')).toHaveAccessibleDescription('请输入当前管理员密码。')
    expect(screen.getByLabelText('备份密码')).toHaveAccessibleDescription('请输入备份密码。')
    expect(restoreFullBackup).not.toHaveBeenCalled()
  })

  it('reports validated staging without claiming that the site has been restored', async () => {
    vi.mocked(restoreFullBackup).mockResolvedValue({ job_id: 'job', source_workspace_id: null, tables: 10, records: 42, files: 3, memory_files: 2, mode: 'staged', restore_path: '/data/site-restore/job.zip', restart_required: true })
    renderWithProviders(<SiteBackupPanel />)
    fireEvent.click(screen.getByRole('button', { name: '校验全站备份并暂存恢复' }))
    const file = new File(['archive'], 'site.zip', { type: 'application/zip' })
    await userEvent.upload(screen.getByLabelText('全站备份 ZIP'), file)
    fireEvent.change(screen.getByLabelText('当前管理员密码'), { target: { value: 'current-admin' } })
    // Existing archives may have passwords shorter than the new-backup minimum.
    fireEvent.change(screen.getByLabelText('备份密码'), { target: { value: 'legacy' } })
    await userEvent.click(screen.getByRole('button', { name: '验证并暂存' }))
    await waitFor(() => expect(restoreFullBackup).toHaveBeenCalledWith(file, 'current-admin', 'legacy'))
    expect(await screen.findByRole('status')).toHaveTextContent('当前数据尚未覆盖')
    expect(screen.getByRole('status')).toHaveTextContent('python -m app.services.site_restore')
  })
})
