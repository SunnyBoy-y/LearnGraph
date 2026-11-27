import { adaptiveTimeoutMs } from '@/lib/network-quality'

export interface SseEvent<TData = unknown> {
  event: string
  id?: string
  data: TData
  rawData: string
}

export interface SseParseOptions {
  signal?: AbortSignal
  dedupe?: boolean
  seenEventIds?: Set<string>
  /**
   * Maximum silence between received stream chunks (including server keepalive
   * comments) before treating the connection as half-open. Pass `0` to disable.
   */
  idleTimeoutMs?: number
}

export class SseIdleTimeoutError extends Error {
  readonly code = 'sse_idle_timeout'
  readonly timeoutMs: number

  constructor(timeoutMs: number) {
    super(`流式连接超过 ${Math.round(timeoutMs / 1000)} 秒无数据响应`)
    this.name = 'SseIdleTimeoutError'
    this.timeoutMs = timeoutMs
  }
}

const DEFAULT_SSE_IDLE_TIMEOUT_MS = 30_000

/**
 * Cap for the SSE dedupe window. Very long agent streams emit tens of
 * thousands of events; an unbounded Set would keep every id resident. Event
 * sequences are monotonic, so ids older than the cursor are never re-seen;
 * clearing at the cap costs nothing but the Set memory.
 */
const MAX_SEEN_EVENT_IDS = 4096

interface ParsedBlock {
  event: string
  id?: string
  rawData: string
}

function abortReason(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException('The operation was aborted', 'AbortError')
}

function assertNotAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw abortReason(signal)
}

function parseBlock(block: string): ParsedBlock | null {
  let event = 'message'
  let id: string | undefined
  const data: string[] = []

  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue

    const colon = line.indexOf(':')
    const field = colon === -1 ? line : line.slice(0, colon)
    let value = colon === -1 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'event') event = value || 'message'
    else if (field === 'data') data.push(value)
    else if (field === 'id' && !value.includes('\0')) id = value
  }

  if (data.length === 0) return null
  return { event, ...(id !== undefined ? { id } : {}), rawData: data.join('\n') }
}

function decodeData<TData>(rawData: string): TData {
  try {
    return JSON.parse(rawData) as TData
  } catch {
    return rawData as TData
  }
}

function payloadEventId(data: unknown): string | undefined {
  if (typeof data !== 'object' || data === null || !('event_id' in data)) return undefined
  const eventId = (data as { event_id?: unknown }).event_id
  return typeof eventId === 'string' && eventId.length > 0 ? eventId : undefined
}

function takeBlock(buffer: string): { block: string; rest: string } | null {
  const boundary = /\r?\n\r?\n/.exec(buffer)
  if (!boundary || boundary.index === undefined) return null
  return {
    block: buffer.slice(0, boundary.index),
    rest: buffer.slice(boundary.index + boundary[0].length),
  }
}

/**
 * Incrementally parses a WHATWG Response body as Server-Sent Events.
 *
 * An event is deduplicated by the SSE `id:` field, or by `data.event_id` when
 * providers place the identifier inside their JSON envelope. Callers may pass
 * a shared `seenEventIds` set when reconnecting a stream.
 */
export async function* parseSseResponse<TData = unknown>(
  response: Response,
  options: SseParseOptions = {},
): AsyncGenerator<SseEvent<TData>> {
  if (!response.body) throw new TypeError('The SSE response has no readable body')

  const { signal, dedupe = true } = options
  const idleTimeoutMs =
    options.idleTimeoutMs === undefined
      ? adaptiveTimeoutMs(DEFAULT_SSE_IDLE_TIMEOUT_MS)
      : options.idleTimeoutMs
  const seenEventIds = options.seenEventIds ?? new Set<string>()
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const cancelReader = () => {
    void reader.cancel(abortReason(signal as AbortSignal)).catch(() => undefined)
  }
  signal?.addEventListener('abort', cancelReader, { once: true })

  const readChunkWithTimeout = async (): Promise<ReadableStreamReadResult<Uint8Array>> => {
    if (idleTimeoutMs <= 0) return reader.read()
    let timer: ReturnType<typeof setTimeout> | undefined
    let timedOut = false
    const timeoutError = new SseIdleTimeoutError(idleTimeoutMs)
    const timeoutPromise = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        timedOut = true
        reject(timeoutError)
        void reader.cancel(timeoutError).catch(() => undefined)
      }, idleTimeoutMs)
    })
    try {
      const result = await Promise.race([reader.read(), timeoutPromise])
      if (timedOut) throw timeoutError
      return result
    } catch (error) {
      if (timedOut) throw timeoutError
      throw error
    } finally {
      if (timer !== undefined) clearTimeout(timer)
    }
  }

  const emitBlock = (block: string): SseEvent<TData> | null => {
    const parsed = parseBlock(block)
    if (!parsed) return null

    const data = decodeData<TData>(parsed.rawData)
    const id = parsed.id || payloadEventId(data)
    if (dedupe && id) {
      if (seenEventIds.has(id)) return null
      // Bound the dedupe window (dsh event-window style): very long agent
      // streams can emit tens of thousands of events, and an unbounded Set
      // would keep every id resident for the whole session. Event sequences
      // are monotonic, so older ids are never seen again after the cursor
      // passes them; clearing at the cap loses nothing but the Set memory.
      if (seenEventIds.size >= MAX_SEEN_EVENT_IDS) seenEventIds.clear()
      seenEventIds.add(id)
    }

    return {
      event: parsed.event,
      ...(id ? { id } : {}),
      data,
      rawData: parsed.rawData,
    }
  }

  try {
    assertNotAborted(signal)
    while (true) {
      const { done, value } = await readChunkWithTimeout()
      assertNotAborted(signal)
      buffer += decoder.decode(value, { stream: !done })

      let next = takeBlock(buffer)
      while (next) {
        buffer = next.rest
        const event = emitBlock(next.block)
        if (event) yield event
        next = takeBlock(buffer)
      }

      if (done) break
    }

    if (buffer.trim()) {
      const event = emitBlock(buffer)
      if (event) yield event
    }
  } finally {
    signal?.removeEventListener('abort', cancelReader)
    reader.releaseLock()
  }
}

