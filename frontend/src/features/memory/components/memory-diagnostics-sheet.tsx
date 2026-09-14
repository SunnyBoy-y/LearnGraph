import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Eye, FileClock, FileText, RefreshCw, Sparkles } from 'lucide-react'
import { toast } from 'sonner'

import {
  archiveGoalMemories,
  extractSessionMemories,
  getEffectiveMemoryPackage,
  getGoalMemoryOverview,
  getMemoryPolicy,
  getSessionContextSummary,
  purgeExpiredMemoryContent,
  summarizeSessionContext,
} from '@/api'
import { MessageResponse } from '@/components/ai-elements/message'
import { SessionCombobox } from '@/components/shared/session-combobox'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  SectionHeading,
  Surface,
} from '@/components/shared/page-elements'
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
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { workspaceQueryKey, workspaceResourcePrefix } from '@/lib/query-keys'
import type { Goal } from '@/types/goals'
import type { Session } from '@/types/sessions'
import { ContextManifestPanel } from './context-manifest-panel'
import { MemoryTaskEpisodePanel } from './memory-task-episode-panel'

/**
 * 高级诊断抽屉：原来的「进行中 / 使用记录 / Goal 记忆 / 保留期维护」全部降级到这里。
 * 底层能力一个都没有删除，只是不再占据记忆页首屏。
 */
export function MemoryDiagnosticsSheet({
  goals,
  isSystemAdmin,
  open,
  sessions,
  workspaceId,
  onOpenChange,
}: {
  goals: Goal[]
  isSystemAdmin: boolean
  open: boolean
  sessions: Session[]
  workspaceId: string
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Sheet onOpenChange={onOpenChange} open={open}>
      <SheetContent className="w-full gap-0 overflow-y-auto p-0 sm:max-w-3xl" side="right">
        <SheetHeader className="border-b px-6 py-4">
          <SheetTitle className="text-base">高级诊断</SheetTitle>
          <SheetDescription>
            这里保留记忆系统的全部底层视图：注入预览、真实回执、Task / Episode、Goal 记忆与保留期维护。
            需要在工作区设置中拥有管理权限。
          </SheetDescription>
        </SheetHeader>
        <div className="space-y-4 px-6 py-5">
          <MemoryInjectionPanel sessions={sessions} workspaceId={workspaceId} />
          <Surface className="p-5">
            <SectionHeading
              description="本轮真实提供给模型的记忆回执（候选 / 检索 / 选择 / 注入 / 排除）。"
              title="使用记录"
            />
            <div className="mt-4">
              <ContextManifestPanel />
            </div>
          </Surface>
          <MemoryTaskEpisodePanel />
          <GoalMemoryToolsCard goals={goals} workspaceId={workspaceId} />
          {isSystemAdmin ? <RetentionMaintenanceCard workspaceId={workspaceId} /> : null}
        </div>
      </SheetContent>
    </Sheet>
  )
}

