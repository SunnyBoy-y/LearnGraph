import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'

import { listMemoryTimeline, type MemoryTimeFilter } from '@/api/memory'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { workspaceQueryKey } from '@/lib/query-keys'
import { memoryOccurrenceHint } from '../memory-display'

const COMMON_TIMEZONES = [
  'Asia/Shanghai',
  'UTC',
  'Asia/Tokyo',
  'Asia/Singapore',
  'America/New_York',
  'America/Los_Angeles',
  'Europe/London',
] as const

function offsetForTimezone(timezone: string, referenceDate: Date): string {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: timezone,
      timeZoneName: 'longOffset',
    }).formatToParts(referenceDate)
    const tzPart = parts.find((part) => part.type === 'timeZoneName')?.value ?? ''
    if (tzPart === 'GMT' || tzPart === 'UTC') return 'Z'
    const match = tzPart.match(/GMT([+-]\d{2}:\d{2})/)
    if (match) return match[1]
  } catch {
    // Fallback to browser local offset when custom timezone string is unrecognized.
  }
  const minutes = -referenceDate.getTimezoneOffset()
  const sign = minutes >= 0 ? '+' : '-'
  const abs = Math.abs(minutes)
  return `${sign}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`
}

function localInputToIso(localValue: string, timezone: string): string {
  if (!localValue) return ''
  const normalized = localValue.length === 16 ? `${localValue}:00` : localValue
  const date = new Date(normalized)
  const offset = offsetForTimezone(timezone, Number.isNaN(date.getTime()) ? new Date() : date)
  return `${normalized}${offset}`
}

function isoToLocalInput(iso: string): string {
  const match = iso.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})/)
  return match ? match[1] : ''
}

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
      setValidationError('请使用带时区的 ISO 时间，例如 2026-10-18T00:00:00+08:00，或使用日期选择器选取。')
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
          <Input id="memory-timezone" list="memory-common-timezones" value={timezone} onChange={(event) => setTimezone(event.target.value)} className="w-44" maxLength={80} required />
          <datalist id="memory-common-timezones">
            {COMMON_TIMEZONES.map((tz) => <option key={tz} value={tz} />)}
          </datalist>
        </div>
        <Button disabled={(!expression.trim() && !startAt && !endAt) || timeline.isFetching} type="submit">查询时间线</Button>
        {filter ? <Button type="button" variant="ghost" onClick={() => { setFilter(null); setValidationError('') }}>清除查询</Button> : null}
      </div>
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">精确时间范围（可选）</summary>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="memory-time-start">开始时间（含）</Label>
            <div className="flex flex-wrap gap-1.5">
              <Input
                aria-label="选择开始日期时间"
                className="w-44 shrink-0 text-xs"
                onChange={(event) => setStartAt(localInputToIso(event.target.value, timezone))}
                type="datetime-local"
                value={isoToLocalInput(startAt)}
              />
              <Input
                className="min-w-44 flex-1 font-mono text-xs"
                id="memory-time-start"
                onChange={(event) => setStartAt(event.target.value)}
                placeholder="2026-10-18T00:00:00+08:00"
                value={startAt}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="memory-time-end">结束时间（不含）</Label>
            <div className="flex flex-wrap gap-1.5">
              <Input
                aria-label="选择结束日期时间"
                className="w-44 shrink-0 text-xs"
                onChange={(event) => setEndAt(localInputToIso(event.target.value, timezone))}
                type="datetime-local"
                value={isoToLocalInput(endAt)}
              />
              <Input
                className="min-w-44 flex-1 font-mono text-xs"
                id="memory-time-end"
                onChange={(event) => setEndAt(event.target.value)}
                placeholder="2026-10-19T00:00:00+08:00"
                value={endAt}
              />
            </div>
          </div>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">选择日期时间后会自动补齐当前时区偏移；也可直接粘贴带时区的 ISO 时间。</p>
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
