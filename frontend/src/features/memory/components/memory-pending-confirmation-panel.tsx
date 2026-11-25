import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Pencil, Trash2, XCircle } from 'lucide-react'
import { toast } from '@/lib/notifications'

import {
  batchRecordMemoryFeedback,
  batchRetractMemories,
  decideMemoryDraft,
  decideMemoryDraftsBatch,
  forgetMemory,
  listMemoryDrafts,
  listMemories,
  recordMemoryFeedback,
  retractMemory,
  supersedeMemory,
} from '@/api'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
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
  Surface,
} from '@/components/shared/page-elements'
import type { MemoryDraft, MemoryEntry } from '@/types/memory'
import type { Session } from '@/types/sessions'
import { memoryRelativeTime } from '../memory-display'
import { matchesPendingGroup, usePendingMemorySummary } from '../use-pending-memory-summary'

type PendingGroup = 'drafts' | 'needs_confirmation' | 'disputed' | 'inferred'

const GROUP_LABELS: Array<{ key: PendingGroup; label: string; hint: string }> = [
  {
    key: 'drafts',
    label: 'AI 的新理解',
    hint: 'AI 刚从对话与学习记录里整理出来，还没有写入长期记忆。',
  },
  {
    key: 'needs_confirmation',
    label: '等你确认',
    hint: '需要你确认之后，才会作为事实用在后续对话里。',
  },
  {
    key: 'disputed',
    label: '说法不一致',
    hint: '与已有记忆互相矛盾，需要你裁定哪一种更准确。',
  },
  {
    key: 'inferred',
    label: 'AI 的推测',
    hint: '由 AI 推测得到，证据还不够充分，建议确认或纠正。',
  },
]

const DRAFT_OPERATION_LABELS: Record<string, string> = {
  CREATE: '新增',
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

function sourceLine(
  createdAt: string | null | undefined,
  sessionId: string | null | undefined,
  sessions: Session[],
): string {
  const relative = memoryRelativeTime(createdAt)
  const session = sessionId ? sessions.find((item) => item.id === sessionId) : undefined
  const origin = session?.title ? `来自「${session.title}」的对话` : '来自工作区学习记录'
  return relative ? `来源：${origin} · ${relative}` : `来源：${origin}`
}

function firstSentence(text: string | null | undefined, fallback: string): string {
  const lines = (text ?? '')
    .split('\n')
    .map((line) => line.replace(/^[#>*\-\s]+/, '').trim())
    .filter(Boolean)
  return lines[0] || fallback
}

async function invalidatePendingMemoryQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  extraMemoryId?: string,
) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ['memory'] }),
    queryClient.invalidateQueries({ queryKey: ['memory-pending'] }),
    queryClient.invalidateQueries({ queryKey: ['memory-drafts'] }),
    queryClient.invalidateQueries({
      predicate: (query) =>
        Array.isArray(query.queryKey) &&
        query.queryKey[0] === 'workspace' &&
        query.queryKey[2] === 'memory',
    }),
    ...(extraMemoryId
      ? [queryClient.invalidateQueries({ queryKey: ['memory-detail', extraMemoryId] })]
      : []),
  ])
}

