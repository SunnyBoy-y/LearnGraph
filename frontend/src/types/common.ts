export type IsoDateTime = string

export type UnknownRecord = Record<string, unknown>

export interface ActionResponse {
  status: string
  message: string
  resource_id: string | null
  details: UnknownRecord
}

export interface ApiErrorEnvelope {
  error: {
    code: string
    message: string
    details?: unknown
  }
}

export interface ValidationIssue {
  type?: string
  loc?: Array<string | number>
  msg: string
  input?: unknown
  ctx?: UnknownRecord
}

