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

/** 沙箱出站联网开关（工作区级，仅公网，默认关闭）。 */
export interface SandboxEgress {
  /** true = 沙箱可经代理访问所有公网域名（内网/本机/云元数据仍被拒绝）。 */
  allow_public_network: boolean
}

export interface SettingUpdateRequest {
  value: unknown
}

export interface WorkspaceSetting {
  key: string
  value: unknown
  updated_at: IsoDateTime
}

