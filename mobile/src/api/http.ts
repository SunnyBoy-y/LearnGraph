/**
 * 移动端 HTTP client —— 参照 frontend/src/api/client.ts 的语义裁剪：
 * 可插拔 baseUrl/token/workspace/deviceId（运行时随连接配置与登录态切换），
 * 401 触发 onUnauthorized 回调；POST SSE 复用同一解析器。
 */

import { parseSseResponse } from './sse'
import type { SseEvent, SseParseOptions } from './sse'

export type QueryValue = string | number | boolean | null | undefined
export type QueryParams = Record<string, QueryValue | QueryValue[]>

export interface ApiRequestOptions {
  signal?: AbortSignal
  headers?: HeadersInit
  query?: QueryParams
  /** 默认 true；登录等匿名接口传 false */
  auth?: boolean
  /** 默认 true；登录/会话管理接口传 false */
  workspace?: boolean
  /** 请求超时（毫秒），弱网时自动放大 1.8 倍；传 0 关闭 */
  timeoutMs?: number
  /** 禁用自动重试 */
  noRetry?: boolean
}

export interface ApiStreamOptions extends ApiRequestOptions, SseParseOptions {}

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

/** 运行时可变上下文：连接切换 / 登录登出时更新 */
export const apiContext = {
  baseUrl: '/api/v1',
  getToken: (): string | null => null,
  getWorkspaceId: (): string | null => null,
  getDeviceId: (): string => 'browser-dev',
  onUnauthorized: (): void => {},
}

export function configureApi(config: Partial<typeof apiContext>): void {
  Object.assign(apiContext, config)
}

function resolveApiBaseUrl(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) return '/api/v1'
  const normalized = trimmed === '/' ? '' : trimmed.replace(/\/+$/, '')
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

function appError(body: unknown): { code: string; message: string; details: unknown } | null {
  if (!isRecord(body) || !isRecord(body.error)) return null
  if (typeof body.error.code !== 'string' || typeof body.error.message !== 'string') return null
  return {
    code: body.error.code,
    message: body.error.message,
    details: body.error.details,
  }
}

interface RequestBody {
  value: unknown
  kind: 'json' | 'raw'
}

const RETRYABLE_METHODS = new Set(['GET', 'HEAD'])
const RETRYABLE_STATUSES = new Set([502, 503, 504])
const MAX_RETRY_ATTEMPTS = 3
const BASE_RETRY_DELAY_MS = 500
const DEFAULT_REQUEST_TIMEOUT_MS = 30_000
const DEFAULT_SSE_CONNECT_TIMEOUT_MS = 45_000

let mobileEwmaRttMs: number | null = null
let mobileConsecutiveFailures = 0

export function isMobileWeakNetwork(): boolean {
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return true
  return (mobileEwmaRttMs !== null && mobileEwmaRttMs >= 1_500) || mobileConsecutiveFailures >= 2
}

function recordMobileSample(ok: boolean, durationMs: number): void {
  if (ok) {
    mobileConsecutiveFailures = 0
    const clamped = Math.min(Math.max(0, durationMs), 30_000)
    mobileEwmaRttMs =
      mobileEwmaRttMs === null ? clamped : Math.round(0.3 * clamped + 0.7 * mobileEwmaRttMs)
  } else {
    mobileConsecutiveFailures += 1
  }
}

function computeJitteredBackoff(attemptIndex: number): number {
  const cap = Math.min(8_000, BASE_RETRY_DELAY_MS * 2 ** Math.max(0, attemptIndex))
  return Math.round(cap * (0.5 + Math.random() * 0.5))
}

