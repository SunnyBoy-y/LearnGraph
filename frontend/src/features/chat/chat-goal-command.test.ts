import { describe, expect, it } from 'vitest'
import { parseGoalCommand } from './chat-goal-command'

describe('parseGoalCommand', () => {
  it('distinguishes slash commands from ordinary conversation', () => {
    expect(parseGoalCommand('What does /goal mean?')).toBeNull()
    expect(parseGoalCommand('/goals')).toBeNull()
    expect(parseGoalCommand(' /goal ')).toEqual({ type: 'status' })
    expect(parseGoalCommand('/goal status')).toEqual({ type: 'status' })
  })

  it.each(['pause', 'resume', 'clear'])('parses the %s control', (action) => {
    expect(parseGoalCommand(`/goal ${action}`)).toEqual({ type: 'control', action })
  })

  it('accepts multiline objectives and an explicit bounded round count', () => {
    expect(parseGoalCommand('/goal --rounds 8 生成报告\n验收：附上出处')).toEqual({
      type: 'start', objective: '生成报告\n验收：附上出处', maxRounds: 8,
    })
    expect(parseGoalCommand('/goal pause and inspect')).toMatchObject({ type: 'start' })
    for (const text of ['--rounds 0 任务', '--rounds 21 任务', '--rounds 5', 'a'.repeat(4001)]) {
      expect(parseGoalCommand(`/goal ${text}`)).toMatchObject({ type: 'invalid' })
    }
  })
})
