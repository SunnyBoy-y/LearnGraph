import { useEffect, useMemo, useRef, useState } from 'react'
import { compactSessionContext } from '@/api/sessions'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Spinner } from '@/components/ui/spinner'
import { conversationUsage, shortDuration, shortTokens } from '@/lib/conversation-usage'
import type { CompactContextResult, Message, SessionContextUsage } from '@/types/sessions'
import './context-usage-status.css'

function StatGroup({ title, rows }: { title: string; rows: [string, string][] }) {
  return <section className="context-usage-detail__group">
    <h3>{title}</h3>
    <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
  </section>
}

/** Composer footer: hover/focus for details; tap/click also works on touch devices. */
export function ContextUsageStatus({ usage, messages, sessionId, providerId, modelId, agentMode = false, hasMoreHistory = false, onCompacted }: {
  usage: SessionContextUsage
  messages: readonly Message[]
  sessionId: string
  providerId?: string
  modelId?: string
  agentMode?: boolean
  hasMoreHistory?: boolean
  onCompacted?: () => void
}) {
  const [open, setOpen] = useState(false)
  const [compacting, setCompacting] = useState(false)
  const [result, setResult] = useState<CompactContextResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const stats = useMemo(() => conversationUsage(messages), [messages])
  // used_ratio measures proximity to compaction, not occupancy of the model window.
  const percent = Math.round(usage.estimated_tokens / Math.max(1, usage.context_window_tokens) * 100)
  const speed = stats.tokensPerSecond === undefined ? '—' : `${Math.round(stats.tokensPerSecond)} tok/s`
  const hit = stats.cacheHit === undefined ? '—' : `${stats.cacheHit}%`
  const canCompact = !compacting && usage.used_ratio >= 0.5
  const segments = [
    `${stats.rounds} 轮 · ${stats.steps} 步`,
    `上下文 ${percent}%`,
    `总耗时 ${shortDuration(stats.duration)} · 工具 ${stats.tools} 次`,
    `↑${shortTokens(stats.input)} · ↓${shortTokens(stats.output)} · 命中 ${hit}`,
    `TTFT ${shortDuration(stats.firstToken)} · ${speed}`,
  ]

  const cancelTimer = () => { clearTimeout(timer.current) }
  useEffect(() => () => clearTimeout(timer.current), [])
  const changeOpen = (next: boolean) => {
    cancelTimer()
    setOpen(next)
    if (!next) { setResult(null); setError(null) }
  }
  const scheduleClose = () => {
    cancelTimer()
    timer.current = setTimeout(() => { if (!compacting) changeOpen(false) }, 180)
  }
  const compact = async () => {
    if (!canCompact) return
    cancelTimer()
    setCompacting(true); setResult(null); setError(null)
    try {
      const response = await compactSessionContext(sessionId, { provider_id: providerId, model_id: modelId, agent_mode: agentMode })
      setResult(response)
      if (!response.skipped) onCompacted?.()
    } catch {
      setError('压缩失败，请稍后重试')
    } finally { setCompacting(false) }
  }

  return <div className="context-usage-status">
    <Popover open={open} onOpenChange={changeOpen}>
      <PopoverTrigger asChild>
        <button type="button" className="context-usage-status__trigger" aria-label="上下文用量与会话统计"
          onFocus={event => { if (event.target.matches(':focus-visible')) changeOpen(true) }}
          onPointerEnter={event => {
            if (event.pointerType === 'touch') return
            cancelTimer(); timer.current = setTimeout(() => changeOpen(true), 180)
          }} onPointerLeave={scheduleClose}>
          {segments.map(segment => <span key={segment}>{segment}</span>)}
        </button>
      </PopoverTrigger>
      <PopoverContent className="context-usage-detail" align="center" side="top" sideOffset={8}
        aria-label="上下文用量详情"
        onOpenAutoFocus={event => event.preventDefault()}
        onCloseAutoFocus={event => event.preventDefault()}
        onPointerEnter={cancelTimer} onPointerLeave={scheduleClose}>
        <StatGroup title="会话规模" rows={[
          ['对话轮次', String(stats.rounds)], ['推理步数', String(stats.steps)],
        ]} />
        <StatGroup title="上下文窗口" rows={[
          ['窗口占用', `${percent}%`],
          ['上下文大小', `${shortTokens(usage.estimated_tokens)} tok`],
          ['距自动压缩', `${shortTokens(usage.remaining_tokens)} tok`],
        ]} />
        <StatGroup title="时间开销" rows={[
          ['生成总耗时', shortDuration(stats.duration)], ['工具调用', `${stats.tools} 次`],
        ]} />
        <StatGroup title="TOKEN 用量" rows={[
          ['输入 token', stats.input === undefined ? '—' : `${shortTokens(stats.input)} tok`],
          ['输出 token', stats.output === undefined ? '—' : `${shortTokens(stats.output)} tok`],
          ['缓存命中率', hit],
        ]} />
        <StatGroup title="响应性能" rows={[
          ['首 token 延迟', shortDuration(stats.firstToken)], ['生成吞吐', speed],
        ]} />
        <StatGroup title="历史维护" rows={[
          ['摘要覆盖消息', `${usage.compacted_message_count ?? 0} 条`],
        ]} />
        <p className="context-usage-detail__note">
          {hasMoreHistory ? '轮次、步数与用量仅统计已加载的消息。' : '轮次、步数与用量统计当前消息版本。'}
          上下文为估算值；吞吐按最近回复的输出与总耗时计算。
        </p>
        <div className="context-usage-detail__compact">
          <Button size="sm" variant="ghost" disabled={!canCompact} onClick={() => void compact()}>
            {compacting && <Spinner className="size-3" />}{compacting ? '正在压缩…' : '立即压缩上下文'}
          </Button>
          {!canCompact && !compacting && <p>达到压缩阈值的 50% 后可手动压缩</p>}
          {result && <p role="status">{result.skipped ? (result.reason === 'context_too_small' ? '上下文还小，暂无需压缩' : '没有可压缩的早期消息') : `已压缩：${shortTokens(result.estimated_tokens_before)} → ${shortTokens(result.estimated_tokens_after)} tok`}</p>}
          {error && <p role="alert">{error}</p>}
        </div>
      </PopoverContent>
    </Popover>
  </div>
}
