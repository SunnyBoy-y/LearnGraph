import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import { renderWithProviders } from '@/test/render'
import { PrimaryDatabaseMigrationPanel } from './primary-database-migration-panel'
import { getPrimaryDatabaseMigration, preflightPrimaryDatabaseMigration, startPrimaryDatabaseMigration } from '@/api/migrations'

const auth = vi.hoisted(() => ({ isSystemAdmin: true, userId: 'admin' }))
vi.mock('@/features/auth/auth-context-value', () => ({ useAuth: () => auth }))
vi.mock('./database-configuration-sheet', () => ({ DatabaseConfigurationSheet: () => null }))
vi.mock('@/api/migrations', () => ({ getPrimaryDatabaseMigration: vi.fn(), preflightPrimaryDatabaseMigration: vi.fn(), startPrimaryDatabaseMigration: vi.fn(), activatePrimaryDatabaseMigration: vi.fn(), cancelPrimaryDatabaseMigration: vi.fn(), resumePrimaryDatabaseMigration: vi.fn() }))

beforeEach(() => {
  vi.clearAllMocks()
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
