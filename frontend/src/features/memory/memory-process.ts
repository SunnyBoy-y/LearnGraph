import type { MemoryProfileIntentResult } from '@/types/memory'

/**
 * Small in-memory event log for the memory composer.  Memory profile writes
 * are intentionally synchronous REST calls (they are not chat messages), so
 * they do not naturally appear in the conversation. Keeping the latest
 * operation here lets the shell render a local reasoning/tool timeline
 * without inventing a second session just for UI.
 */

export type MemoryProcessStepKind = 'request' | 'reasoning' | 'tool' | 'result'
export type MemoryProcessStepStatus = 'pending' | 'running' | 'completed' | 'failed'

export interface MemoryProcessStep {
  id: string
  kind: MemoryProcessStepKind
  label: string
  detail?: string
  status: MemoryProcessStepStatus
  startedAt?: number
  completedAt?: number
}

export interface MemoryProcess {
  id: string
  workspaceId: string
  input: string
  providerId?: string
  modelId?: string
  status: 'running' | 'completed' | 'failed'
  startedAt: number
  completedAt?: number
  steps: MemoryProcessStep[]
  result?: MemoryProfileIntentResult
  error?: string
}

type Listener = () => void

const processes = new Map<string, MemoryProcess>()
const listeners = new Set<Listener>()

function notify() {
  listeners.forEach((listener) => listener())
}

function now() {
  return Date.now()
}

function processId() {
  return `memory-process-${now()}-${Math.random().toString(36).slice(2, 8)}`
}

function step(
  id: string,
  kind: MemoryProcessStepKind,
  label: string,
  status: MemoryProcessStepStatus,
  detail?: string,
): MemoryProcessStep {
  const timestamp = now()
  return {
    id,
    kind,
    label,
    detail,
    status,
    startedAt: timestamp,
    ...(status === 'completed' || status === 'failed' ? { completedAt: timestamp } : {}),
  }
}

export function startMemoryProcess(input: {
  workspaceId: string
  text: string
  providerId?: string
  modelId?: string
}): string {
  const id = processId()
  const startedAt = now()
  processes.set(input.workspaceId, {
    id,
    workspaceId: input.workspaceId,
    input: input.text,
    providerId: input.providerId,
    modelId: input.modelId,
    status: 'running',
    startedAt,
    steps: [
      step('request', 'request', '已接收记忆更新请求', 'completed'),
      step('reasoning', 'reasoning', '推理：分析记忆意图', 'running', '识别需要记住、修改或忘记的事实'),
      step('read-memory', 'tool', '工具调用：读取并校验现有记忆', 'running'),
      step('write-memory', 'tool', '工具调用：写入记忆草稿', 'pending'),
      step('result', 'result', '整理学习记忆报告', 'pending'),
    ],
  })
  notify()
  return id
}

export function updateMemoryProcess(
  workspaceId: string,
  id: string,
  update: (current: MemoryProcess) => MemoryProcess,
) {
  const current = processes.get(workspaceId)
  if (!current || current.id !== id) return
  processes.set(workspaceId, update(current))
  notify()
}

export function completeMemoryProcess(
  workspaceId: string,
  id: string,
  result: MemoryProfileIntentResult,
) {
  updateMemoryProcess(workspaceId, id, (current) => {
    const timestamp = now()
    const committed = result.auto_committed || result.drafts_created
    const detail = result.status === 'no_change'
      ? '没有识别到需要长期保存的变化'
      : `${result.drafts_created} 条草稿 · ${result.auto_committed} 条已自动提交`
    return {
      ...current,
      status: 'completed',
      completedAt: timestamp,
      providerId: result.provider_id ?? current.providerId,
      modelId: result.model_id ?? current.modelId,
      result,
      steps: current.steps.map((item) => {
        if (item.id === 'reasoning') {
          return { ...item, status: 'completed', completedAt: timestamp }
        }
        if (item.id === 'read-memory') {
          return { ...item, status: 'completed', completedAt: timestamp }
        }
        if (item.id === 'write-memory') {
          return {
            ...item,
            status: 'completed',
            detail: committed ? `已处理 ${committed} 条记忆` : '未产生新的写入',
            completedAt: timestamp,
          }
        }
        if (item.id === 'result') {
          return { ...item, status: 'completed', detail, completedAt: timestamp }
        }
        return item
      }),
    }
  })
}

export function failMemoryProcess(workspaceId: string, id: string, error: string) {
  updateMemoryProcess(workspaceId, id, (current) => {
    const timestamp = now()
    return {
      ...current,
      status: 'failed',
      completedAt: timestamp,
      error,
      steps: current.steps.map((item) => {
        if (item.status === 'running') {
          return { ...item, status: 'failed', detail: error, completedAt: timestamp }
        }
        if (item.id === 'result') {
          return { ...item, status: 'failed', detail: '本次记忆更新未完成', completedAt: timestamp }
        }
        return item
      }),
    }
  })
}

export function getMemoryProcess(workspaceId: string): MemoryProcess | null {
  return processes.get(workspaceId) ?? null
}

export function subscribeMemoryProcess(listener: Listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
