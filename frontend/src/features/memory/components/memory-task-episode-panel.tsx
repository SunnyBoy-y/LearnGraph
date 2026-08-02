import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ListChecks, Milestone, Search } from 'lucide-react'

import { getMemoryTask, searchMemoryEpisodes } from '@/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  SectionHeading,
  StatePill,
  Surface,
} from '@/components/shared/page-elements'
import type { MemoryEpisode } from '@/types/episodes'
import type { MemoryTaskState } from '@/types/tasks'

function formatList(items: Array<Record<string, unknown>>, fallbackKey = 'title'): string[] {
  return items
    .map((item) => {
      const value = item[fallbackKey] ?? item['name'] ?? item['step'] ?? item['id']
      return typeof value === 'string' ? value : JSON.stringify(value)
    })
    .filter(Boolean)
}

function TaskField({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-1">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm leading-6 text-foreground/90">{value || '—'}</dd>
    </div>
  )
}

function TaskListBlock({
  title,
  items,
  emptyHint,
}: {
  title: string
  items: Array<Record<string, unknown>>
  emptyHint: string
}) {
  const lines = formatList(items)
  return (
    <div className="space-y-2">
      <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
        <Badge variant="outline">{items.length}</Badge>
      </p>
      {lines.length ? (
        <ul className="space-y-1.5 text-sm leading-6">
          {lines.map((line, index) => (
            <li className="flex gap-2" key={index}>
              <span className="text-muted-foreground">·</span>
              <span className="text-foreground/90">{line}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">{emptyHint}</p>
      )}
    </div>
  )
}

function EpisodeCard({ episode }: { episode: MemoryEpisode }) {
  const decisions = formatList(episode.decisions ?? [])
  const openQuestions = formatList(episode.open_questions ?? [])
  const constraints = formatList(episode.constraints ?? [])
  return (
    <div className="rounded-xl border bg-background/80 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-semibold">{episode.title || '未命名 Episode'}</p>
        <StatePill status={episode.status} />
        <Badge variant="outline">v{episode.stream_version}</Badge>
      </div>
      {episode.summary ? (
        <p className="mt-2 text-sm leading-6 text-muted-foreground">{episode.summary}</p>
      ) : null}
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">决定</p>
          {decisions.length ? (
            <ul className="space-y-1 text-xs leading-5">
              {decisions.map((line, index) => <li key={index}>· {line}</li>)}
            </ul>
          ) : <p className="text-xs text-muted-foreground">—</p>}
        </div>
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">开放问题</p>
          {openQuestions.length ? (
            <ul className="space-y-1 text-xs leading-5">
              {openQuestions.map((line, index) => <li key={index}>· {line}</li>)}
            </ul>
          ) : <p className="text-xs text-muted-foreground">—</p>}
        </div>
        <div className="space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">约束</p>
          {constraints.length ? (
            <ul className="space-y-1 text-xs leading-5">
              {constraints.map((line, index) => <li key={index}>· {line}</li>)}
            </ul>
          ) : <p className="text-xs text-muted-foreground">—</p>}
        </div>
      </div>
      {episode.source_message_refs?.length ? (
        <p className="mt-3 break-all font-mono text-[10px] text-muted-foreground">
          来源消息：{episode.source_message_refs.join(', ')}
        </p>
      ) : null}
      {episode.boundary_reason ? (
        <p className="mt-1 text-[10px] text-muted-foreground">边界：{episode.boundary_reason}</p>
      ) : null}
    </div>
  )
}

export function MemoryTaskEpisodePanel() {
  const [taskId, setTaskId] = useState('')
  const [submittedId, setSubmittedId] = useState('')

  const task = useQuery({
    queryKey: ['memory-task', submittedId],
    queryFn: () => getMemoryTask(submittedId),
    enabled: Boolean(submittedId),
  })

  const episodes = useQuery({
    queryKey: ['memory-episodes', submittedId],
    queryFn: () => searchMemoryEpisodes({ task_id: submittedId, limit: 10 }),
    enabled: Boolean(submittedId),
  })

  function submit(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = taskId.trim()
    if (!trimmed) return
    setSubmittedId(trimmed)
  }

  const taskData = task.data as MemoryTaskState | undefined

  return (
    <Surface className="p-5">
      <SectionHeading
        description="查看当前 Task State 与相关 Episode。所有查询按当前 tenant/user/workspace/task 隔离，不跨工作区召回。"
        title="任务与 Episode"
      />
      <form className="mt-4 flex flex-col gap-2 border-t pt-4 sm:flex-row sm:items-center" onSubmit={submit}>
        <Input
          aria-label="任务 ID"
          className="sm:max-w-sm"
          onChange={(event) => setTaskId(event.target.value)}
          placeholder="输入 task_id（如 task_memory）"
          value={taskId}
        />
        <Button disabled={!taskId.trim()} size="sm" type="submit" variant="outline">
          <Search className="size-4" />查询
        </Button>
      </form>

      {!submittedId ? (
        <p className="mt-4 text-xs text-muted-foreground">
          输入任务 ID 后展示目标、当前阶段、已完成/待办步骤、阻塞项与下一步，以及该任务下的 Episode 决策、开放问题与来源。
        </p>
      ) : task.isPending ? (
        <LoadingState />
      ) : task.isError ? (
        <ErrorState message={task.error.message} />
      ) : taskData ? (
        <div className="mt-4 space-y-5 border-t pt-4">
          <div className="flex flex-wrap items-center gap-2">
            <Milestone className="size-4 text-primary" />
            <p className="text-sm font-semibold">{taskData.title || taskData.task_id}</p>
            <StatePill status={taskData.status} />
            <Badge variant="outline">v{taskData.stream_version}</Badge>
            {taskData.current_stage ? <Badge variant="secondary">{taskData.current_stage}</Badge> : null}
          </div>
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            <TaskField label="目标" value={taskData.goal} />
            <TaskField label="下一步" value={taskData.next_action} />
          </dl>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="rounded-xl border bg-muted/20 p-4">
              <TaskListBlock
                emptyHint="尚无已完成步骤"
                items={taskData.completed ?? []}
                title="已完成"
              />
            </div>
            <div className="rounded-xl border bg-muted/20 p-4">
              <div className="space-y-2">
                <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  <ListChecks className="size-3.5" />待办
                  <Badge variant="outline">{(taskData.pending ?? []).length}</Badge>
                </p>
                <TaskListBlock emptyHint="尚无待办步骤" items={taskData.pending ?? []} title="" />
              </div>
            </div>
          </div>
          {(taskData.blocked_by ?? []).length ? (
            <div className="rounded-xl border border-amber-500/30 bg-amber-50/40 p-4 dark:bg-amber-950/15">
              <TaskListBlock emptyHint="" items={taskData.blocked_by ?? []} title="阻塞项" />
            </div>
          ) : null}
        </div>
      ) : null}

      {submittedId ? (
        <div className="mt-6 border-t pt-4">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            相关 Episode
          </p>
          {episodes.isPending ? (
            <LoadingState />
          ) : episodes.isError ? (
            <ErrorState message={episodes.error.message} />
          ) : (episodes.data ?? []).length ? (
            <div className="grid gap-3">
              {episodes.data.map((episode) => <EpisodeCard episode={episode} key={episode.episode_id} />)}
            </div>
          ) : (
            <EmptyState description="该任务下还没有 Episode；会在主题结束或 Token 阈值触发时自动生成。" title="暂无 Episode" />
          )}
        </div>
      ) : null}
    </Surface>
  )
}
