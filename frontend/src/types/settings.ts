import type { IsoDateTime } from './common'

export interface SettingUpdateRequest {
  value: unknown
}

export interface WorkspaceSetting {
  key: string
  value: unknown
  updated_at: IsoDateTime
}

