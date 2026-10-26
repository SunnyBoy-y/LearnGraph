import type {
  BackupRestoreResult,
  DatabaseConfiguration,
  DatabaseConfigurationInput,
  MigrationAdapterStatus,
  MigrationDatabaseKind,
  MigrationJob,
  MigrationPreflightRequest,
  PrimaryDatabaseMigration,
  PrimaryDatabasePreflight,
} from '@/types/migrations'

import { apiClient } from './client'
import { saveBackupDownload, startPreparedBackupDownload } from '@/lib/backup-download'

export function listMigrations(): Promise<MigrationJob[]> {
  return apiClient.get<MigrationJob[]>('/migrations')
}

export function listMigrationAdapters(): Promise<MigrationAdapterStatus[]> {
  return apiClient.get<MigrationAdapterStatus[]>('/migrations/adapters')
}

export function listDatabaseConfigurations(): Promise<DatabaseConfiguration[]> {
  return apiClient.get<DatabaseConfiguration[]>('/migrations/database-configurations')
}

export function saveDatabaseConfiguration(
  providerKind: MigrationDatabaseKind,
  payload: DatabaseConfigurationInput,
): Promise<DatabaseConfiguration> {
  return apiClient.put<DatabaseConfiguration, DatabaseConfigurationInput>(
    `/migrations/database-configurations/${encodeURIComponent(providerKind)}`,
    payload,
  )
}

export function getMigration(jobId: string): Promise<MigrationJob> {
  return apiClient.get<MigrationJob>(`/migrations/${encodeURIComponent(jobId)}`)
}

export function preflightMigration(payload: MigrationPreflightRequest): Promise<MigrationJob> {
  return apiClient.post<MigrationJob, MigrationPreflightRequest>('/migrations/preflight', payload)
}

export function startMigration(jobId: string): Promise<MigrationJob> {
  return apiClient.post<MigrationJob>(`/migrations/${encodeURIComponent(jobId)}/start`)
}

export function commitMigration(jobId: string): Promise<MigrationJob> {
  return apiClient.post<MigrationJob, { confirm: true }>(
    `/migrations/${encodeURIComponent(jobId)}/commit`,
    { confirm: true },
  )
}

export function rollbackMigration(jobId: string): Promise<MigrationJob> {
  return apiClient.post<MigrationJob, { confirm: true }>(
    `/migrations/${encodeURIComponent(jobId)}/rollback`,
    { confirm: true },
  )
}

export function downloadFullBackup(currentPassword: string, backupPassword: string): Promise<void> {
  const payload = { current_password: currentPassword, backup_password: backupPassword }
  const name = `learngraph-site-backup-${new Date().toISOString().slice(0, 10)}.zip`
  return saveBackupDownload(() => apiClient.postDownload('/migrations/backup', payload), name, async () => {
    const prepared = await apiClient.post<{ ticket_id: string }>('/migrations/backup/browser-grants', payload)
    if (!/^[0-9a-f]{32}$/.test(prepared.ticket_id)) throw new Error('下载授权格式无效')
    startPreparedBackupDownload(`${apiClient.baseUrl}/migrations/backup/browser/${prepared.ticket_id}`, name)
  })
}

export function restoreFullBackup(file: File, currentPassword: string, backupPassword: string): Promise<BackupRestoreResult> {
  const form = new FormData()
  form.append('backup', file, file.name)
  form.append('confirm', 'true')
  form.append('current_password', currentPassword)
  form.append('backup_password', backupPassword)
  return apiClient.upload<BackupRestoreResult>('/migrations/restore', form)
}

export const getPrimaryDatabaseMigration = () => apiClient.get<PrimaryDatabaseMigration>('/migrations/database/status')
export const preflightPrimaryDatabaseMigration = () => apiClient.post<PrimaryDatabasePreflight>('/migrations/database/preflight')
export const startPrimaryDatabaseMigration = (payload: { confirm: true; current_password: string; managed: boolean; auto_activate: boolean }) =>
  apiClient.post<PrimaryDatabaseMigration, typeof payload>('/migrations/database/start', payload)
export const activatePrimaryDatabaseMigration = (current_password: string) =>
  apiClient.post<PrimaryDatabaseMigration, { confirm: true; current_password: string }>('/migrations/database/activate', { confirm: true, current_password })
export const cancelPrimaryDatabaseMigration = () =>
  apiClient.post<PrimaryDatabaseMigration, { confirm: true }>('/migrations/database/cancel', { confirm: true })
export const resumePrimaryDatabaseMigration = () =>
  apiClient.post<PrimaryDatabaseMigration, { confirm: true }>('/migrations/database/resume', { confirm: true })
