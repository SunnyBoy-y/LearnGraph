import type { IsoDateTime, UnknownRecord } from './common'

export interface AuditEvent {
  id: string
  workspace_id: string
  actor_id: string
  action: string
  resource_type: string
  resource_id: string
  outcome: string
  trace_id: string
  details: UnknownRecord
  created_at: IsoDateTime
}

export interface AuditQuery {
  action?: string
}

