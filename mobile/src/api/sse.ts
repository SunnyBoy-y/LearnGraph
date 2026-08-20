/**
 * 增量 SSE 解析器 —— 从 frontend/src/api/sse.ts 原样复用的自包含实现
 * （前端经过生产验证；mobile 端直接使用同一语义）。
 */

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
}

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
 * 增量解析 WHATWG Response body 为 Server-Sent Events。
 * 事件按 SSE `id:` 或 `data.event_id` 去重；重连时可传入共享 seenEventIds。
 */
export async function* parseSseResponse<TData = unknown>(
  response: Response,
  options: SseParseOptions = {},
): AsyncGenerator<SseEvent<TData>> {
  if (!response.body) throw new TypeError('The SSE response has no readable body')

  const { signal, dedupe = true } = options
  const seenEventIds = options.seenEventIds ?? new Set<string>()
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const cancelReader = () => {
    void reader.cancel(abortReason(signal as AbortSignal)).catch(() => undefined)
  }
  signal?.addEventListener('abort', cancelReader, { once: true })

  const emitBlock = (block: string): SseEvent<TData> | null => {
    const parsed = parseBlock(block)
    if (!parsed) return null

    const data = decodeData<TData>(parsed.rawData)
    const id = parsed.id || payloadEventId(data)
    if (dedupe && id) {
      if (seenEventIds.has(id)) return null
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
      const { done, value } = await reader.read()
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
