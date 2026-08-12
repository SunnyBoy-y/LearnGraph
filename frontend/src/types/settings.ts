import type { IsoDateTime } from './common'

export interface WebFetchPolicy {
  allow_without_confirmation: boolean
  allowed_domains: string[]
}

export interface ResearchPolicy {
  allowed_domains: string[]
}

/** 统一白名单（搜索 / 网页抓取 / 出站 Egress 共用一层）。 */
export interface AccessAllowlist {
  /** 白名单内域名：搜索、抓取、沙箱出站均不拦截。 */
  allowed_domains: string[]
  /** 不拦截全放行：公网域名全部放行（内网/元数据仍被拒绝）。 */
  allow_all: boolean
}

export interface SettingUpdateRequest {
  value: unknown
}

export interface WorkspaceSetting {
  key: string
  value: unknown
  updated_at: IsoDateTime
}

