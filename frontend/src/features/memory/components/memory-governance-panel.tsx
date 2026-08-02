import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  History,
  Link2,
  Pencil,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react'
import { toast } from 'sonner'

import {
  forgetMemory,
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
import type { MemoryEntry } from '@/types/memory'

const FORGET_CLEANUP_TARGETS = [
  '结构化投影（memory_item）',
  '全文检索 / FTS 投影',
  'Episode 派生字段',
  'Task State 派生字段',
  'Embedding / 向量投影',
  '关系边（memory_relation）',
  'Context 缓存与访问派生记录',
  '外部 Provider 投影',
  '导出可见性与事件密钥销毁',
]

function SourceUsageSection({ memory }: { memory: MemoryEntry }) {
  const sourceIds = memory.source_ids ?? []
  const headEventId = memory.head_event_id ?? null
  const accessCount = memory.access_count ?? 0
  const successCount = memory.successful_use_count ?? 0
  const lastAccessed = memory.last_accessed_at
  return (
    <div className="space-y-2 rounded-lg border bg-muted/15 p-3 text-xs">
      <p className="flex items-center gap-2 font-semibold">
        <Link2 className="size-3.5" />来源与使用记录
      </p>
      <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[110px_1fr]">
        <dt className="text-muted-foreground">来源 IDs</dt>
        <dd className="break-all font-mono text-[10px]">
          {sourceIds.length ? sourceIds.join(', ') : '—'}
        </dd>
        <dt className="text-muted-foreground">head event</dt>
        <dd className="break-all font-mono text-[10px]">{headEventId ?? '—'}</dd>
        <dt className="text-muted-foreground">访问次数</dt>
        <dd className="font-mono text-[10px]">{accessCount}</dd>
        <dt className="text-muted-foreground">成功使用</dt>
        <dd className="font-mono text-[10px]">{successCount}</dd>
        <dt className="text-muted-foreground">最近访问</dt>
        <dd className="font-mono text-[10px]">
          {lastAccessed ? new Date(lastAccessed).toLocaleString() : '—'}
        </dd>
      </dl>
      <p className="text-[10px] text-muted-foreground">
        使用记录只展示 ID、时间与计数，不泄露原始 Prompt 正文。
      </p>
    </div>
  )
}

function ScopeAudienceSection({ memory }: { memory: MemoryEntry }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      <div className="rounded-lg border bg-muted/15 p-3 text-xs">
        <p className="flex items-center gap-2 font-semibold">
          <ShieldCheck className="size-3.5" />Audience（受众）
        </p>
        <p className="mt-1 font-mono text-[10px]">{memory.audience_type ?? 'workspace'}</p>
        <p className="mt-1 text-[10px] text-muted-foreground">控制对其他用户/子 Agent 的可见性。</p>
      </div>
      <div className="rounded-lg border bg-muted/15 p-3 text-xs">
        <p className="flex items-center gap-2 font-semibold">
          <History className="size-3.5" />Context Scope（检索作用域）
        </p>
        <p className="mt-1 font-mono text-[10px]">
          {memory.scope_type ?? 'workspace'}
          {memory.scope_id ? ` / ${memory.scope_id}` : ''}
        </p>
        <p className="mt-1 text-[10px] text-muted-foreground">检索时作用域硬过滤维度，与受众独立。</p>
      </div>
    </div>
  )
}

