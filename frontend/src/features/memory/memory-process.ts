import type { MemoryProfileIntentResult } from '@/types/memory'

/** Latest server-emitted memory Agent execution, scoped to the workspace. */

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
    steps: [step('prepare', 'request', '正在连接记忆 Agent', 'running')],
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

export function recordMemoryProcessStep(
  workspaceId: string,
  id: string,
  event: Omit<MemoryProcessStep, 'startedAt' | 'completedAt'>,
) {
  updateMemoryProcess(workspaceId, id, (current) => {
    const previous = current.steps.find((item) => item.id === event.id)
    const next: MemoryProcessStep = {
      ...event,
      startedAt: previous?.startedAt ?? now(),
      ...(['completed', 'failed'].includes(event.status) ? { completedAt: now() } : {}),
    }
    return {
      ...current,
      steps: previous
        ? current.steps.map((item) => item.id === event.id ? next : item)
        : [...current.steps, next],
    }
  })
}

export function completeMemoryProcess(
  workspaceId: string,
  id: string,
  result: MemoryProfileIntentResult,
) {
  updateMemoryProcess(workspaceId, id, (current) => ({
    ...current,
    status: 'completed',
    completedAt: now(),
    providerId: result.provider_id ?? current.providerId,
    modelId: result.model_id ?? current.modelId,
    result,
    steps: [...current.steps, step(
      'result', 'result', '记忆 Agent 处理结果', 'completed',
      `提交 ${result.auto_committed} 条，待确认 ${Math.max(0, result.drafts_created - result.auto_committed)} 条`
        + (result.message ? `。${result.message}` : ''),
    )],
  }))
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