function MemoryInjectionPanel({
  sessions,
  workspaceId,
}: {
  sessions: Session[]
  workspaceId: string
}) {
  const queryClient = useQueryClient()
  const [sessionId, setSessionId] = useState(sessions[0]?.id ?? '')
  const policy = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'policy', sessionId),
    queryFn: () => getMemoryPolicy(sessionId),
    enabled: Boolean(sessionId),
  })
  const preview = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'package', sessionId),
    queryFn: () => getEffectiveMemoryPackage({ session_id: sessionId }),
    enabled: Boolean(sessionId),
  })
  const extractNow = useMutation({
    mutationFn: () => extractSessionMemories(sessionId),
    onSuccess: async (result) => {
      if (result.status === 'no_new_messages') {
        toast.info('该会话没有新的可抽取内容')
      } else {
        toast.success(
          `抽取完成：提炼 ${result.drafts_created} 条（自动写入 ${result.auto_committed ?? 0} 条）${result.completion_reason ? ` · ${result.completion_reason}` : ''}`,
        )
      }
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, 'memory') })
    },
    onError: (error) => toast.error(error.message),
  })
  const contextSummary = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'context-summary', sessionId),
    queryFn: () => getSessionContextSummary(sessionId),
    enabled: Boolean(sessionId),
  })
  const summarizeNow = useMutation({
    mutationFn: () => summarizeSessionContext(sessionId),
    onSuccess: (result) => {
      if (result.status === 'ok') {
        if (result.summary) {
          queryClient.setQueryData(
            workspaceQueryKey(workspaceId, 'memory', 'context-summary', sessionId),
            result.summary,
          )
        }
        toast.success(
          `摘要已生成 v${result.version}：覆盖 ${result.covered_messages} 条消息（本次新增 ${result.newly_summarized} 条）`,
        )
      } else if (result.status === 'too_short') {
        toast.info('该会话消息太少，暂不需要摘要')
      } else if (result.status === 'fresh') {
        toast.info('摘要已是最新，无需重新生成')
      } else {
        toast.info(`未生成摘要：${result.status}`)
      }
    },
    onError: (error) => toast.error(error.message),
  })

  const extractionBlocked = policy.data && !policy.data.effective_learning_enabled
  const extractionBlockers = policy.data
    ? [
        !policy.data.workspace_enabled && '工作区共同记忆',
        !policy.data.workspace_learning_enabled && '工作区学习记忆',
        !policy.data.session_enabled && 'Session 共同记忆',
        !policy.data.session_learning_enabled && 'Session 学习记忆',
      ].filter(Boolean)
    : []

  if (!sessions.length) {
    return (
      <Surface className="p-2">
        <EmptyState
          description="创建会话后可以在这里查看下一轮对话实际注入的记忆。"
          title="当前没有 Session"
        />
      </Surface>
    )
  }

  return (
    <Surface className="p-5">
      <SectionHeading
        description="透明化：查看所选 Session 下一轮对话实际会注入哪些记忆。"
        title="注入预览"
      />
      <div className="mt-4 flex flex-col gap-2 border-t pt-4 sm:flex-row sm:items-center">
        <SessionCombobox
          className="sm:max-w-sm"
          onChange={setSessionId}
          sessions={sessions}
          value={sessionId}
        />
        <div className="flex flex-wrap gap-1.5">
          <Button
            disabled={!sessionId || preview.isFetching}
            onClick={() => void preview.refetch()}
            size="sm"
            variant="outline"
          >
            <RefreshCw className={preview.isFetching ? 'size-4 animate-spin' : 'size-4'} />
            刷新
          </Button>
          <Button
            disabled={!sessionId || extractNow.isPending || policy.isPending || Boolean(extractionBlocked)}
            onClick={() => extractNow.mutate()}
            size="sm"
            variant="outline"
          >
            <Sparkles className={extractNow.isPending ? 'size-4 animate-pulse' : 'size-4'} />
            立即抽取记忆
          </Button>
          <Button
            disabled={!sessionId || summarizeNow.isPending}
            onClick={() => summarizeNow.mutate()}
            size="sm"
            variant="outline"
          >
            <FileText className={summarizeNow.isPending ? 'size-4 animate-pulse' : 'size-4'} />
            立即生成摘要
          </Button>
        </div>
      </div>
      {policy.isError ? (
        <p className="mt-3 text-xs text-destructive">记忆策略读取失败：{policy.error.message}</p>
      ) : extractionBlocked ? (
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200">
          当前未满足自动学习条件：{extractionBlockers.join('、')}。请先在设置中开启后再提取。
        </p>
      ) : null}
      <div className="mt-4 rounded-lg border bg-muted/15 p-4">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-semibold">会话上下文摘要</p>
          {contextSummary.data ? (
            <span className="text-xs text-muted-foreground">v{contextSummary.data.version}</span>
          ) : null}
        </div>
        {contextSummary.isLoading ? (
          <p className="mt-2 text-xs text-muted-foreground">正在读取摘要…</p>
        ) : contextSummary.isError ? (
          <p className="mt-2 text-xs text-destructive">摘要读取失败：{contextSummary.error.message}</p>
        ) : contextSummary.data ? (
          <div className="mt-3 space-y-2">
            <p className="whitespace-pre-wrap text-sm leading-6">{contextSummary.data.summary}</p>
            <p className="text-[11px] text-muted-foreground">
              覆盖 {contextSummary.data.source_message_ids.length} 条消息 ·{' '}
              {contextSummary.data.estimated_tokens_before} → {contextSummary.data.estimated_tokens_after}{' '}
              tokens
            </p>
          </div>
        ) : (
          <p className="mt-2 text-xs text-muted-foreground">当前 Session 尚未生成会话摘要。</p>
        )}
      </div>
      {preview.isLoading ? (
        <p className="mt-4 text-xs text-muted-foreground">加载中…</p>
      ) : preview.isError ? (
        <p className="mt-4 text-xs text-destructive">{preview.error.message}</p>
      ) : preview.data ? (
        <div className="mt-4 space-y-3 border-t pt-4">
          <p className="text-xs text-muted-foreground">
            命中 {preview.data.effective_memories.length} 条 · 估算 {preview.data.token_estimate} tokens
            {preview.data.conflicts.length ? ` · ${preview.data.conflicts.length} 处作用域覆盖` : ''}
          </p>
          {preview.data.effective_memories.length ? (
            <ul className="grid gap-2 lg:grid-cols-2">
              {preview.data.effective_memories.map((item) => (
                <li className="rounded-lg border bg-muted/20 px-3 py-2" key={item.id}>
                  <p className="text-xs font-semibold">{item.title}</p>
                  <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                    {item.record_kind} · {item.scope_type} · {item.zone}
                    {typeof item.retrieval_score === 'number'
                      ? ` · score ${item.retrieval_score.toFixed(2)}`
                      : ''}
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">
              当前策略下不会注入任何记忆（检查工作区/Session 开关，或还没有活跃记忆）。
            </p>
          )}
          {preview.data.prompt_block ? (
            <ScrollArea className="max-h-72 rounded-lg border bg-muted/25">
              <pre className="whitespace-pre-wrap p-3 text-[10px] leading-4 text-muted-foreground">
                {preview.data.prompt_block}
              </pre>
            </ScrollArea>
          ) : null}
        </div>
      ) : null}
    </Surface>
  )
}

function RetentionMaintenanceCard({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient()
  const purge = useMutation({
    mutationFn: purgeExpiredMemoryContent,
    onSuccess: async (result) => {
      toast.success(
        `维护完成：销毁 ${result.content_keys_destroyed} 个到期内容密钥，清理 ${result.journal_entries_removed} 条 Journal`,
      )
      await queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, 'memory'),
      })
    },
    onError: (error) => toast.error(error.message),
  })

  return (
    <Surface className="border-amber-200 bg-amber-50/35 p-4 dark:border-amber-900 dark:bg-amber-950/15">
      <SectionHeading
        description="仅系统管理员可见；服务端会重校验 Bearer 与工作区作用域。"
        title="保留期维护"
      />
      <p className="mt-3 text-xs leading-5 text-muted-foreground">
        销毁超过恢复窗口的内容密钥与恢复密文，并清理到期 Journal 元数据。不会恢复或伪造正文。
      </p>
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button className="mt-4" disabled={purge.isPending} size="sm" variant="outline">
            <RefreshCw className={purge.isPending ? 'size-4 animate-spin' : 'size-4'} />
            运行到期清理
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>运行记忆保留期清理？</AlertDialogTitle>
            <AlertDialogDescription>
              系统会仅销毁已经超过恢复窗口的内容密钥和到期审计元数据。未到期的删除记录不会受影响。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction disabled={purge.isPending} onClick={() => purge.mutate()}>
              {purge.isPending ? '清理中…' : '确认运行清理'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Surface>
  )
}

function GoalMemoryToolsCard({ goals, workspaceId }: { goals: Goal[]; workspaceId: string }) {
  const queryClient = useQueryClient()
  const [selectedGoalId, setSelectedGoalId] = useState('')
  const [overviewOpen, setOverviewOpen] = useState(false)

  const overview = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'goal-overview', selectedGoalId),
    queryFn: () => getGoalMemoryOverview(selectedGoalId),
    enabled: Boolean(selectedGoalId) && overviewOpen,
  })

  const archive = useMutation({
    mutationFn: () => archiveGoalMemories(selectedGoalId),
    onSuccess: async (result) => {
      toast.success(`已归档 ${result.archived} 条 Goal 记忆`)
      await queryClient.invalidateQueries({
        queryKey: workspaceResourcePrefix(workspaceId, 'memory'),
      })
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, 'goals') })
    },
    onError: (error) => toast.error(error.message),
  })

  return (
    <Surface className="gap-3 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-52 flex-1 space-y-1">
          <Label className="text-sm">Goal 记忆总览与归档</Label>
          <p className="text-xs text-muted-foreground">
            为指定目标生成热区记忆总览，或在目标结束后将生命周期记忆移入冷区归档。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Select onValueChange={setSelectedGoalId} value={selectedGoalId}>
            <SelectTrigger className="w-52">
              <SelectValue placeholder="选择目标" />
            </SelectTrigger>
            <SelectContent>
              {goals.map((goal) => (
                <SelectItem key={goal.id} value={goal.id}>
                  {goal.title || goal.id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Dialog onOpenChange={setOverviewOpen} open={overviewOpen}>
            <DialogTrigger asChild>
              <Button
                disabled={!selectedGoalId}
                onClick={() => setOverviewOpen(true)}
                type="button"
                variant="outline"
              >
                <Eye className="size-4" />生成总览
              </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
              <DialogHeader>
                <DialogTitle>Goal 记忆总览</DialogTitle>
                <DialogDescription>
                  {goals.find((goal) => goal.id === selectedGoalId)?.title ?? selectedGoalId}
                </DialogDescription>
              </DialogHeader>
              {overview.isPending ? (
                <LoadingState label="正在生成总览…" />
              ) : overview.isError ? (
                <ErrorState message={overview.error.message} onRetry={() => overview.refetch()} />
              ) : (
                <div className="rounded-xl border bg-background p-4">
                  <MessageResponse className="prose prose-sm max-w-none">
                    {overview.data?.overview_markdown ?? ''}
                  </MessageResponse>
                </div>
              )}
            </DialogContent>
          </Dialog>
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                disabled={!selectedGoalId || archive.isPending}
                type="button"
                variant="destructive"
              >
                <Archive className="size-4" />
                {archive.isPending ? '归档中…' : '归档 Goal 记忆'}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogMedia className="bg-amber-500/10 text-amber-700">
                  <FileClock />
                </AlertDialogMedia>
                <AlertDialogTitle>归档该目标的记忆？</AlertDialogTitle>
                <AlertDialogDescription>
                  将目标关联的记忆移入冷区（archive），不再参与热区召回。此操作会更新记忆层级并可追溯审计，不会删除任何记忆。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction
                  onClick={(event) => {
                    event.preventDefault()
                    void archive.mutateAsync().then(() => undefined)
                  }}
                >
                  确认归档
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>
    </Surface>
  )
}
