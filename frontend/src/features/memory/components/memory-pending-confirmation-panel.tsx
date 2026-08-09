import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Pencil, Trash2, XCircle } from 'lucide-react'
import { toast } from 'sonner'

import {
  decideMemoryDraft,
  forgetMemory,
  listMemoryDrafts,
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
import type { MemoryDraft, MemoryEntry } from '@/types/memory'

type PendingFilter = 'drafts' | 'needs_confirmation' | 'disputed' | 'inferred'

const FILTER_LABELS: Array<{ key: PendingFilter; label: string; hint: string }> = [
  { key: 'needs_confirmation', label: '待确认', hint: 'lifecycle_status = needs_confirmation' },
  { key: 'disputed', label: '冲突', hint: 'lifecycle_status = disputed' },
  { key: 'inferred', label: '推断', hint: 'assertion_type = inferred' },
]

function matchesFilter(item: MemoryEntry, filter: PendingFilter): boolean {
  if (filter === 'inferred') return item.assertion_type === 'inferred'
  return item.lifecycle_status === filter
}

const OPERATION_LABELS: Record<string, string> = {
  CREATE: '新建',
  UPDATE: '更新',
  CORRECT: '纠正',
  CONFIRM: '确认',
  COMPLETE: '完成',
  CANCEL: '取消',
  RESCHEDULE: '改期',
  MERGE: '合并',
  SUPERSEDE: '取代',
  RETRACT: '撤回',
  PROMOTE: '提升',
  DEMOTE: '降级',
  ARCHIVE: '归档',
}

function MemoryDraftRow({ draft }: { draft: MemoryDraft }) {
  const queryClient = useQueryClient()
  const [rejectOpen, setRejectOpen] = useState(false)
  const [rejectionReason, setRejectionReason] = useState('')

  async function refreshAll() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['memory'] }),
      queryClient.invalidateQueries({ queryKey: ['memory-pending'] }),
      queryClient.invalidateQueries({ queryKey: ['memory-drafts'] }),
    ])
  }

  const commit = useMutation({
    mutationFn: () => decideMemoryDraft(draft.id, { decision: 'commit', reason: 'user_confirmed' }),
    onSuccess: async () => {
      toast.success('已确认草稿并写入正式记忆')
      await refreshAll()
    },
    onError: (error) => toast.error(error.message),
  })

  const reject = useMutation({
    mutationFn: () =>
      decideMemoryDraft(draft.id, {
        decision: 'reject',
        reason: rejectionReason.trim() || 'user_rejected',
      }),
    onSuccess: async () => {
      toast.success('已拒绝该记忆草稿')
      setRejectOpen(false)
      setRejectionReason('')
      await refreshAll()
    },
    onError: (error) => toast.error(error.message),
  })

  return (
    <div className="rounded-xl border border-dashed bg-background/60 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="secondary">草稿</Badge>
        <Badge variant="outline">{OPERATION_LABELS[draft.operation] ?? draft.operation}</Badge>
        <p className="text-sm font-semibold">{draft.title || draft.memory_type}</p>
        <StatePill status={draft.status.toLowerCase()} />
      </div>
      {draft.content ? (
        <p className="mt-2 line-clamp-3 text-sm leading-6 text-muted-foreground">{draft.content}</p>
      ) : null}
      <p className="mt-2 font-mono text-[10px] text-muted-foreground">
        {draft.memory_type}
        {typeof draft.confidence === 'number' ? ` · confidence ${draft.confidence.toFixed(2)}` : ''}
        {draft.created_by ? ` · by ${draft.created_by}` : ''}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3">
        <Button
          disabled={commit.isPending}
          onClick={() => commit.mutate()}
          size="xs"
          variant="outline"
        >
          <CheckCircle2 className="size-3.5" />确认写入
        </Button>
        <Button onClick={() => setRejectOpen(true)} size="xs" variant="ghost">
          <XCircle className="size-3.5 text-destructive" />拒绝
        </Button>
      </div>

      <Dialog onOpenChange={setRejectOpen} open={rejectOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>拒绝记忆草稿「{draft.title || draft.memory_type}」？</DialogTitle>
            <DialogDescription>
              草稿将标记为 REJECTED，不会写入正式记忆。可填写拒绝原因（可选，用于审计）。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label htmlFor={`draft-reject-reason-${draft.id}`}>拒绝原因（可选）</Label>
            <Input
              id={`draft-reject-reason-${draft.id}`}
              onChange={(event) => setRejectionReason(event.target.value)}
              placeholder="例如：信息过时 / 不准确"
              value={rejectionReason}
            />
          </div>
          <DialogFooter>
            <Button
              disabled={reject.isPending}
              onClick={() => reject.mutate()}
              variant="destructive"
            >
              {reject.isPending ? '处理中…' : '拒绝草稿'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
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
  const draftsQuery = useQuery({
    queryKey: ['memory-drafts'],
    queryFn: () => listMemoryDrafts({ status: 'PENDING' }),
  })

  const drafts = draftsQuery.data ?? []

  const filtered = useMemo(() => {
    if (filter === 'drafts') return drafts
    const items = query.data ?? []
    return items.filter((item) => matchesFilter(item, filter))
  }, [query.data, drafts, filter])

  const counts = useMemo(() => {
    const items = query.data ?? []
    return {
      drafts: drafts.length,
      needs_confirmation: items.filter((item) => matchesFilter(item, 'needs_confirmation')).length,
      disputed: items.filter((item) => matchesFilter(item, 'disputed')).length,
      inferred: items.filter((item) => matchesFilter(item, 'inferred')).length,
    }
  }, [query.data, drafts])

  const isPending = filter === 'drafts' ? draftsQuery.isPending : query.isPending
  const isError = filter === 'drafts' ? draftsQuery.isError : query.isError
  const errorMessage = (filter === 'drafts' ? draftsQuery.error : query.error)?.message ?? ''

  const emptyHint =
    filter === 'drafts'
      ? '没有待确认的记忆草稿'
      : `当前没有 ${FILTER_LABELS.find((option) => option.key === filter)?.hint} 记忆。`

  return (
    <Surface className="p-5">
      <SectionHeading
        description="记忆草稿、冲突与待确认记忆不会自动作为高置信事实注入。草稿确认后写入正式记忆，纠正后建立替代关系，不应保存则拒绝/永久忘记。"
        title="待确认记忆"
      />
      <div className="mt-4 flex flex-wrap gap-2 border-t pt-4">
        <Button
          key="drafts"
          onClick={() => setFilter('drafts')}
          size="sm"
          variant={filter === 'drafts' ? 'default' : 'outline'}
        >
          草稿
          <span className="ml-1.5 font-mono text-[10px] text-muted-foreground tabular-nums">
            {counts.drafts}
          </span>
        </Button>
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
        {isPending ? (
          <LoadingState />
        ) : isError ? (
          <ErrorState message={errorMessage} />
        ) : filtered.length ? (
          filter === 'drafts'
            ? (filtered as MemoryDraft[]).map((item) => (
                <MemoryDraftRow draft={item} key={item.id} />
              ))
            : (filtered as MemoryEntry[]).map((item) => <MemoryPendingRow item={item} key={item.id} />)
        ) : (
          <EmptyState description={emptyHint} title="暂无待处理记忆" />
        )}
      </div>
    </Surface>
  )
}