export function MemoryGovernancePanel({ memory }: { memory: MemoryEntry }) {
  const queryClient = useQueryClient()
  const [confirmation, setConfirmation] = useState('')
  const [supersedeOpen, setSupersedeOpen] = useState(false)
  const [replacementTitle, setReplacementTitle] = useState('')
  const [replacementContent, setReplacementContent] = useState('')

  async function refreshAll() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['memory'] }),
      queryClient.invalidateQueries({ queryKey: ['memory-detail', memory.id] }),
      queryClient.invalidateQueries({ queryKey: ['memory-pending'] }),
    ])
  }

  const feedback = useMutation({
    mutationFn: (
      feedbackType:
        | 'correct'
        | 'stale'
        | 'project_only'
        | 'durable'
        | 'deny_child'
        | 'suppress_auto_recall',
    ) => recordMemoryFeedback(memory.id, { feedback_type: feedbackType }),
    onSuccess: async () => {
      toast.success('记忆治理策略已更新')
      await refreshAll()
    },
    onError: (error) => toast.error(error.message),
  })

  const supersede = useMutation({
    mutationFn: () =>
      supersedeMemory(memory.id, {
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
    mutationFn: () => forgetMemory(memory.id, { confirmation: confirmation.trim() }),
    onSuccess: async () => {
      toast.success('已永久忘记；外部投影将在后台持续清理')
      await refreshAll()
    },
    onError: (error) => toast.error(error.message),
  })

  return (
    <div className="space-y-3 rounded-xl border border-border/70 bg-muted/20 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <ShieldCheck className="size-4" />
        <strong className="text-sm">记忆治理</strong>
        <Badge variant="outline">受众：{memory.audience_type ?? 'workspace'}</Badge>
        <Badge variant="outline">{memory.sensitivity ?? 'normal'}</Badge>
        <Badge variant="outline">{memory.lifecycle_status ?? 'active'}</Badge>
        {memory.auto_recall_suppressed ? <Badge variant="secondary">已停止自动召回</Badge> : null}
        {memory.child_agent_denied ? <Badge variant="secondary">子 Agent 不可见</Badge> : null}
      </div>

      <ScopeAudienceSection memory={memory} />

      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => feedback.mutate('correct')}>
          <CheckCircle2 className="size-3.5" />确认正确
        </Button>
        <Button size="sm" variant="outline" onClick={() => feedback.mutate('stale')}>
          已过时
        </Button>
        <Button size="sm" variant="outline" onClick={() => setSupersedeOpen(true)}>
          <Pencil className="size-3.5" />记错了（纠正/替代）
        </Button>
        <Button size="sm" variant="outline" onClick={() => feedback.mutate('durable')}>
          永久记住
        </Button>
        <Button size="sm" variant="outline" onClick={() => feedback.mutate('project_only')}>
          只在本项目
        </Button>
        <Button size="sm" variant="outline" onClick={() => feedback.mutate('deny_child')}>
          不给子 Agent
        </Button>
        <Button size="sm" variant="outline" onClick={() => feedback.mutate('suppress_auto_recall')}>
          停止自动召回
        </Button>
      </div>

      <SourceUsageSection memory={memory} />

      <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3">
        <p className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
          <TriangleAlert className="size-4 text-destructive" />
          永久忘记会销毁事件密钥并清除以下投影，无法恢复。请输入“{memory.title}”确认。
        </p>
        <ul className="mb-3 grid gap-1 text-[10px] text-muted-foreground sm:grid-cols-2">
          {FORGET_CLEANUP_TARGETS.map((target) => (
            <li className="flex items-start gap-1" key={target}>
              <AlertTriangle className="mt-0.5 size-3 text-destructive/70" />
              {target}
            </li>
          ))}
        </ul>
        <p className="mb-2 text-[10px] text-amber-700 dark:text-amber-300">
          外部 Provider 投影删除若失败会持续后台重试；在清理完成前不会提示“成功”。
        </p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Input
            aria-label="永久忘记确认标题"
            onChange={(event) => setConfirmation(event.target.value)}
            placeholder={memory.title}
            value={confirmation}
          />
          <Button
            size="sm"
            variant="destructive"
            disabled={forget.isPending || confirmation.trim() !== memory.title.trim()}
            onClick={() => forget.mutate()}
          >
            永久忘记
          </Button>
        </div>
      </div>

      <Dialog onOpenChange={setSupersedeOpen} open={supersedeOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>纠正「{memory.title}」</DialogTitle>
            <DialogDescription>
              提交替代内容后，旧记忆标记 superseded，新记忆成为当前有效版本；历史不会被静默覆盖。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <div className="space-y-2">
              <Label htmlFor="governance-supersede-title">替代标题</Label>
              <Input
                id="governance-supersede-title"
                onChange={(event) => setReplacementTitle(event.target.value)}
                value={replacementTitle}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="governance-supersede-content">替代内容</Label>
              <Textarea
                className="min-h-32"
                id="governance-supersede-content"
                onChange={(event) => setReplacementContent(event.target.value)}
                value={replacementContent}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              disabled={
                supersede.isPending || !replacementTitle.trim() || !replacementContent.trim()
              }
              onClick={() => supersede.mutate()}
            >
              {supersede.isPending ? '提交中…' : '提交纠正'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
