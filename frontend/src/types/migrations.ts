import type { IsoDateTime, UnknownRecord } from './common'

export type MigrationResourceKind = 'database' | 'object_storage'

export interface MigrationPreflightRequest {
  source_kind: string
  target_kind: string
  resource_kind?: MigrationResourceKind
  target_name?: string
}

export interface MigrationAdapterStatus {
  provider_kind: string
  capability: string
  status: string
  configured: boolean
  driver_available: boolean
  connection_verified: boolean
  details: UnknownRecord
}

export type MigrationDatabaseKind = 'postgresql' | 'mysql'
export type DatabaseSslMode = 'disable' | 'prefer' | 'require'

export interface DatabaseConfigurationInput {
  host: string
  port: number
  database_name: string
  username: string
  password?: string
  ssl_mode: DatabaseSslMode
}

export interface DatabaseConfiguration {
  provider_kind: MigrationDatabaseKind
  host: string
  port: number
  database_name: string
  username: string
  ssl_mode: DatabaseSslMode
  password_configured: boolean
  status: string
  driver_available: boolean
  connection_verified: boolean
  last_error_code: string | null
  last_verified_at: IsoDateTime | null
  updated_at: IsoDateTime
}

export interface MigrationCheckpoint {
  sequence: number
  state: string
  status: string
  metrics: UnknownRecord
  error_code: string | null
  error_message: string | null
  started_at: IsoDateTime
  finished_at: IsoDateTime | null
}

export interface MigrationJob {
  id: string
  workspace_id: string
  source_kind: string
  target_kind: string
  status: string
  report: UnknownRecord
  resource_kind: MigrationResourceKind
  can_rollback: boolean
  reverse_migration_required: boolean
  maintenance_active: boolean
  checkpoints: MigrationCheckpoint[]
  created_at: IsoDateTime
  updated_at: IsoDateTime
}

export interface BackupRestoreResult {
  job_id: string
  source_workspace_id: string | null
  tables: number
  records: number
  files: number
  memory_files: number
  mode: string
}
