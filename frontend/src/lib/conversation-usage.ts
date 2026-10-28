import type { UnknownRecord } from '@/types/common'
import type { Message } from '@/types/sessions'

export function usageNumber(record: UnknownRecord, key: string): number | undefined {
  const value = record[key]
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined
}

/** Current visible versions only; never add retry overlays or cached stream estimates twice. */
export function conversationUsage(messages: readonly Message[]) {
  let rounds = 0, steps = 0, tools = 0
  let input: number | undefined, output: number | undefined, duration: number | undefined
  let cached = 0, cacheInput = 0
  let latest: UnknownRecord = {}
  for (const message of messages) {
    if (message.role === 'user') rounds += 1
    if (message.role !== 'assistant') continue
    const trace = message.provider_trace
    const toolRounds = usageNumber(trace, 'agent_tool_rounds')
    steps += (toolRounds ?? 0) + 1
    tools += usageNumber(trace, 'agent_tool_calls') ?? message.parts.filter(part => part.type === 'tool_call').length
    const messageInput = usageNumber(trace, 'input_tokens')
    const messageOutput = usageNumber(trace, 'output_tokens')
    const messageDuration = usageNumber(trace, 'generation_duration_ms')
    if (messageInput !== undefined) input = (input ?? 0) + messageInput
    if (messageOutput !== undefined) output = (output ?? 0) + messageOutput
    if (messageDuration !== undefined) duration = (duration ?? 0) + messageDuration
    const messageCached = usageNumber(trace, 'cached_input_tokens')
    // Only include inputs for which the provider actually reports cache usage.
    if (messageInput !== undefined && messageCached !== undefined) {
      cacheInput += messageInput
      cached += Math.min(messageCached, messageInput)
    }
    latest = trace
  }
  const lastDuration = usageNumber(latest, 'generation_duration_ms')
  const lastOutput = usageNumber(latest, 'output_tokens')
  return {
    rounds, steps, tools, input, output, duration,
    cacheHit: cacheInput > 0 ? Math.round(cached / cacheInput * 100) : undefined,
    firstToken: usageNumber(latest, 'first_token_ms'),
    tokensPerSecond: lastDuration && lastOutput !== undefined ? lastOutput / lastDuration * 1000 : undefined,
  }
}

export function shortTokens(tokens: number | undefined): string {
  if (tokens === undefined) return '—'
  if (tokens >= 10_000) return `${Number((tokens / 10_000).toFixed(1))}万`
  if (tokens >= 1_000) return `${Number((tokens / 1_000).toFixed(1))}千`
  return String(Math.round(tokens))
}

export function shortDuration(ms: number | undefined): string {
  if (ms === undefined) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${Number((ms / 1000).toFixed(1))}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m${seconds % 60}s`
  return `${Math.floor(seconds / 3600)}h${Math.floor(seconds % 3600 / 60)}m`
}
