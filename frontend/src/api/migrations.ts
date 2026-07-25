import type {
  BackupRestoreResult,
  DatabaseConfiguration,
  DatabaseConfigurationInput,
  MigrationAdapterStatus,
  MigrationDatabaseKind,
  MigrationJob,
  MigrationPreflightRequest,
} from '@/types/migrations'

import { apiClient } from './client'

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

export function downloadFullBackup(): Promise<Blob> {
  return apiClient.getBlob('/migrations/backup')
}

export function restoreFullBackup(file: File): Promise<BackupRestoreResult> {
  const form = new FormData()
  form.append('backup', file, file.name)
  form.append('confirm', 'true')
  return apiClient.upload<BackupRestoreResult>('/migrations/restore', form)
}
