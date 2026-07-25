import type { ApiErrorEnvelope, ValidationIssue } from '@/types/common'

import { authStore } from './auth-store'
import { parseSseResponse } from './sse'
import type { SseEvent, SseParseOptions } from './sse'

export type QueryValue = string | number | boolean | null | undefined
export type QueryParams = Record<string, QueryValue | QueryValue[]>

export interface ApiRequestOptions {
  signal?: AbortSignal
  headers?: HeadersInit
  query?: QueryParams
  auth?: boolean
  workspace?: boolean
}

export interface ApiStreamOptions extends ApiRequestOptions, SseParseOptions {}

export interface ApiClientConfig {
  baseUrl?: string
  fetch?: typeof fetch
  accessToken?: () => string | null | undefined
  workspaceId?: () => string | null | undefined
}

interface RequestBody {
  value: unknown
  kind: 'json' | 'raw'
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown
  readonly method: string
  readonly url: string
  readonly responseBody: unknown

  constructor(input: {
    status: number
    code: string
    message: string
    details?: unknown
    method: string
    url: string
    responseBody?: unknown
    cause?: unknown
  }) {
    super(input.message, input.cause === undefined ? undefined : { cause: input.cause })
    this.name = 'ApiError'
    this.status = input.status
    this.code = input.code
    this.details = input.details
    this.method = input.method
    this.url = input.url
    this.responseBody = input.responseBody
  }
}

export function resolveApiBaseUrl(value?: string): string {
  const configured = value?.trim()
  if (!configured) return '/api/v1'
  const normalized = configured === '/' ? '' : configured.replace(/\/+$/, '')
  if (!normalized || normalized.endsWith('/api/v1')) return normalized || '/api/v1'
  return `${normalized}/api/v1`
}

function appendQuery(url: string, query?: QueryParams): string {
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    const values = Array.isArray(value) ? value : [value]
    for (const item of values) {
      if (item !== null && item !== undefined) params.append(key, String(item))
    }
  }
  const serialized = params.toString()
  if (!serialized) return url
  return `${url}${url.includes('?') ? '&' : '?'}${serialized}`
}

