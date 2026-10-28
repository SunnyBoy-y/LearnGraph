import { describe, expect, it } from 'vitest'
import { conversationUsage, shortDuration, shortTokens } from './conversation-usage'
import type { Message } from '@/types/sessions'

const message = (role: string, provider_trace = {}, parts: Message['parts'] = []): Message => ({ role, provider_trace, parts }) as Message

describe('conversation usage', () => {
  it('counts user turns and model invocations, without double counting tool parts and trace totals', () => {
    const stats = conversationUsage([
      message('user'), message('assistant', { agent_tool_rounds: 2, agent_tool_calls: 3 }, [{ type: 'tool_call' }] as Message['parts']),
      message('user'), message('assistant'),
    ])
    expect(stats).toMatchObject({ rounds: 2, steps: 4, tools: 3 })
  })
  it('weights cache hits by reported input and uses only the latest response for performance', () => {
    const stats = conversationUsage([
      message('assistant', { input_tokens: 1000, cached_input_tokens: 500, output_tokens: 40, generation_duration_ms: 2000, first_token_ms: 100 }),
      message('assistant', { input_tokens: 9000, cached_input_tokens: 9000, output_tokens: 80, generation_duration_ms: 1000, first_token_ms: 200 }),
      message('assistant', { input_tokens: 5000 }),
    ])
    expect(stats).toMatchObject({ input: 15000, output: 120, duration: 3000, cacheHit: 95 })
    expect(stats.firstToken).toBeUndefined()
    expect(stats.tokensPerSecond).toBeUndefined()
  })
  it('keeps unavailable and invalid provider metrics distinct from known zero', () => {
    expect(conversationUsage([message('assistant', { input_tokens: -2, output_tokens: NaN, first_token_ms: Infinity })])).toMatchObject({ input: undefined, output: undefined, duration: undefined, cacheHit: undefined })
    expect(conversationUsage([message('assistant', { output_tokens: 0, generation_duration_ms: 1000 })]).tokensPerSecond).toBe(0)
    expect(shortTokens(undefined)).toBe('—')
    expect(shortTokens(3184000)).toBe('318.4万')
    expect(shortDuration(1529000)).toBe('25m29s')
  })
})
