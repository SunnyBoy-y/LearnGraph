import type { IsoDateTime } from './common'

export interface WebFetchPolicy {
  allow_without_confirmation: boolean
  allowed_domains: string[]
}

export interface ResearchPolicy {
  allowed_domains: string[]
}
export interface SettingUpdateRequest {
  value: unknown
}

export interface WorkspaceSetting {
  key: string
  value: unknown
  updated_at: IsoDateTime
}