function joinUrl(baseUrl: string, path: string): string {
  if (/^https?:\/\//i.test(path)) return path
  const normalizedPath = path.startsWith('/') ? path : `/${path}`
  return `${baseUrl}${normalizedPath}` || '/'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

async function readResponseBody(response: Response): Promise<unknown> {
  if (response.status === 204 || response.status === 205) return undefined
  const text = await response.text()
  if (!text) return undefined

  try {
    return JSON.parse(text) as unknown
  } catch {
    return text
  }
}

function appError(body: unknown): ApiErrorEnvelope['error'] | null {
  if (!isRecord(body) || !isRecord(body.error)) return null
  if (typeof body.error.code !== 'string' || typeof body.error.message !== 'string') return null
  return {
    code: body.error.code,
    message: body.error.message,
    details: body.error.details,
  }
}

function validationIssues(body: unknown): ValidationIssue[] | null {
  if (!isRecord(body) || !Array.isArray(body.detail)) return null
  const issues = body.detail.filter(
    (item): item is ValidationIssue => isRecord(item) && typeof item.msg === 'string',
  )
  return issues.length > 0 ? issues : null
}

function isAbort(error: unknown, signal?: AbortSignal): boolean {
  return signal?.aborted === true || (error instanceof DOMException && error.name === 'AbortError')
}

export class ApiClient {
  readonly baseUrl: string
  private readonly fetcher: typeof fetch
  private readonly accessToken: () => string | null | undefined
  private readonly workspaceId: () => string | null | undefined

  constructor(config: ApiClientConfig = {}) {
    this.baseUrl = resolveApiBaseUrl(config.baseUrl ?? import.meta.env.VITE_API_BASE_URL)
    this.fetcher = config.fetch ?? globalThis.fetch.bind(globalThis)
    this.accessToken = config.accessToken ?? (() => authStore.getAccessToken())
    this.workspaceId = config.workspaceId ?? (() => authStore.getWorkspaceId())
  }

  private async fetchResponse(
    method: string,
    path: string,
    options: ApiRequestOptions,
    body?: RequestBody,
  ): Promise<Response> {
    const url = appendQuery(joinUrl(this.baseUrl, path), options.query)
    const headers = new Headers(options.headers)
    if (options.auth !== false && !headers.has('Authorization')) {
      const token = this.accessToken()
      if (token) headers.set('Authorization', `Bearer ${token}`)
    }
    if (options.workspace !== false && !headers.has('X-Workspace-ID')) {
      const workspaceId = this.workspaceId()
      if (workspaceId) headers.set('X-Workspace-ID', workspaceId)
    }
    if (!headers.has('X-Device-ID')) {
      headers.set('X-Device-ID', authStore.getDeviceId())
    }

    let requestBody: BodyInit | undefined
    if (body?.kind === 'json' && body.value !== undefined) {
      if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
      requestBody = JSON.stringify(body.value)
    } else if (body?.kind === 'raw') {
      requestBody = body.value as BodyInit
    }

    try {
      const response = await this.fetcher(url, {
        method,
        headers,
        body: requestBody,
        signal: options.signal,
      })
      if (response.ok) return response

      const responseBody = await readResponseBody(response)
      const known = appError(responseBody)
      const issues = validationIssues(responseBody)
      if (response.status === 401 && options.auth !== false) {
        authStore.clear()
        if (
          typeof window !== 'undefined' &&
          !window.location.pathname.startsWith('/auth/login')
        ) {
          window.location.replace('/auth/login')
        }
      }
      throw new ApiError({
        status: response.status,
        code: known?.code ?? (issues ? 'validation_error' : 'http_error'),
        message:
          known?.message ??
          issues?.[0]?.msg ??
          (typeof responseBody === 'string' ? responseBody : response.statusText || `HTTP ${response.status}`),
        details: known?.details ?? issues,
        method,
        url,
        responseBody,
      })
    } catch (error) {
      if (error instanceof ApiError || isAbort(error, options.signal)) throw error
      throw new ApiError({
        status: 0,
        code: 'network_error',
        message: error instanceof Error ? error.message : 'Network request failed',
        details: null,
        method,
        url,
        cause: error,
      })
    }
  }

  private async request<TResponse>(
    method: string,
    path: string,
    options: ApiRequestOptions,
    body?: RequestBody,
  ): Promise<TResponse> {
    const response = await this.fetchResponse(method, path, options, body)
    return (await readResponseBody(response)) as TResponse
  }

  get<TResponse>(path: string, options: ApiRequestOptions = {}): Promise<TResponse> {
    return this.request<TResponse>('GET', path, options)
  }

  async getBlob(path: string, options: ApiRequestOptions = {}): Promise<Blob> {
    const response = await this.fetchResponse('GET', path, options)
    return response.blob()
  }

  post<TResponse, TBody = unknown>(
    path: string,
    body?: TBody,
    options: ApiRequestOptions = {},
  ): Promise<TResponse> {
    return this.request<TResponse>('POST', path, options, { value: body, kind: 'json' })
  }

  put<TResponse, TBody = unknown>(
    path: string,
    body?: TBody,
    options: ApiRequestOptions = {},
  ): Promise<TResponse> {
    return this.request<TResponse>('PUT', path, options, { value: body, kind: 'json' })
  }

  patch<TResponse, TBody = unknown>(
    path: string,
    body?: TBody,
    options: ApiRequestOptions = {},
  ): Promise<TResponse> {
    return this.request<TResponse>('PATCH', path, options, { value: body, kind: 'json' })
  }

  delete<TResponse>(path: string, options: ApiRequestOptions = {}): Promise<TResponse> {
    return this.request<TResponse>('DELETE', path, options)
  }

  upload<TResponse>(path: string, formData: FormData, options: ApiRequestOptions = {}): Promise<TResponse> {
    return this.request<TResponse>('POST', path, options, { value: formData, kind: 'raw' })
  }

  async *postSse<TData = unknown, TBody = unknown>(
    path: string,
    body: TBody,
    options: ApiStreamOptions = {},
  ): AsyncGenerator<SseEvent<TData>> {
    const headers = new Headers(options.headers)
    if (!headers.has('Accept')) headers.set('Accept', 'text/event-stream')
    const response = await this.fetchResponse('POST', path, { ...options, headers }, { value: body, kind: 'json' })
    yield* parseSseResponse<TData>(response, options)
  }
}

export const apiClient = new ApiClient()
