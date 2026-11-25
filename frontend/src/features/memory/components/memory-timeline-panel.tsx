import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'

import { listMemoryTimeline, type MemoryTimeFilter } from '@/api/memory'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { workspaceQueryKey } from '@/lib/query-keys'
import { memoryOccurrenceHint } from '../memory-display'

export function MemoryTimelinePanel({ workspaceId }: { workspaceId: string }) {
  const [expression, setExpression] = useState('')
  const [timezone, setTimezone] = useState('Asia/Shanghai')
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
  const [filter, setFilter] = useState<MemoryTimeFilter | null>(null)
  const [validationError, setValidationError] = useState('')
  const timeline = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'timeline', filter),
    queryFn: () => listMemoryTimeline(filter!),
    enabled: filter !== null,
    throwOnError: false,
    retry: false,
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    setValidationError('')
    const bounds = [startAt, endAt].filter(Boolean)
    if (bounds.some((value) => !/(Z|[+-]\d{2}:\d{2})$/i.test(value) || Number.isNaN(Date.parse(value)))) {
      setValidationError('请使用带时区的 ISO 时间，例如 2026-10-18T00:00:00+08:00。')
      return
    }
    if (startAt && endAt && Date.parse(startAt) >= Date.parse(endAt)) {
      setValidationError('结束时间必须晚于开始时间。')
      return
    }
    const next = {
      ...(expression.trim() ? { time_query: expression } : {}),
      ...(startAt ? { start_at: startAt } : {}),
      ...(endAt ? { end_at: endAt } : {}),
      timezone_name: timezone,
    }
    if (JSON.stringify(next) === JSON.stringify(filter)) void timeline.refetch()
    else setFilter(next)
  }

  const entries = timeline.data ? [
    ...timeline.data.memories.map((item) => ({
      key: `memory-${item.id}`, at: item.event_at, title: item.title, content: item.content,
      hint: memoryOccurrenceHint(item) || '时间未标注',
    })),
    ...timeline.data.episodes.map((item) => ({
      key: `episode-${item.id}`, at: item.started_at, title: item.title, content: item.summary,
      hint: `对话片段 · ${memoryOccurrenceHint({ event_at: item.started_at, timezone_name: filter?.timezone_name || timezone })}`,
    })),
  ].sort((a, b) => (Date.parse(b.at || '') || 0) - (Date.parse(a.at || '') || 0)) : []

  return <section aria-label="按发生时间回忆" className="space-y-3 rounded-xl border bg-muted/10 p-4">
    <form onSubmit={submit} className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-52 flex-1 space-y-1.5">
          <Label htmlFor="memory-time-query">按发生时间回忆</Label>
          <Input id="memory-time-query" value={expression} onChange={(event) => setExpression(event.target.value)} placeholder="例如：昨天、上周、2026年10月18日" maxLength={240} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="memory-timezone">时区</Label>
          <Input id="memory-timezone" value={timezone} onChange={(event) => setTimezone(event.target.value)} className="w-40" maxLength={80} required />
        </div>
        <Button disabled={(!expression.trim() && !startAt && !endAt) || timeline.isFetching} type="submit">查询时间线</Button>
        {filter ? <Button type="button" variant="ghost" onClick={() => { setFilter(null); setValidationError('') }}>清除查询</Button> : null}
      </div>
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">精确时间范围（可选）</summary>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <div className="space-y-1.5"><Label htmlFor="memory-time-start">开始时间（含）</Label><Input id="memory-time-start" value={startAt} onChange={(event) => setStartAt(event.target.value)} placeholder="2026-10-18T00:00:00+08:00" /></div>
          <div className="space-y-1.5"><Label htmlFor="memory-time-end">结束时间（不含）</Label><Input id="memory-time-end" value={endAt} onChange={(event) => setEndAt(event.target.value)} placeholder="2026-10-19T00:00:00+08:00" /></div>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">带时区的精确边界优先于时间表达；不填写的边界由时间表达确定。</p>
      </details>
    </form>
    <p className="text-xs text-muted-foreground">按事件发生时间筛选，不是记忆的最后更新时间；区间结束时刻不包含在内。</p>
    {validationError ? <p role="alert">{validationError}</p> : null}
    {filter && timeline.isFetching ? <p role="status">正在查找这段时间的记忆…</p> : null}
    {filter && timeline.isError ? <p role="alert">时间查询失败：{timeline.error.message}</p> : null}
    {filter && !timeline.isFetching && !timeline.isError && timeline.data ? <div aria-live="polite" className="space-y-3">
      <p className="text-sm font-medium">{timeline.data.time_window.display || filter.time_query || `${filter.start_at || '不限'} — ${filter.end_at || '不限'}`} · {timeline.data.memories.length} 条记忆 / {timeline.data.episodes.length} 个对话片段</p>
      {!entries.length ? <p className="text-sm text-muted-foreground">这段时间没有匹配记录。不会用其他日期的记忆补齐。</p> : null}
      <ol className="space-y-3 border-l pl-4">
        {entries.map((item) => <li key={item.key}>
          <p className="text-xs text-muted-foreground">{item.hint}</p>
          <h4 className="text-sm font-medium">{item.title}</h4>
          <p className="whitespace-pre-wrap text-sm">{item.content}</p>
        </li>)}
      </ol>
    </div> : null}
  </section>
}
