import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Pencil, Trash2, XCircle } from 'lucide-react'
import { toast } from 'sonner'

import {
  decideMemoryDraft,
  forgetMemory,
  listMemoryDrafts,
  listMemories,
  recordMemoryFeedback,
  retractMemory,
  supersedeMemory,
} from '@/api'
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

function DraftCard({ draft, sessions }: { draft: MemoryDraft; sessions: Session[] }) {
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
      toast.success('已记住这条理解')
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
      toast.success('已丢弃这条理解，不会写入长期记忆')
      setRejectOpen(false)
      setRejectionReason('')
      await refreshAll()
    },
    onError: (error) => toast.error(error.message),
  })

  return (
    <div className="rounded-xl border bg-background/70 px-4 py-3.5">
      <p className="text-sm leading-6">{firstSentence(draft.content, draft.title || draft.memory_type)}</p>
      <p className="mt-2 text-xs text-muted-foreground">
        {sourceLine(draft.created_at, draft.session_id, sessions)}
        {DRAFT_OPERATION_LABELS[draft.operation]
          ? ` · ${DRAFT_OPERATION_LABELS[draft.operation]}`
          : ''}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t pt-3">
        <Button
          disabled={commit.isPending}
          onClick={() => commit.mutate()}
          size="sm"
          variant="outline"
        >
          <CheckCircle2 className="size-3.5" />是的，记住
        </Button>
        <Button onClick={() => setRejectOpen(true)} size="sm" variant="ghost">
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
  onCorrect,
}: {
  item: MemoryEntry
  sessions: Session[]
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
      toast.success('已记住这条理解')
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
      toast.success('已用你的说法替换这条理解，历史仍然保留')
      setSupersedeOpen(false)
      setReplacementTitle('')
      setReplacementContent('')
      await refreshAll()
    },
    onError: (error) => toast.error(error.message),
  })

  const retract = useMutation({
    mutationFn: () => retractMemory(item.id, retractReason.trim() || 'user_says_incorrect'),
    onSuccess: async () => {
      toast.success('已停用这条理解，后续对话不会再使用它')
      setRetractOpen(false)
      setRetractReason('')
      await refreshAll()
    },
    onError: (error) => toast.error(error.message),
  })

  const forget = useMutation({
    mutationFn: () => forgetMemory(item.id, { confirmation: forgetConfirmation.trim() }),
    onSuccess: async () => {
      toast.success('已永久忘记；外部投影将在后台持续清理')
      setForgetOpen(false)
      setForgetConfirmation('')
      await refreshAll()
    },
    onError: (error) => toast.error(error.message),
  })

  return (
    <div className="rounded-xl border bg-background/70 px-4 py-3.5">
      <p className="text-sm leading-6">{firstSentence(item.content, item.title)}</p>
      <p className="mt-2 text-xs text-muted-foreground">
        {sourceLine(item.updated_at ?? item.created_at, item.session_id, sessions)}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t pt-3">
        <Button
          disabled={confirm.isPending}
          onClick={() => confirm.mutate()}
          size="sm"
          variant="outline"
        >
          <CheckCircle2 className="size-3.5" />是的，记住
        </Button>
        <Button onClick={() => setSupersedeOpen(true)} size="sm" variant="ghost">
          <Pencil className="size-3" />修改
        </Button>
        <Button onClick={() => setRetractOpen(true)} size="sm" variant="ghost">
          <XCircle className="size-3.5" />不是这样
        </Button>
        <Button
          aria-label="永久忘记这条记忆"
          className="ml-auto"
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
  const [group, setGroup] = useState<PendingGroup>('drafts')
  const { counts, total } = usePendingMemorySummary()
  const active = useQuery({
    queryKey: ['memory-pending'],
    queryFn: () => listMemories({ state: 'active', include_content: true }),
  })
  const drafts = useQuery({
    queryKey: ['memory-drafts'],
    queryFn: () => listMemoryDrafts({ status: 'PENDING' }),
  })

  const items = group === 'drafts' ? (drafts.data ?? []) : (active.data ?? []).filter((item) => matchesPendingGroup(item, group))
  const isPending = group === 'drafts' ? drafts.isPending : active.isPending
  const isError = group === 'drafts' ? drafts.isError : active.isError
  const errorMessage = (group === 'drafts' ? drafts.error : active.error)?.message ?? ''
  const hint = GROUP_LABELS.find((option) => option.key === group)?.hint ?? ''

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
            onClick={() => setGroup(option.key)}
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
      <div className="mt-3 space-y-3">
        {isPending ? (
          <LoadingState />
        ) : isError ? (
          <ErrorState message={errorMessage} />
        ) : items.length ? (
          group === 'drafts' ? (
            (items as MemoryDraft[]).map((draft) => (
              <DraftCard draft={draft} key={draft.id} sessions={sessions} />
            ))
          ) : (
            (items as MemoryEntry[]).map((item) => (
              <PendingMemoryCard
                item={item}
                key={item.id}
                onCorrect={(text) => onRequestCorrection?.(text)}
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
    </Surface>
  )
}
