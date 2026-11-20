import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@/test/render'
import { PrimaryDatabaseMigrationPanel } from './primary-database-migration-panel'
import { activatePrimaryDatabaseMigration, getPrimaryDatabaseMigration, preflightPrimaryDatabaseMigration, startPrimaryDatabaseMigration } from '@/api/migrations'

const auth = vi.hoisted(() => ({ isSystemAdmin: true, userId: 'admin' }))
vi.mock('@/features/auth/auth-context-value', () => ({ useAuth: () => auth }))
vi.mock('./database-configuration-sheet', () => ({ DatabaseConfigurationSheet: () => null }))
vi.mock('@/api/migrations', () => ({ getPrimaryDatabaseMigration: vi.fn(), preflightPrimaryDatabaseMigration: vi.fn(), startPrimaryDatabaseMigration: vi.fn(), activatePrimaryDatabaseMigration: vi.fn(), cancelPrimaryDatabaseMigration: vi.fn(), resumePrimaryDatabaseMigration: vi.fn() }))

beforeEach(() => {
  vi.resetAllMocks()
  auth.isSystemAdmin = true
  vi.mocked(getPrimaryDatabaseMigration).mockResolvedValue({ active_database: 'sqlite', irreversible: false, running: false, agent_configured: true })
})

describe('primary database migration', () => {
  it('shows automatic agent startup and real lifecycle state', async () => {
    vi.mocked(getPrimaryDatabaseMigration).mockResolvedValue({
      id: 'job', state: 'STARTING_AGENT', active_database: 'sqlite', irreversible: false,
      running: true, agent_configured: true, agent_auto_start: true, agent_state: 'starting',
    })
    renderWithProviders(<PrimaryDatabaseMigrationPanel />)
    await screen.findByText('启动数据库迁移服务')
    expect(screen.getByText(/迁移服务将在需要时自动启动/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '强制备份并开始迁移' })).not.toBeInTheDocument()
  })

  it('distinguishes agent shutdown from the running database', async () => {
    vi.mocked(getPrimaryDatabaseMigration).mockResolvedValue({
      id: 'job', state: 'COPYING', active_database: 'sqlite', irreversible: false,
      running: true, agent_configured: true, agent_auto_start: true, agent_state: 'stopped',
    })
    renderWithProviders(<PrimaryDatabaseMigrationPanel />)
    await screen.findByText('临时迁移服务已停止，不影响 PostgreSQL 运行。')
  })

  it('hides the feature and makes no database request for a workspace owner', async () => {
    auth.isSystemAdmin = false
    renderWithProviders(<PrimaryDatabaseMigrationPanel />)
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(screen.queryByText('主业务数据库')).not.toBeInTheDocument()
    expect(getPrimaryDatabaseMigration).not.toHaveBeenCalled()
  })

  it('requires preflight, irreversible acknowledgement and admin password', async () => {
    vi.mocked(preflightPrimaryDatabaseMigration).mockResolvedValue({ ready: true, tables: 188, checks: [{ key: 'source_integrity', passed: true }] })
    vi.mocked(startPrimaryDatabaseMigration).mockResolvedValue({ id: 'job', state: 'BACKING_UP', active_database: 'sqlite', irreversible: false, running: true, agent_configured: true })
    renderWithProviders(<PrimaryDatabaseMigrationPanel />)
    const start = await screen.findByRole('button', { name: '强制备份并开始迁移' })
    expect(start).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '执行迁移预检' }))
    await screen.findByText('SQLite 完整性与外键：通过')
    expect(start).toBeDisabled()
    fireEvent.click(screen.getByLabelText('我确认仅迁移主业务库，成功后永久使用 PostgreSQL，不支持迁回 SQLite。'))
    fireEvent.change(screen.getByLabelText('当前管理员密码'), { target: { value: 'isolated-admin-password' } })
    await waitFor(() => expect(start).toBeEnabled())
    fireEvent.click(start)
    await waitFor(() => expect(startPrimaryDatabaseMigration).toHaveBeenCalledWith({ confirm: true, current_password: 'isolated-admin-password', managed: true, auto_activate: true }))
  })

  it('shows the blocked index names and keeps migration blocked until a successful recheck', async () => {
    vi.mocked(preflightPrimaryDatabaseMigration)
      .mockResolvedValueOnce({ ready: false, tables: 196, checks: [{ key: 'source_indexes', passed: false, unmapped_indexes: ['items.custom_unique_value'] }] })
      .mockResolvedValueOnce({ ready: true, tables: 196, checks: [{ key: 'source_indexes', passed: true, unmapped_indexes: [] }] })
    renderWithProviders(<PrimaryDatabaseMigrationPanel />)
    const start = await screen.findByRole('button', { name: '强制备份并开始迁移' })
    await waitFor(() => expect(start).toHaveAccessibleDescription('请先执行迁移预检。'))
    fireEvent.click(screen.getByRole('button', { name: '执行迁移预检' }))
    await screen.findByText('索引与唯一约束均可保留：未通过')
    expect(screen.getByText('无法保留的索引：items.custom_unique_value')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('我确认仅迁移主业务库，成功后永久使用 PostgreSQL，不支持迁回 SQLite。'))
    fireEvent.change(screen.getByLabelText('当前管理员密码'), { target: { value: 'admin-password' } })
    expect(start).toBeDisabled()
    expect(start).toHaveAccessibleDescription('预检未通过，请先处理上方未通过的项目，再重新执行迁移预检。')
    fireEvent.submit(start.closest('form')!)
    expect(startPrimaryDatabaseMigration).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '执行迁移预检' }))
    await screen.findByText('索引与唯一约束均可保留：通过')
    expect(start).toBeEnabled()
    expect(screen.queryByText('无法保留的索引：items.custom_unique_value')).not.toBeInTheDocument()
  })

  it('shows missing-password feedback instead of silently disabling an otherwise ready form', async () => {
    vi.mocked(preflightPrimaryDatabaseMigration).mockResolvedValue({ ready: true, tables: 196, checks: [] })
    renderWithProviders(<PrimaryDatabaseMigrationPanel />)
    const start = await screen.findByRole('button', { name: '强制备份并开始迁移' })
    fireEvent.click(screen.getByRole('button', { name: '执行迁移预检' }))
    await waitFor(() => expect(preflightPrimaryDatabaseMigration).toHaveBeenCalledOnce())
    fireEvent.click(screen.getByLabelText('我确认仅迁移主业务库，成功后永久使用 PostgreSQL，不支持迁回 SQLite。'))
    await waitFor(() => expect(start).toBeEnabled())
    await userEvent.click(start)
    expect(screen.getByLabelText('当前管理员密码')).toHaveAccessibleDescription('请输入当前管理员密码。')
    expect(screen.getByLabelText('当前管理员密码')).toHaveFocus()
    expect(startPrimaryDatabaseMigration).not.toHaveBeenCalled()
  })

  it('submits an autofilled administrator password even without a React change event', async () => {
    vi.mocked(preflightPrimaryDatabaseMigration).mockResolvedValue({ ready: true, tables: 196, checks: [] })
    vi.mocked(startPrimaryDatabaseMigration).mockResolvedValue({ id: 'job', state: 'BACKING_UP', active_database: 'sqlite', irreversible: false, running: true, agent_configured: true })
    renderWithProviders(<PrimaryDatabaseMigrationPanel />)
    const start = await screen.findByRole('button', { name: '强制备份并开始迁移' })
    fireEvent.click(screen.getByRole('button', { name: '执行迁移预检' }))
    fireEvent.click(screen.getByLabelText('我确认仅迁移主业务库，成功后永久使用 PostgreSQL，不支持迁回 SQLite。'))
    await waitFor(() => expect(start).toBeEnabled())
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(screen.getByLabelText('当前管理员密码'), 'autofilled-admin-password')
    await userEvent.click(start)
    await waitFor(() => expect(startPrimaryDatabaseMigration).toHaveBeenCalledWith({ confirm: true, current_password: 'autofilled-admin-password', managed: true, auto_activate: true }))
  })

  it('invalidates a previous passing preflight while rechecking and when the request fails', async () => {
    let reject!: (error: Error) => void
    vi.mocked(preflightPrimaryDatabaseMigration)
      .mockResolvedValueOnce({ ready: true, tables: 196, checks: [] })
      .mockReturnValueOnce(new Promise((_resolve, onReject) => { reject = onReject }))
    renderWithProviders(<PrimaryDatabaseMigrationPanel />)
    const start = await screen.findByRole('button', { name: '强制备份并开始迁移' })
    fireEvent.click(screen.getByRole('button', { name: '执行迁移预检' }))
    fireEvent.click(screen.getByLabelText('我确认仅迁移主业务库，成功后永久使用 PostgreSQL，不支持迁回 SQLite。'))
    await waitFor(() => expect(start).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '执行迁移预检' }))
    await waitFor(() => expect(start).toBeDisabled())
    expect(start).toHaveAccessibleDescription('迁移预检正在执行，请稍候。')
    reject(new Error('network unavailable'))
    await waitFor(() => expect(start).toHaveAccessibleDescription('迁移预检失败：network unavailable。请重新执行预检。'))
    expect(start).toBeDisabled()
    expect(startPrimaryDatabaseMigration).not.toHaveBeenCalled()
  })

  it('accepts autofill when manually activating a fully verified migration', async () => {
    vi.mocked(getPrimaryDatabaseMigration).mockResolvedValue({ id: 'job', state: 'READY_TO_ACTIVATE', active_database: 'sqlite', irreversible: false, running: true, agent_configured: true, all_checks_passed: true })
    vi.mocked(activatePrimaryDatabaseMigration).mockResolvedValue({ id: 'job', state: 'ACTIVATING', active_database: 'sqlite', irreversible: false, running: true, agent_configured: true })
    renderWithProviders(<PrimaryDatabaseMigrationPanel />)
    const activate = await screen.findByRole('button', { name: '确认切换至 PostgreSQL' })
    fireEvent.click(screen.getByLabelText('我确认仅迁移主业务库，成功后永久使用 PostgreSQL，不支持迁回 SQLite。'))
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(screen.getByLabelText('当前管理员密码'), 'autofilled-activation-password')
    await userEvent.click(activate)
    await waitFor(() => expect(activatePrimaryDatabaseMigration).toHaveBeenCalledWith('autofilled-activation-password'))
  })

  it('blocks start after a failed check and shows actual progress without a rollback button', async () => {
    vi.mocked(getPrimaryDatabaseMigration).mockResolvedValue({ id: 'job', state: 'COPYING', active_database: 'sqlite', irreversible: false, running: true, agent_configured: true, tables_copied: 27, tables_total: 188, backup_verified: true, rows_copied: 1254 })
    renderWithProviders(<PrimaryDatabaseMigrationPanel />)
    await screen.findByText('27 / 188')
    expect(screen.getByRole('progressbar', { name: '全量复制表数' })).toHaveAttribute('aria-valuenow', '27')
    expect(screen.queryByRole('button', { name: /回滚|迁回/ })).not.toBeInTheDocument()
  })

  it('removes all migration controls after PostgreSQL activation', async () => {
    vi.mocked(getPrimaryDatabaseMigration).mockResolvedValue({ state: 'COMPLETED', active_database: 'postgresql', irreversible: true, running: false, agent_configured: true })
    renderWithProviders(<PrimaryDatabaseMigrationPanel />)
    await screen.findByText('当前主库：PostgreSQL')
    expect(screen.getByText(/SQLite 已永久封存/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '强制备份并开始迁移' })).not.toBeInTheDocument()
  })
})