function DraftCard({
  draft,
  sessions,
  selected,
  selectionDisabled = false,
  onSelect,
  onResolved,
}: {
  draft: MemoryDraft
  sessions: Session[]
  selected: boolean
  selectionDisabled?: boolean
  onSelect: (checked: boolean) => void
  onResolved?: (id: string) => void
}) {
  const queryClient = useQueryClient()
  const [rejectOpen, setRejectOpen] = useState(false)
  const [rejectionReason, setRejectionReason] = useState('')
  const sentence = firstSentence(draft.content, draft.title || draft.memory_type)

  const commit = useMutation({
    mutationFn: () => decideMemoryDraft(draft.id, { decision: 'commit', reason: 'user_confirmed' }),
    onSuccess: async () => {
      toast.success('已记住这条理解')
      onResolved?.(draft.id)
      await invalidatePendingMemoryQueries(queryClient)
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
      toast.success('已丢弃这条理解，不会写入长期记忆')
      setRejectOpen(false)
      setRejectionReason('')
      onResolved?.(draft.id)
      await invalidatePendingMemoryQueries(queryClient)
    },
    onError: (error) => toast.error(error.message),
  })

  const cardBusy = selectionDisabled || commit.isPending || reject.isPending

  return (
    <div
      className={`rounded-xl border px-4 py-3.5 transition-colors ${
        selected ? 'border-primary/40 bg-primary/5' : 'bg-background/70'
      }`}
    >
      <div className="flex items-start gap-3">
        <Checkbox
          aria-label={`选择理解：${sentence}`}
          checked={selected}
          className="mt-1"
          disabled={cardBusy}
          onCheckedChange={(checked) => onSelect(checked === true)}
        />
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-6">{sentence}</p>
          <p className="mt-2 text-xs text-muted-foreground">
            {sourceLine(draft.created_at, draft.session_id, sessions)}
            {DRAFT_OPERATION_LABELS[draft.operation]
              ? ` · ${DRAFT_OPERATION_LABELS[draft.operation]}`
              : ''}
          </p>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t pt-3">
        <Button
          disabled={cardBusy}
          onClick={() => commit.mutate()}
          size="sm"
          variant="outline"
        >
          <CheckCircle2 className="size-3.5" />是的，记住
        </Button>
        <Button
          disabled={cardBusy}
          onClick={() => setRejectOpen(true)}
          size="sm"
          variant="ghost"
        >
          <XCircle className="size-3.5" />不是这样
        </Button>
      </div>

      <Dialog onOpenChange={setRejectOpen} open={rejectOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>这条理解不对？</DialogTitle>
            <DialogDescription>
              丢弃后不会写入长期记忆，也不会影响已经记住的内容。可以补充一句说明，方便之后核对。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label htmlFor={`draft-reject-reason-${draft.id}`}>补充说明（可选）</Label>
            <Input
              id={`draft-reject-reason-${draft.id}`}
              onChange={(event) => setRejectionReason(event.target.value)}
              placeholder="例如：这条说的是以前的情况"
              value={rejectionReason}
            />
          </div>
          <DialogFooter>
            <Button disabled={reject.isPending} onClick={() => reject.mutate()} variant="destructive">
              {reject.isPending ? '处理中…' : '确定，不是这样'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function PendingMemoryCard({
  item,
  sessions,
  selected,
  selectionDisabled = false,
  onSelect,
  onResolved,
  onCorrect,
}: {
  item: MemoryEntry
  sessions: Session[]
  selected: boolean
  selectionDisabled?: boolean
  onSelect: (checked: boolean) => void
  onResolved?: (id: string) => void
  onCorrect: (text: string) => void
}) {
  const queryClient = useQueryClient()
  const [supersedeOpen, setSupersedeOpen] = useState(false)
  const [forgetOpen, setForgetOpen] = useState(false)
  const [retractOpen, setRetractOpen] = useState(false)
  const [replacementTitle, setReplacementTitle] = useState('')
  const [replacementContent, setReplacementContent] = useState('')
  const [forgetConfirmation, setForgetConfirmation] = useState('')
  const [retractReason, setRetractReason] = useState('')
  const sentence = firstSentence(item.content, item.title)

  const confirm = useMutation({
    mutationFn: () => recordMemoryFeedback(item.id, { feedback_type: 'correct' }),
    onSuccess: async () => {
      toast.success('已记住这条理解')
      onResolved?.(item.id)
      await invalidatePendingMemoryQueries(queryClient, item.id)
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
      toast.success('已用你的说法替换这条理解，历史仍然保留')
      setSupersedeOpen(false)
      setReplacementTitle('')
      setReplacementContent('')
      onResolved?.(item.id)
      await invalidatePendingMemoryQueries(queryClient, item.id)
    },
    onError: (error) => toast.error(error.message),
  })

  const retract = useMutation({
    mutationFn: () => retractMemory(item.id, retractReason.trim() || 'user_says_incorrect'),
    onSuccess: async () => {
      toast.success('已停用这条理解，后续对话不会再使用它')
      setRetractOpen(false)
      setRetractReason('')
      onResolved?.(item.id)
      await invalidatePendingMemoryQueries(queryClient, item.id)
    },
    onError: (error) => toast.error(error.message),
  })

  const forget = useMutation({
    mutationFn: () => forgetMemory(item.id, { confirmation: forgetConfirmation.trim() }),
    onSuccess: async () => {
      toast.success('已永久忘记；外部投影将在后台持续清理')
      setForgetOpen(false)
      setForgetConfirmation('')
      onResolved?.(item.id)
      await invalidatePendingMemoryQueries(queryClient, item.id)
    },
    onError: (error) => toast.error(error.message),
  })

  const cardBusy =
    selectionDisabled ||
    confirm.isPending ||
    supersede.isPending ||
    retract.isPending ||
    forget.isPending

  return (
    <div
      className={`rounded-xl border px-4 py-3.5 transition-colors ${
        selected ? 'border-primary/40 bg-primary/5' : 'bg-background/70'
      }`}
    >
      <div className="flex items-start gap-3">
        <Checkbox
          aria-label={`选择理解：${sentence}`}
          checked={selected}
          className="mt-1"
          disabled={cardBusy}
          onCheckedChange={(checked) => onSelect(checked === true)}
        />
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-6">{sentence}</p>
          <p className="mt-2 text-xs text-muted-foreground">
            {sourceLine(item.updated_at ?? item.created_at, item.session_id, sessions)}
          </p>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t pt-3">
        <Button
          disabled={cardBusy}
          onClick={() => confirm.mutate()}
          size="sm"
          variant="outline"
        >
          <CheckCircle2 className="size-3.5" />是的，记住
        </Button>
        <Button
          disabled={cardBusy}
          onClick={() => setSupersedeOpen(true)}
          size="sm"
          variant="ghost"
        >
          <Pencil className="size-3" />修改
        </Button>
        <Button
          disabled={cardBusy}
          onClick={() => setRetractOpen(true)}
          size="sm"
          variant="ghost"
        >
          <XCircle className="size-3.5" />不是这样
        </Button>
        <Button
          aria-label="永久忘记这条记忆"
          className="ml-auto"
          disabled={cardBusy}
          onClick={() => setForgetOpen(true)}
          size="icon-xs"
          variant="ghost"
        >
          <Trash2 className="size-3.5 text-destructive" />
        </Button>
      </div>

      <Dialog onOpenChange={setSupersedeOpen} open={supersedeOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>修改这条理解</DialogTitle>
            <DialogDescription>
              提交你的说法后，这条理解会被标记为已被取代，新的说法成为当前版本；历史不会被静默覆盖。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-2">
              <Label htmlFor={`supersede-title-${item.id}`}>新的标题</Label>
              <Input
                id={`supersede-title-${item.id}`}
                onChange={(event) => setReplacementTitle(event.target.value)}
                value={replacementTitle}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor={`supersede-content-${item.id}`}>新的说法</Label>
              <Textarea
                className="min-h-32"
                id={`supersede-content-${item.id}`}
                onChange={(event) => setReplacementContent(event.target.value)}
                value={replacementContent}
              />
            </div>
            <p className="text-xs text-muted-foreground">
              如果需要说清来龙去脉，也可以
              <button
                className="mx-1 text-primary underline-offset-4 hover:underline"
                onClick={() => {
                  setSupersedeOpen(false)
                  onCorrect(item.title)
                }}
                type="button"
              >
                回到报告页用自然语言说明
              </button>
              。
            </p>
          </div>
          <DialogFooter>
            <Button
              disabled={
                supersede.isPending || !replacementTitle.trim() || !replacementContent.trim()
              }
              onClick={() => supersede.mutate()}
            >
              {supersede.isPending ? '提交中…' : '提交修改'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog onOpenChange={setRetractOpen} open={retractOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>这条理解不对？</DialogTitle>
            <DialogDescription>
              停用后它不会再参与对话，但记录仍然保留在历史里，之后可以重新启用。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label htmlFor={`retract-reason-${item.id}`}>补充说明（可选）</Label>
            <Input
              id={`retract-reason-${item.id}`}
              onChange={(event) => setRetractReason(event.target.value)}
              placeholder="例如：我已经换方向了"
              value={retractReason}
            />
          </div>
          <DialogFooter>
            <Button disabled={retract.isPending} onClick={() => retract.mutate()} variant="destructive">
              {retract.isPending ? '处理中…' : '确定，不是这样'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog onOpenChange={setForgetOpen} open={forgetOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>永久忘记这条记忆？</DialogTitle>
            <DialogDescription>
              会销毁事件密钥并清除检索、向量、关系与上下文投影，无法恢复。请输入“{item.title}”确认。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Input
              aria-label="永久忘记确认标题"
              onChange={(event) => setForgetConfirmation(event.target.value)}
              placeholder={item.title}
              value={forgetConfirmation}
            />
          </div>
          <DialogFooter>
            <Button
              disabled={forget.isPending || forgetConfirmation.trim() !== item.title.trim()}
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

export function MemoryPendingConfirmationPanel({
  sessions = [],
  onRequestCorrection,
}: {
  sessions?: Session[]
  onRequestCorrection?: (text: string) => void
}) {
  const queryClient = useQueryClient()
  const [group, setGroup] = useState<PendingGroup>('drafts')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [batchRejectOpen, setBatchRejectOpen] = useState(false)
  const [batchRejectionReason, setBatchRejectionReason] = useState('')
  const { counts, total } = usePendingMemorySummary()
  const active = useQuery({
    queryKey: ['memory-pending'],
    queryFn: () => listMemories({ state: 'active', include_content: true }),
  })
  const drafts = useQuery({
    queryKey: ['memory-drafts'],
    queryFn: () => listMemoryDrafts({ status: 'PENDING' }),
  })

  const items =
    group === 'drafts'
      ? (drafts.data ?? [])
      : (active.data ?? []).filter((item) => matchesPendingGroup(item, group))
  const isPending = group === 'drafts' ? drafts.isPending : active.isPending
  const isError = group === 'drafts' ? drafts.isError : active.isError
  const errorMessage = (group === 'drafts' ? drafts.error : active.error)?.message ?? ''
  const hint = GROUP_LABELS.find((option) => option.key === group)?.hint ?? ''

  const visibleIds = items.map((item) => item.id)
  const selectedVisibleIds = visibleIds.filter((id) => selectedIds.has(id))
  const selectedCount = selectedVisibleIds.length

  function handleGroupChange(nextGroup: PendingGroup) {
    setGroup(nextGroup)
    setSelectedIds(new Set())
    setBatchRejectOpen(false)
    setBatchRejectionReason('')
  }

  function selectAllVisible(checked: boolean) {
    setSelectedIds(checked ? new Set(visibleIds) : new Set())
  }

  function toggleItem(id: string, checked: boolean) {
    setSelectedIds((previous) => {
      const next = new Set(previous)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  function removeFromSelection(id: string) {
    setSelectedIds((previous) => {
      if (!previous.has(id)) return previous
      const next = new Set(previous)
      next.delete(id)
      return next
    })
  }

  const batchConfirm = useMutation({
    mutationFn: async (ids: string[]) => {
      if (group === 'drafts') {
        return decideMemoryDraftsBatch({
          draft_ids: ids,
          decision: 'commit',
          reason: 'user_confirmed',
        })
      }
      return batchRecordMemoryFeedback({
        memory_ids: ids,
        feedback_type: 'correct',
      })
    },
    onSuccess: async (_result, ids) => {
      toast.success(`已记住 ${ids.length} 条理解`)
      setSelectedIds((previous) => new Set([...previous].filter((id) => !ids.includes(id))))
      await invalidatePendingMemoryQueries(queryClient)
    },
    onError: (error) => toast.error(error.message),
  })

  const batchReject = useMutation({
    mutationFn: async (ids: string[]) => {
      const reason = batchRejectionReason.trim()
      if (group === 'drafts') {
        return decideMemoryDraftsBatch({
          draft_ids: ids,
          decision: 'reject',
          reason: reason || 'user_rejected',
        })
      }
      return batchRetractMemories(ids, reason || 'user_says_incorrect')
    },
    onSuccess: async (_result, ids) => {
      toast.success(
        group === 'drafts'
          ? `已丢弃 ${ids.length} 条理解，不会写入长期记忆`
          : `已停用 ${ids.length} 条理解，后续对话不会再使用它们`,
      )
      setSelectedIds((previous) => new Set([...previous].filter((id) => !ids.includes(id))))
      setBatchRejectOpen(false)
      setBatchRejectionReason('')
      await invalidatePendingMemoryQueries(queryClient)
    },
    onError: (error) => toast.error(error.message),
  })

  const batchBusy = batchConfirm.isPending || batchReject.isPending

  return (
    <Surface className="p-5">
      <SectionHeading
        description="AI 最近形成了这些理解，请告诉我是否准确。还没有确认的内容不会作为高置信事实出现在报告与对话里。"
        title="AI 最近形成的理解"
      />
      <div className="mt-4 flex flex-wrap gap-1.5 border-t pt-4">
        {GROUP_LABELS.map((option) => (
          <Button
            key={option.key}
            onClick={() => handleGroupChange(option.key)}
            size="sm"
            variant={group === option.key ? 'default' : 'outline'}
          >
            {option.label}
            <span className="ml-1.5 font-mono text-[10px] tabular-nums">
              {counts[option.key]}
            </span>
          </Button>
        ))}
      </div>

      {!isPending && !isError && items.length ? (
        <div
          aria-label="批量操作"
          className="mt-3 flex flex-wrap items-center gap-3 rounded-xl border bg-muted/20 px-4 py-2.5"
          role="region"
        >
          <label className="flex items-center gap-2.5 text-sm">
            <Checkbox
              aria-label="全选当前列表"
              checked={
                selectedCount > 0 && selectedCount < items.length
                  ? 'indeterminate'
                  : items.length > 0 && selectedCount === items.length
              }
              disabled={batchBusy}
              onCheckedChange={(checked) => selectAllVisible(checked === true)}
            />
            全选当前列表
          </label>
          <span aria-live="polite" className="text-xs text-muted-foreground">
            已选 {selectedCount} 条
          </span>
          {selectedCount ? (
            <Button
              disabled={batchBusy}
              onClick={() => setSelectedIds(new Set())}
              size="sm"
              variant="ghost"
            >
              取消选择
            </Button>
          ) : null}
          <div className="ml-auto flex flex-wrap items-center gap-1.5">
            <Button
              disabled={batchBusy || !selectedCount}
              onClick={() => batchConfirm.mutate(selectedVisibleIds)}
              size="sm"
              variant="outline"
            >
              <CheckCircle2 className="size-3.5" />
              {batchConfirm.isPending ? '处理中…' : '是的，记住'}
            </Button>
            <Button
              disabled={batchBusy || !selectedCount}
              onClick={() => setBatchRejectOpen(true)}
              size="sm"
              variant="ghost"
            >
              <XCircle className="size-3.5" />不是这样
            </Button>
          </div>
        </div>
      ) : null}

      <div className="mt-3 space-y-3">
        {isPending ? (
          <LoadingState />
        ) : isError ? (
          <ErrorState message={errorMessage} />
        ) : items.length ? (
          group === 'drafts' ? (
            (items as MemoryDraft[]).map((draft) => (
              <DraftCard
                draft={draft}
                key={draft.id}
                onResolved={removeFromSelection}
                onSelect={(checked) => toggleItem(draft.id, checked)}
                selected={selectedIds.has(draft.id)}
                selectionDisabled={batchBusy}
                sessions={sessions}
              />
            ))
          ) : (
            (items as MemoryEntry[]).map((item) => (
              <PendingMemoryCard
                item={item}
                key={item.id}
                onCorrect={(text) => onRequestCorrection?.(text)}
                onResolved={removeFromSelection}
                onSelect={(checked) => toggleItem(item.id, checked)}
                selected={selectedIds.has(item.id)}
                selectionDisabled={batchBusy}
                sessions={sessions}
              />
            ))
          )
        ) : (
          <EmptyState
            description={total ? `当前这一组没有待处理内容。${hint}` : 'AI 会在学习过程中继续整理，有新的理解时会出现在这里。'}
            title="暂时没有需要你确认的理解"
          />
        )}
      </div>

      <Dialog
        onOpenChange={(open) => {
          if (!batchReject.isPending) setBatchRejectOpen(open)
        }}
        open={batchRejectOpen}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>这 {selectedCount} 条理解不对？</DialogTitle>
            <DialogDescription>
              {group === 'drafts'
                ? '丢弃后不会写入长期记忆，也不会影响已经记住的内容。可以补充一句说明，方便之后核对。'
                : '停用后它们不会再参与对话，但记录仍然保留在历史里，之后可以重新启用。'}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label htmlFor="pending-batch-reject-reason">补充说明（可选）</Label>
            <Input
              id="pending-batch-reject-reason"
              onChange={(event) => setBatchRejectionReason(event.target.value)}
              placeholder={group === 'drafts' ? '例如：这些说的是以前的情况' : '例如：我已经换方向了'}
              value={batchRejectionReason}
            />
          </div>
          <DialogFooter>
            <Button
              disabled={batchReject.isPending}
              onClick={() => batchReject.mutate(selectedVisibleIds)}
              variant="destructive"
            >
              {batchReject.isPending ? '处理中…' : '确定，不是这样'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Surface>
  )
}
