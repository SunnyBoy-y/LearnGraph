// 流统计纯函数补测（git 旁路：*.test.* 被忽略）
import { describe, it, expect } from 'vitest'
import {
  beginStreamStats,
  recordStreamDelta,
  lookupStreamStats,
  estimateStreamTokens,
  formatMilliseconds,
  formatTokensPerSecond,
  deltaTextOf,
} from './stream-stats'

describe('stream-stats', () => {
  it('begin/record/lookup 生命周期', () => {
    beginStreamStats('m1', 1000)
    recordStreamDelta('m1', 'hello')
    const stats = lookupStreamStats('m1')
    expect(stats).toBeDefined()
    expect(stats!.asciiChars).toBe(5)
    expect(stats!.cjkChars).toBe(0)
  })

  it('未注册消息 lookup 返回 undefined，record 静默忽略', () => {
    expect(lookupStreamStats('nope')).toBeUndefined()
    recordStreamDelta('nope', 'x') // 不抛异常
  })

  it('estimateStreamTokens：CJK≈0.8 token/字符，ASCII≈4 字符/token', () => {
    const base = { startedAt: 0, firstDeltaAt: 0, lastDeltaAt: 0 } as const
    expect(estimateStreamTokens({ ...base, cjkChars: 100, asciiChars: 0 })).toBe(80)
    expect(estimateStreamTokens({ ...base, cjkChars: 0, asciiChars: 80 })).toBe(20)
    expect(estimateStreamTokens({ ...base, cjkChars: 0, asciiChars: 0 })).toBe(0)
  })

  it('formatMilliseconds 格式化', () => {
    expect(formatMilliseconds(1500)).toBe('1.5s')
    expect(formatMilliseconds(500)).toBe('500ms')
    expect(formatMilliseconds(-1)).toBe('–')
  })

  it('formatTokensPerSecond 格式化', () => {
    expect(formatTokensPerSecond(10.4)).toBe('10.4 tok/s')
    expect(formatTokensPerSecond(150)).toBe('150 tok/s')
    expect(formatTokensPerSecond(0)).toBe('–')
  })

  it('deltaTextOf 提取 SSE 事件的 content_delta', () => {
    expect(deltaTextOf({ part: { content_delta: 'abc' } })).toBe('abc')
    expect(deltaTextOf({ part: {} })).toBe('')
    expect(deltaTextOf({})).toBe('')
  })
})
