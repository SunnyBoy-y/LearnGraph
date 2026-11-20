import type { ChatGoalAction } from '@/api/chat-goals'

export type GoalCommand =
  | { type: 'status' }
  | { type: 'control'; action: ChatGoalAction }
  | { type: 'start'; objective: string; maxRounds?: number }
  | { type: 'invalid'; reason: string }

export function parseGoalCommand(text: string): GoalCommand | null {
  const match = /^\/goal(?=\s|$)([\s\S]*)$/i.exec(text.trim())
  if (!match) return null
  let objective = match[1].trim()
  if (!objective || /^status$/i.test(objective)) return { type: 'status' }
  const action = objective.toLowerCase()
  if (action === 'pause' || action === 'resume' || action === 'clear') {
    return { type: 'control', action }
  }
  let maxRounds: number | undefined
  if (/^--rounds(?=\s|$)/.test(objective)) {
    const budget = /^--rounds\s+(\d{1,2})\s+([\s\S]+)$/.exec(objective)
    if (!budget || Number(budget[1]) < 1 || Number(budget[1]) > 20) {
      return { type: 'invalid', reason: '轮次须在 1–20 之间，格式：/goal --rounds 8 任务目标' }
    }
    maxRounds = Number(budget[1])
    objective = budget[2].trim()
  }
  if (Array.from(objective).length > 4000) return { type: 'invalid', reason: '长程任务目标最多 4000 字。' }
  return { type: 'start', objective, maxRounds }
}
