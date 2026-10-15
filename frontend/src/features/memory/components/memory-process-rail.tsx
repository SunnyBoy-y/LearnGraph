import { useSyncExternalStore } from 'react'
import {
  Brain,
  Check,
  CircleAlert,
  CircleDot,
  Clock3,
  LoaderCircle,
  Wrench,
} from 'lucide-react'

import {
  getMemoryProcess,
  subscribeMemoryProcess,
  type MemoryProcess,
  type MemoryProcessStep,
} from '../memory-process'

function stepIcon(step: MemoryProcessStep) {
  if (step.status === 'failed') return <CircleAlert className="size-3.5 text-destructive" />
  if (step.status === 'running') return <LoaderCircle className="size-3.5 animate-spin text-primary" />
  if (step.status === 'completed') return <Check className="size-3.5 text-emerald-600 dark:text-emerald-400" />
  if (step.kind === 'reasoning') return <Brain className="size-3.5" />
  if (step.kind === 'tool') return <Wrench className="size-3.5" />
  if (step.kind === 'result') return <CircleDot className="size-3.5" />
  return <Clock3 className="size-3.5" />
}

function statusLabel(process: MemoryProcess) {
  if (process.status === 'running') return '执行中'
  if (process.status === 'failed') return '未完成'
  return '已完成'
}

function elapsed(process: MemoryProcess) {
  const end = process.completedAt ?? Date.now()
  const seconds = Math.max(0, (end - process.startedAt) / 1000)
  if (seconds < 1) return '不到 1 秒'
  return `${seconds.toFixed(1)} 秒`
}

export function MemoryProcessRail({ workspaceId }: { workspaceId: string }) {
  const process = useSyncExternalStore(
    subscribeMemoryProcess,
    () => getMemoryProcess(workspaceId),
    () => null,
  )

  return (
    <section aria-label="记忆更新过程" className="memory-process-rail">
      <header className="memory-process-rail__header">
        <div>
          <span className="memory-process-rail__eyebrow">
            <Brain className="size-3.5" />AI 导师过程
          </span>
          <h2>推理与工具调用</h2>
          <p>发送记忆后，这里会显示本次整理的可追踪阶段。</p>
        </div>
        {process ? (
          <span className={`memory-process-rail__status is-${process.status}`}>
            {statusLabel(process)}
          </span>
        ) : null}
      </header>

      {!process ? (
        <div className="memory-process-rail__empty">
          <Wrench className="size-5" />
          <strong>等待一次记忆更新</strong>
          <span>输入“记住、修改或忘记什么”，发送后会在这里看到推理与工具步骤。</span>
        </div>
      ) : (
        <div className="memory-process-rail__body">
          <div className="memory-process-rail__meta">
            <span title={process.input}>“{process.input}”</span>
            <small>
              {process.modelId ? `模型 · ${process.modelId}` : '模型 · 按记忆设置'} · {elapsed(process)}
            </small>
          </div>
          <ol className="memory-process-rail__steps">
            {process.steps.map((step, index) => (
              <li className={`memory-process-rail__step is-${step.status}`} key={step.id}>
                <span className="memory-process-rail__step-icon">{stepIcon(step)}</span>
                <span className="memory-process-rail__step-copy">
                  <strong>{step.label}</strong>
                  {step.detail ? <small>{step.detail}</small> : null}
                </span>
                {index < process.steps.length - 1 ? <i aria-hidden="true" /> : null}
              </li>
            ))}
          </ol>
          <p className="memory-process-rail__note">
            过程摘要只展示阶段与结果，不会把模型的隐式思维原文暴露出来。
          </p>
          {process.error ? <p className="memory-process-rail__error">{process.error}</p> : null}
        </div>
      )}
    </section>
  )
}
