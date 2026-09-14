import { type FormEvent, type ReactNode, useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { History, Info, MoreHorizontal, Pencil, RotateCcw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { getMemory, listMemoryRevisions, updateMemory } from '@/api'
import { EmptyState, ErrorState, LoadingState } from '@/components/shared/page-elements'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogMedia,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { workspaceQueryKey } from '@/lib/query-keys'
import type { MemoryEntry, MemoryRevision, MemoryZone } from '@/types/memory'
import { formatMemoryTime, memoryCategoryLabel, memoryCategoryOptions, memoryUpdatedHint } from '../memory-display'
import { MemoryGovernancePanel } from './memory-governance-panel'

const zoneOptions: Array<{ zone: MemoryZone; title: string }> = [
  { zone: 'hot', title: '热摘要' },
  { zone: 'recent', title: '近期事件' },
  { zone: 'topics', title: '主题记忆' },
  { zone: 'archive', title: '冷区归档' },
]

function memoryBody(markdown: string | null): string {
  if (!markdown) return ''
  const lines = markdown.split('\n')
  if (lines[0]?.startsWith('# ')) lines.shift()
  while (lines[0] === '') lines.shift()
  return lines.join('\n').trimEnd()
}

export function MemoryAllList({
  busy,
  createAction,
  items,
  workspaceId,
  onDelete,
  onRestoreRevision,
  onUpdate,
}: {
  busy: boolean
  createAction?: ReactNode
  items: MemoryEntry[]
  workspaceId: string
  onDelete: (id: string) => void
  onRestoreRevision: (item: MemoryEntry, revision: number) => void
  onUpdate: (id: string, payload: Parameters<typeof updateMemory>[1]) => Promise<void>
}) {
  const [category, setCategory] = useState<string | null>(null)
  const [detailId, setDetailId] = useState<string | null>(null)
  const options = memoryCategoryOptions(items)
  const visible = category ? items.filter((item) => memoryCategoryLabel(item) === category) : items

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <Button
            onClick={() => setCategory(null)}
            size="sm"
            variant={category === null ? 'default' : 'outline'}
          >
            全部
            <span className="ml-1.5 font-mono text-[10px] tabular-nums">{items.length}</span>
          </Button>
          {options.map((label) => (
            <Button
              key={label}
              onClick={() => setCategory(label)}
              size="sm"
              variant={category === label ? 'default' : 'outline'}
            >
              {label}
              <span className="ml-1.5 font-mono text-[10px] tabular-nums">
                {items.filter((item) => memoryCategoryLabel(item) === label).length}
              </span>
            </Button>
          ))}
        </div>
        {createAction}
      </div>

      {!items.length ? (
        <div className="surface p-2">
          <EmptyState
            description="在报告页底部直接告诉 AI 导师值得记住的内容，或手动新增一条。事件投影记忆同样会出现在这里。"
            title="当前工作区还没有可见记忆"
          />
        </div>
      ) : (
        <section className="surface overflow-hidden">
          <div className="divide-y">
            {visible.map((item) => (
              <MemoryListRow
                busy={busy}
                item={item}
                key={item.id}
                onDelete={() => onDelete(item.id)}
                onOpenDetail={() => setDetailId(item.id)}
                onRestoreRevision={(revision) => onRestoreRevision(item, revision)}
                onUpdate={(payload) => onUpdate(item.id, payload)}
                workspaceId={workspaceId}
              />
            ))}
          </div>
          {!visible.length ? (
            <EmptyState
              description="换一个分类，或在报告里让 AI 导师学习新的偏好。"
              title="该分类下没有记忆"
            />
          ) : null}
        </section>
      )}

      <TechnicalDetailDialog
        memoryId={detailId}
        onClose={() => setDetailId(null)}
        workspaceId={workspaceId}
      />
    </div>
  )
}

function MemoryListRow({
  busy,
  item,
  workspaceId,
  onDelete,
  onOpenDetail,
  onRestoreRevision,
  onUpdate,
}: {
  busy: boolean
  item: MemoryEntry
  workspaceId: string
  onDelete: () => void
  onOpenDetail: () => void
  onRestoreRevision: (revision: number) => void
  onUpdate: (payload: Parameters<typeof updateMemory>[1]) => Promise<void>
}) {
  const [revisionOpen, setRevisionOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const readOnly = item.view_source === 'event'
  const sourceCount = item.source_ids?.length ?? 0
  const updated = memoryUpdatedHint(item.updated_at)

  return (
    <div className="flex items-start gap-3 px-5 py-3.5">
      <div className="min-w-0 flex-1">
        <p className="line-clamp-2 text-sm font-medium leading-5">{item.title}</p>
        <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
          <span>{memoryCategoryLabel(item)}</span>
          {sourceCount ? (
            <>
              <span className="text-border">·</span>
              <span>{sourceCount} 个来源</span>
            </>
          ) : null}
          {updated ? (
            <>
              <span className="text-border">·</span>
              <span>{updated}</span>
            </>
          ) : null}
          {readOnly ? (
            <>
              <span className="text-border">·</span>
              <span>来自学习记录（只读）</span>
            </>
          ) : null}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        {readOnly ? null : (
          <EditMemoryDialog
            busy={busy}
            item={item}
            onUpdate={onUpdate}
            workspaceId={workspaceId}
          />
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button aria-label={`${item.title} 的更多操作`} size="icon-xs" variant="ghost">
                <MoreHorizontal className="size-3.5" />
              </Button>
            </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuItem onClick={onOpenDetail}>
              <Info className="size-3.5" />技术详情
            </DropdownMenuItem>
            <DropdownMenuItem disabled={readOnly} onClick={() => setRevisionOpen(true)}>
              <History className="size-3.5" />历史版本
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              disabled={readOnly}
              onSelect={() => setDeleteOpen(true)}
              variant="destructive"
            >
              <Trash2 className="size-3.5" />删除
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <Dialog onOpenChange={setRevisionOpen} open={revisionOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>历史版本 · {item.title}</DialogTitle>
            <DialogDescription>
              恢复旧版不会覆盖历史，而是基于旧内容创建新的版本。
            </DialogDescription>
          </DialogHeader>
          <RevisionList
            busy={busy}
            currentRevision={item.revision}
            onRestore={(revision) => {
              setRevisionOpen(false)
              onRestoreRevision(revision)
            }}
            workspaceId={workspaceId}
            memoryId={item.id}
          />
        </DialogContent>
      </Dialog>

      <AlertDialog onOpenChange={setDeleteOpen} open={deleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogMedia className="bg-destructive/10 text-destructive">
              <Trash2 />
            </AlertDialogMedia>
            <AlertDialogTitle>删除“{item.title}”？</AlertDialogTitle>
            <AlertDialogDescription>
              删除后会从报告中移除。30 分钟内可以在 ··· → 回收站直接恢复；之后正文不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={onDelete} variant="destructive">
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function RevisionList({
  busy,
  currentRevision,
  memoryId,
  workspaceId,
  onRestore,
}: {
  busy: boolean
  currentRevision: number
  memoryId: string
  workspaceId: string
  onRestore: (revision: number) => void
}) {
  const revisions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'revisions', memoryId),
    queryFn: () => listMemoryRevisions(memoryId),
  })

  if (revisions.isPending) return <LoadingState />
  if (revisions.isError) return <ErrorState message={revisions.error.message} />

  return (
    <div className="max-h-[55vh] divide-y overflow-auto border-y">
      {(revisions.data ?? []).map((revision) => (
        <RevisionRow
          busy={busy}
          currentRevision={currentRevision}
          key={revision.id}
          onRestore={onRestore}
          revision={revision}
        />
      ))}
    </div>
  )
}

function RevisionRow({
  busy,
  currentRevision,
  revision,
  onRestore,
}: {
  busy: boolean
  currentRevision: number
  revision: MemoryRevision
  onRestore: (revision: number) => void
}) {
  return (
    <div className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-mono text-xs">版本 {revision.revision}</p>
          <Badge variant="outline">{revision.operation}</Badge>
          {revision.is_active ? <Badge>当前</Badge> : null}
        </div>
        <p className="mt-2 line-clamp-2 text-xs leading-5 text-muted-foreground">
          {revision.content ?? '正文已按保留策略销毁'}
        </p>
        <p className="mt-1 text-[10px] text-muted-foreground">
          {formatMemoryTime(revision.created_at)} · {revision.reason}
        </p>
      </div>
      <Button
        disabled={busy || revision.revision === currentRevision || revision.content === null}
        onClick={() => onRestore(revision.revision)}
        size="sm"
        variant="outline"
      >
        <RotateCcw className="size-4" />恢复此版
      </Button>
    </div>
  )
}

function EditMemoryDialog({
  busy,
  item,
  workspaceId,
  onUpdate,
}: {
  busy: boolean
  item: MemoryEntry
  workspaceId: string
  onUpdate: (payload: Parameters<typeof updateMemory>[1]) => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const detail = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'detail', item.id),
    queryFn: () => getMemory(item.id),
    enabled: open,
  })
  const [title, setTitle] = useState(item.title)
  const [content, setContent] = useState('')
  const [zone, setZone] = useState<MemoryZone>(item.zone)

  useEffect(() => {
    if (detail.data) {
      setTitle(detail.data.title)
      setContent(memoryBody(detail.data.content))
      setZone(detail.data.zone)
    }
  }, [detail.data])

  async function submit(event: FormEvent) {
    event.preventDefault()
    try {
      await onUpdate({
        expected_revision: detail.data?.revision ?? item.revision,
        title: title.trim(),
        content: content.trim(),
        zone,
        reason: 'user_edit',
      })
    } catch {
      await detail.refetch()
      toast.error('保存未完成：记忆可能已在其他窗口更新，已刷新为最新版本后请检查并重试。')
      return
    }
    setOpen(false)
  }

  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button size="xs" variant="ghost">
          <Pencil className="size-3" />编辑
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={(event) => void submit(event)}>
          <DialogHeader>
            <DialogTitle>编辑记忆</DialogTitle>
            <DialogDescription>
              保存会创建新的版本；如果其他窗口已经修改过，会返回冲突而不是覆盖。
            </DialogDescription>
          </DialogHeader>
          {detail.isPending ? (
            <LoadingState />
          ) : detail.isError ? (
            <ErrorState message={detail.error.message} />
          ) : (
            <div className="space-y-4 py-5">
              <div className="space-y-2">
                <Label htmlFor={`memory-title-${item.id}`}>标题</Label>
                <Input
                  id={`memory-title-${item.id}`}
                  onChange={(event) => setTitle(event.target.value)}
                  value={title}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor={`memory-content-${item.id}`}>内容</Label>
                <Textarea
                  className="min-h-48 font-mono text-xs"
                  id={`memory-content-${item.id}`}
                  onChange={(event) => setContent(event.target.value)}
                  value={content}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor={`memory-zone-${item.id}`}>读取优先级</Label>
                <Select onValueChange={(value) => setZone(value as MemoryZone)} value={zone}>
                  <SelectTrigger id={`memory-zone-${item.id}`}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {zoneOptions.map((option) => (
                      <SelectItem key={option.zone} value={option.zone}>
                        {option.title}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}
          <DialogFooter>
            <Button disabled={busy || detail.isPending || !title.trim() || !content.trim()} type="submit">
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/**
 * 工程字段（ID / 版本 / 作用域 / Provider / 哈希）全部收进这里，
 * 普通阅读路径上不再出现。
 */
function TechnicalDetailDialog({
  memoryId,
  workspaceId,
  onClose,
}: {
  memoryId: string | null
  workspaceId: string
  onClose: () => void
}) {
  const memory = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'detail', memoryId),
    queryFn: () => getMemory(memoryId ?? ''),
    enabled: Boolean(memoryId),
  })
  const metadata = memory.data
    ? [
        ['lg_memory_id', memory.data.lg_memory_id],
        ['revision', String(memory.data.revision)],
        ['zone', memory.data.zone],
        ['scope', memory.data.session_id ?? memory.data.namespace],
        ['record_kind', memory.data.record_kind],
        ['provider', memory.data.provider_id],
        ['sha256', memory.data.content_hash],
      ]
    : []

  return (
    <Dialog onOpenChange={(open) => !open && onClose()} open={Boolean(memoryId)}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{memory.data?.title ?? '记忆详情'}</DialogTitle>
          <DialogDescription>技术详情与治理操作</DialogDescription>
        </DialogHeader>
        {memory.isPending ? (
          <LoadingState />
        ) : memory.isError ? (
          <ErrorState message={memory.error.message} />
        ) : memory.data ? (
          <div className="space-y-4">
            <dl className="grid gap-x-4 gap-y-2 rounded-xl bg-muted/45 p-4 text-xs sm:grid-cols-[120px_1fr]">
              {metadata.map(([key, value]) => (
                <div className="contents" key={key}>
                  <dt className="text-muted-foreground">{key}</dt>
                  <dd className="break-all font-mono">{value}</dd>
                </div>
              ))}
            </dl>
            <pre className="max-h-[42vh] overflow-auto whitespace-pre-wrap rounded-xl border p-4 font-mono text-xs leading-6">
              {memory.data.content ?? '正文不可用'}
            </pre>
            <MemoryGovernancePanel memory={memory.data} />
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
