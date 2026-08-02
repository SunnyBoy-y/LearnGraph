import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Pencil, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import {
  forgetMemory,
  listMemories,
  recordMemoryFeedback,
  supersedeMemory,
} from '@/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  SectionHeading,
  StatePill,
  Surface,
} from '@/components/shared/page-elements'
import type { MemoryEntry } from '@/types/memory'

type PendingFilter = 'needs_confirmation' | 'disputed' | 'inferred'

const FILTER_LABELS: Array<{ key: PendingFilter; label: string; hint: string }> = [
  { key: 'needs_confirmation', label: '待确认', hint: 'lifecycle_status = needs_confirmation' },
  { key: 'disputed', label: '冲突', hint: 'lifecycle_status = disputed' },
  { key: 'inferred', label: '推断', hint: 'assertion_type = inferred' },
]

function matchesFilter(item: MemoryEntry, filter: PendingFilter): boolean {
  if (filter === 'inferred') return item.assertion_type === 'inferred'
  return item.lifecycle_status === filter
}

function MemoryPendingRow({ item }: { item: MemoryEntry }) {
  const queryClient = useQueryClient()
  const [supersedeOpen, setSupersedeOpen] = useState(false)
  const [forgetOpen, setForgetOpen] = useState(false)
  const [replacementTitle, setReplacementTitle] = useState('')
  const [replacementContent, setReplacementContent] = useState('')
  const [confirmation, setConfirmation] = useState('')

  async function refreshAll() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['memory'] }),
      queryClient.invalidateQueries({ queryKey: ['memory-detail', item.id] }),
      queryClient.invalidateQueries({ queryKey: ['memory-pending'] }),
    ])
  }

  const confirm = useMutation({
    mutationFn: () => recordMemoryFeedback(item.id, { feedback_type: 'correct' }),
    onSuccess: async () => {
      toast.success('已确认该记忆，转为 active')
      await refreshAll()
    },
    onError: (error) => toast.error(error.message),
  })

  const supersede = useMutation({
    mutationFn: () =>
      supersedeMemory(item.id, {
        replacement_title: replacementTitle.trim(),
        replacement_content: replacementContent.trim(),
        reason: 'user_correction',
      }),
    onSuccess: async () => {
      toast.success('已记录纠正并提交替代内容')
      setSupersedeOpen(false)
      setReplacementTitle('')
      setReplacementContent('')
      await refreshAll()
    },
    onError: (error) => toast.error(error.message),
  })

  const forget = useMutation({
    mutationFn: () => forgetMemory(item.id, { confirmation: confirmation.trim() }),
    onSuccess: async () => {
      toast.success('已永久忘记；外部投影将在后台持续清理')
      setForgetOpen(false)
      setConfirmation('')
      await refreshAll()
    },
    onError: (error) => toast.error(error.message),
  })

  return (
    <div className="rounded-xl border bg-background/80 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-semibold">{item.title}</p>
        <StatePill status={item.lifecycle_status ?? 'active'} />
        {item.assertion_type ? <Badge variant="outline">{item.assertion_type}</Badge> : null}
        {item.audience_type ? <Badge variant="secondary">受众：{item.audience_type}</Badge> : null}
      </div>
      {item.content ? (
        <p className="mt-2 line-clamp-3 text-sm leading-6 text-muted-foreground">{item.content}</p>
      ) : null}
      <p className="mt-2 font-mono text-[10px] text-muted-foreground">
        rev {item.revision} · {item.lg_memory_id}
        {typeof item.confidence === 'number' ? ` · confidence ${item.confidence.toFixed(2)}` : ''}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3">
        <Button
          disabled={confirm.isPending}
          onClick={() => confirm.mutate()}
          size="xs"
          variant="outline"
        >
          <CheckCircle2 className="size-3.5" />确认正确
        </Button>
        <Button onClick={() => setSupersedeOpen(true)} size="xs" variant="outline">
          <Pencil className="size-3" />纠正/替代
        </Button>
        <Button onClick={() => setForgetOpen(true)} size="xs" variant="ghost">
          <Trash2 className="size-3.5 text-destructive" />不应保存
        </Button>
      </div>

      <Dialog onOpenChange={setSupersedeOpen} open={supersedeOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>纠正「{item.title}」</DialogTitle>
            <DialogDescription>
              提交替代内容后，旧记忆标记 superseded，新记忆成为当前有效版本；历史不会被静默覆盖。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-2">
              <Label htmlFor={`supersede-title-${item.id}`}>替代标题</Label>
              <Input
                id={`supersede-title-${item.id}`}
                onChange={(event) => setReplacementTitle(event.target.value)}
                value={replacementTitle}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`supersede-content-${item.id}`}>替代内容</Label>
              <Textarea
                className="min-h-32"
                id={`supersede-content-${item.id}`}
                onChange={(event) => setReplacementContent(event.target.value)}
                value={replacementContent}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              disabled={
                supersede.isPending
                || !replacementTitle.trim()
                || !replacementContent.trim()
              }
              onClick={() => supersede.mutate()}
            >
              {supersede.isPending ? '提交中…' : '提交纠正'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog onOpenChange={setForgetOpen} open={forgetOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>永久忘记「{item.title}」？</DialogTitle>
            <DialogDescription>
              将销毁事件密钥并清除检索、向量、关系与上下文投影，无法恢复。请输入“{item.title}”确认。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Input
              aria-label="永久忘记确认标题"
              onChange={(event) => setConfirmation(event.target.value)}
              placeholder={item.title}
              value={confirmation}
            />
          </div>
          <DialogFooter>
            <Button
              disabled={forget.isPending || confirmation.trim() !== item.title.trim()}
              onClick={() => forget.mutate()}
              variant="destructive"
            >
              {forget.isPending ? '清理中…' : '永久忘记'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export function MemoryPendingConfirmationPanel() {
  const [filter, setFilter] = useState<PendingFilter>('needs_confirmation')
  const query = useQuery({
    queryKey: ['memory-pending'],
    queryFn: () => listMemories({ state: 'active', include_content: true }),
  })

  const filtered = useMemo(() => {
    const items = query.data ?? []
    return items.filter((item) => matchesFilter(item, filter))
  }, [query.data, filter])

  const counts = useMemo(() => {
    const items = query.data ?? []
    return {
      needs_confirmation: items.filter((item) => matchesFilter(item, 'needs_confirmation')).length,
      disputed: items.filter((item) => matchesFilter(item, 'disputed')).length,
      inferred: items.filter((item) => matchesFilter(item, 'inferred')).length,
    }
  }, [query.data])

  return (
    <Surface className="p-5">
      <SectionHeading
        description="推断、冲突与待确认记忆不会自动作为高置信事实注入。确认后转 active，纠正后建立替代关系，不应保存则永久忘记。"
        title="待确认记忆"
      />
      <div className="mt-4 flex flex-wrap gap-2 border-t pt-4">
        {FILTER_LABELS.map((option) => (
          <Button
            key={option.key}
            onClick={() => setFilter(option.key)}
            size="sm"
            variant={filter === option.key ? 'default' : 'outline'}
          >
            {option.label}
            <span className="ml-1.5 font-mono text-[10px] text-muted-foreground tabular-nums">
              {counts[option.key]}
            </span>
          </Button>
        ))}
      </div>
      <div className="mt-3 space-y-3">
        {query.isPending ? (
          <LoadingState />
        ) : query.isError ? (
          <ErrorState message={query.error.message} />
        ) : filtered.length ? (
          filtered.map((item) => <MemoryPendingRow item={item} key={item.id} />)
        ) : (
          <EmptyState
            description={`当前没有 ${FILTER_LABELS.find((option) => option.key === filter)?.hint} 记忆。`}
            title="暂无待处理记忆"
          />
        )}
      </div>
    </Surface>
  )
}