function delayWithAbort(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

function isAbortError(error: unknown, signal?: AbortSignal): boolean {
  return signal?.aborted === true || (error instanceof DOMException && error.name === 'AbortError')
}

class ApiClient {
  private async fetchResponse(
    method: string,
    path: string,
    options: ApiRequestOptions,
    body?: RequestBody,
  ): Promise<Response> {
    const baseUrl = resolveApiBaseUrl(apiContext.baseUrl)
    const url = appendQuery(joinUrl(baseUrl, path), options.query)
    const headers = new Headers(options.headers)
    if (options.auth !== false && !headers.has('Authorization')) {
      const token = apiContext.getToken()
      if (token) headers.set('Authorization', `Bearer ${token}`)
    }
    if (options.workspace !== false && !headers.has('X-Workspace-ID')) {
      const workspaceId = apiContext.getWorkspaceId()
      if (workspaceId) headers.set('X-Workspace-ID', workspaceId)
    }
    if (!headers.has('X-Device-ID')) {
      headers.set('X-Device-ID', apiContext.getDeviceId())
    }

    let requestBody: BodyInit | undefined
    if (body?.kind === 'json' && body.value !== undefined) {
      if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
      requestBody = JSON.stringify(body.value)
    } else if (body?.kind === 'raw') {
      requestBody = body.value as BodyInit
    }

    const isSseStream = headers.get('Accept')?.includes('text/event-stream') ?? false
    const retryable =
      !options.noRetry &&
      !isSseStream &&
      (RETRYABLE_METHODS.has(method) || headers.has('Idempotency-Key'))
    const baseTimeout =
      options.timeoutMs === undefined
        ? isSseStream
          ? DEFAULT_SSE_CONNECT_TIMEOUT_MS
          : DEFAULT_REQUEST_TIMEOUT_MS
        : options.timeoutMs

    let attempt = 0
    for (;;) {
      attempt += 1
      const hasMoreAttempts = attempt <= MAX_RETRY_ATTEMPTS
      const effectiveTimeoutMs =
        baseTimeout > 0
          ? isMobileWeakNetwork()
            ? Math.round(baseTimeout * 1.8)
            : baseTimeout
          : 0
      const startedAt = typeof performance !== 'undefined' ? performance.now() : Date.now()
      let attemptController: AbortController | null = null
      let timedOut = false
      let timeoutTimer: ReturnType<typeof setTimeout> | undefined
      let detachCallerAbort: (() => void) | undefined

      if (effectiveTimeoutMs > 0) {
        attemptController = new AbortController()
        if (options.signal) {
          if (options.signal.aborted) {
            attemptController.abort(options.signal.reason)
          } else {
            const onCallerAbort = () => attemptController?.abort(options.signal?.reason)
            options.signal.addEventListener('abort', onCallerAbort, { once: true })
            detachCallerAbort = () => options.signal?.removeEventListener('abort', onCallerAbort)
          }
        }
        timeoutTimer = setTimeout(() => {
          timedOut = true
          attemptController?.abort(new DOMException('Request timeout', 'TimeoutError'))
        }, effectiveTimeoutMs)
      }

      const cleanup = () => {
        if (timeoutTimer !== undefined) {
          clearTimeout(timeoutTimer)
          timeoutTimer = undefined
        }
        detachCallerAbort?.()
        detachCallerAbort = undefined
      }

      try {
        const response = await fetch(url, {
          method,
          headers,
          body: requestBody,
          signal: attemptController ? attemptController.signal : options.signal,
        })
        cleanup()
        const durationMs = (typeof performance !== 'undefined' ? performance.now() : Date.now()) - startedAt
        if (response.ok) {
          recordMobileSample(true, durationMs)
          return response
        }

        if (RETRYABLE_STATUSES.has(response.status)) {
          recordMobileSample(false, durationMs)
          if (retryable && hasMoreAttempts) {
            await delayWithAbort(computeJitteredBackoff(attempt - 1), options.signal)
            continue
          }
        }

        const responseBody = await readResponseBody(response)
        const known = appError(responseBody)
        if (response.status === 401 && options.auth !== false) {
          apiContext.onUnauthorized()
        }
        throw new ApiError({
          status: response.status,
          code: known?.code ?? 'http_error',
          message:
            known?.message ??
            (typeof responseBody === 'string' ? responseBody : response.statusText || `HTTP ${response.status}`),
          details: known?.details ?? null,
          method,
          url,
          responseBody,
        })
      } catch (error) {
        cleanup()
        const durationMs = (typeof performance !== 'undefined' ? performance.now() : Date.now()) - startedAt
        if (error instanceof ApiError) throw error
        if (!timedOut && isAbortError(error, options.signal)) throw error
        recordMobileSample(false, durationMs)
        if (retryable && hasMoreAttempts) {
          await delayWithAbort(computeJitteredBackoff(attempt - 1), options.signal)
          continue
        }
        throw new ApiError({
          status: 0,
          code: timedOut ? 'request_timeout' : 'network_error',
          message: timedOut
            ? `请求超时（>${Math.round(effectiveTimeoutMs / 1000)}s），请检查网络连接`
            : error instanceof Error
              ? error.message
              : '网络请求失败',
          details: timedOut ? { timeout_ms: effectiveTimeoutMs } : null,
          method,
          url,
          cause: error,
        })
      }
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

  getBlob(path: string, options: ApiRequestOptions = {}): Promise<Blob> {
    return this.fetchResponse('GET', path, options).then((r) => r.blob())
  }

  post<TResponse, TBody = unknown>(
    path: string,
    body?: TBody,
    options: ApiRequestOptions = {},
  ): Promise<TResponse> {
    return this.request<TResponse>('POST', path, options, { value: body, kind: 'json' })
  }

  delete<TResponse>(path: string, options: ApiRequestOptions = {}): Promise<TResponse> {
    return this.request<TResponse>('DELETE', path, options)
  }

  async *postSse<TData = unknown, TBody = unknown>(
    path: string,
    body: TBody,
    options: ApiStreamOptions = {},
  ): AsyncGenerator<SseEvent<TData>> {
    const headers = new Headers(options.headers)
    if (!headers.has('Accept')) headers.set('Accept', 'text/event-stream')
    const response = await this.fetchResponse(
      'POST',
      path,
      { ...options, headers },
      { value: body, kind: 'json' },
    )
    const idleTimeoutMs =
      options.idleTimeoutMs === undefined
        ? isMobileWeakNetwork()
          ? 54_000
          : 30_000
        : options.idleTimeoutMs
    yield* parseSseResponse<TData>(response, { ...options, idleTimeoutMs })
  }
}

export const api = new ApiClient()
