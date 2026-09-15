import { type FormEvent, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Download, MoreHorizontal, Plus, RefreshCw, Settings2, Trash2 } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'

import {
  applyMemoryProfileIntent,
  createMemory,
  deleteMemory,
  exportMemoryMarkdown,
  getCurrentUser,
  getGraph,
  getMemoryProfile,
  listGoals,
  listGraphs,
  listMemories,
  listMemoryTypes,
  listMemoryViews,
  listSessions,
  migrateLegacyMemoryAtoms,
  refreshMemoryProfile,
  restoreDeletedMemory,
  restoreMemoryRevision,
  updateMemory,
} from '@/api'
import { authStore } from '@/api/auth-store'
import { ApiError } from '@/api/client'
import { saveBlobViaNative } from '@/lib/native-download'
import {
  identityQueryKey,
  workspaceQueryKey,
  workspaceResourcePrefix,
} from '@/lib/query-keys'
import {
  ErrorState,
  LoadingState,
  PageFrame,
  PageIntro,
} from '@/components/shared/page-elements'
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
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import type {
  MemoryCreateRequest,
  MemoryEntry,
  MemoryNamespace,
  MemoryScopeType,
  MemoryZone,
} from '@/types/memory'
import type { Goal } from '@/types/goals'
import type { GraphNode, GraphSummary } from '@/types/graphs'
import type { Session } from '@/types/sessions'
import { MemoryAllList } from './components/memory-all-list'
import { MemoryDiagnosticsSheet } from './components/memory-diagnostics-sheet'
import { MemoryPendingConfirmationPanel } from './components/memory-pending-confirmation-panel'
import { MemoryReport, type MemoryReportSelection } from './components/memory-report'
import { MemoryReportComposer } from './components/memory-report-composer'
import { MemorySettingsDialog } from './components/memory-settings-dialog'
import { MemoryTrashDialog } from './components/memory-trash-dialog'
import { usePendingMemorySummary } from './use-pending-memory-summary'

const zoneDefinitions: Array<{ zone: MemoryZone; title: string }> = [
  { zone: 'hot', title: '热摘要' },
  { zone: 'recent', title: '近期事件' },
  { zone: 'topics', title: '主题记忆' },
  { zone: 'archive', title: '冷区归档' },
]

type MemoryTab = 'report' | 'all' | 'pending'

/**
 * 从自然语言输入推导记忆标题：取首个非空行，去掉常见 Markdown 标记，
 * 截断到 240 字符以内（服务端标题上限）。仅用于无模型降级保存路径。
 */
function memoryTitleFromText(text: string): string {
  const firstLine = text
    .split('\n')
    .map((line) => line.trim())
    .find(Boolean)
  const cleaned = (firstLine ?? text).replace(/[#>*`[\]()]/g, '').trim()
  return cleaned.slice(0, 240) || '用户记忆'
}

/**
 * `memory_profile_*` 错误码的中文口径。
 *
 * 后端这些 AppError 的 message 全是英文（例如刷新报告时的
 * "The configured memory profile model is unavailable"），页面必须在弹出前
 * 统一翻译，否则用户会看到裸英文。返回 null 表示不属于这一类，调用方回落到
 * 原始 message。
 */
function memoryProfileErrorText(error: unknown, fallback: string): string | null {
  if (!(error instanceof ApiError)) return null
  if (error.code === 'memory_profile_model_unavailable') {
    return '记忆模型当前不可用，请到「记忆设置」检查模型配置后重试'
  }
  if (error.code.startsWith('memory_profile_')) return fallback
  return null
}

function downloadBlob(blob: Blob): void {
  const name = `learngraph-memory-${new Date().toISOString().slice(0, 10)}.zip`
  // 移动端 WebView：纯 blob（后端导出 zip）交给原生 base64 通道
  void saveBlobViaNative(blob, name).then((handled) => {
    if (handled) return
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = name
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  })
}

export function MemoryPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { workspaceId = '' } = useParams()
  const [tab, setTab] = useState<MemoryTab>('report')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [trashOpen, setTrashOpen] = useState(false)
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [selection, setSelection] = useState<MemoryReportSelection | null>(null)
  const [forgetTarget, setForgetTarget] = useState<MemoryEntry[] | null>(null)
  const composerRef = useRef<HTMLInputElement>(null)

  const memories = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'views'),
    queryFn: () => listMemoryViews({ include_content: true }),
  })
  const profile = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'profile'),
    queryFn: getMemoryProfile,
  })
  const memoryTypes = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'types'),
    queryFn: listMemoryTypes,
  })
  const goals = useQuery({ queryKey: workspaceQueryKey(workspaceId, 'goals'), queryFn: listGoals })
  const graphs = useQuery({ queryKey: workspaceQueryKey(workspaceId, 'graphs'), queryFn: listGraphs })
  const deleted = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'deleted'),
    queryFn: async () => [
      ...(await listMemories({ state: 'deleted' })),
      ...(await listMemories({ state: 'destroyed' })),
    ],
  })
  const sessions = useQuery({ queryKey: workspaceQueryKey(workspaceId, 'sessions'), queryFn: listSessions })
  // /auth/me deliberately opts out of X-Workspace-ID, so it belongs to the identity namespace.
  const operator = useQuery({
    queryKey: identityQueryKey(authStore.getSession()?.userId, 'current-user'),
    queryFn: getCurrentUser,
  })
  const { total: pendingCount } = usePendingMemorySummary()

  const refreshMemory = async () => {
    await queryClient.invalidateQueries({ queryKey: workspaceResourcePrefix(workspaceId, 'memory') })
  }
  const create = useMutation({
    mutationFn: createMemory,
    onSuccess: async (item) => {
      toast.success(`已新增记忆（版本 ${item.revision}）`)
      await refreshMemory()
    },
    onError: (error) => toast.error(error.message),
  })
  const update = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Parameters<typeof updateMemory>[1] }) =>
      updateMemory(id, payload),
    onSuccess: async (item) => {
      toast.success(`已保存为版本 ${item.revision}`)
      await refreshMemory()
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, 'memory', 'detail', item.id) })
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, 'memory', 'revisions', item.id) })
    },
    onError: (error) => toast.error(error.message),
  })
  const remove = useMutation({
    mutationFn: deleteMemory,
    onSuccess: async () => {
      toast.success('已移入回收站，30 分钟内可以恢复')
      await refreshMemory()
    },
    onError: (error) => toast.error(error.message),
  })
  const restore = useMutation({
    mutationFn: restoreDeletedMemory,
    onSuccess: async (item) => {
      toast.success(`已恢复（版本 ${item.revision}）`)
      await refreshMemory()
    },
    onError: (error) => toast.error(error.message),
  })
  const restoreRevision = useMutation({
    mutationFn: ({ id, revision, expectedRevision }: { id: string; revision: number; expectedRevision: number }) =>
      restoreMemoryRevision(id, revision, expectedRevision),
    onSuccess: async (item) => {
      toast.success(`已恢复为版本 ${item.revision}`)
      await refreshMemory()
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, 'memory', 'revisions', item.id) })
    },
    onError: (error) => toast.error(error.message),
  })
  const exportArchive = useMutation({
    mutationFn: exportMemoryMarkdown,
    onSuccess: (blob) => {
      downloadBlob(blob)
      toast.success('记忆已导出')
    },
    onError: (error) => toast.error(error.message),
  })
  const editProfile = useMutation({
    mutationFn: applyMemoryProfileIntent,
    onSuccess: async (result) => {
      if (result.status === 'no_change') {
        toast.info('没有识别到需要长期保存的变化')
      } else if (result.auto_committed) {
        toast.success(`已整理并更新 ${result.auto_committed} 条记忆`)
      } else {
        toast.info('已整理成一条新理解，等待你确认')
      }
      await refreshMemory()
    },
    onError: (error) => {
      // 未配置记忆模型时 quickAdd 会降级为直接创建普通记忆并给出中文提示，
      // 这里不再重复弹出英文错误。其余 memory_profile_* 错误统一翻成中文。
      if (error instanceof ApiError && error.code === 'memory_profile_model_unconfigured') return
      toast.error(
        memoryProfileErrorText(error, '整理这次没能完成，输入内容已保留，可稍后重试')
          ?? error.message,
      )
    },
  })
  const regenerateProfile = useMutation({
    mutationFn: refreshMemoryProfile,
    onSuccess: async (result) => {
      // 未配置提取/摘要模型时后端返回原子快照（200），此时不是“整篇重写”，
      // 只提示保持快照模式，配置模型后可一键生成正式报告。
      if (result.status === 'atomic_snapshot') {
        toast.info('未配置记忆摘要模型，当前保持原子快照；配置后可生成正式报告')
      } else {
        toast.success('学习记忆报告已整篇重写')
      }
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, 'memory', 'profile') })
    },
    onError: (error) => {
      // 后端用 `memory_profile_*` 系列错误码说明失败原因；统一翻成中文，并讲清楚
      // 「已保留上一版报告」，避免用户以为报告被清空。
      if (error instanceof ApiError) {
        // 未配置模型时页面会显示原子快照提示，这里不再重复弹窗。
        if (error.code === 'memory_profile_model_unconfigured') return
      }
      toast.error(
        memoryProfileErrorText(error, '刷新失败：模型这次没能完成整理，已保留上一版报告，可稍后再试')
          ?? error.message,
      )
    },
  })
  const migrateAtoms = useMutation({
    mutationFn: () => migrateLegacyMemoryAtoms(20),
    onSuccess: async (result) => {
      toast.success(
        `旧记忆整理完成：迁移 ${result.migrated} 条，拆分新增 ${result.created} 条`,
      )
      await refreshMemory()
      if (result.migrated || result.created) {
        await regenerateProfile.mutateAsync()
      }
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === 'memory_profile_model_unconfigured') {
        toast.info('未配置记忆提取模型，旧记忆整理暂不可用；配置模型后可迁移为原子记忆')
        return
      }
      toast.error(
        memoryProfileErrorText(error, '旧记忆整理失败：模型这次没能完成整理，可稍后重试')
          ?? error.message,
      )
    },
  })
  // 报告正文“忘记这件事”的软删除：逐条删除后统一刷新，失败直接提示。
  const forgetSelectionMutation = useMutation({
    mutationFn: async (items: MemoryEntry[]) => {
      for (const item of items) await deleteMemory(item.id)
    },
    onSuccess: async () => {
      setForgetTarget(null)
      setSelection(null)
      toast.success('已移入回收站，30 分钟内可以恢复')
      await refreshMemory()
    },
    onError: (error) => toast.error(error.message),
  })

  if (memories.isPending || profile.isPending || deleted.isPending || sessions.isPending) {
    return (
      <PageFrame>
        <LoadingState />
      </PageFrame>
    )
  }
  const firstError = memories.error ?? profile.error ?? deleted.error ?? sessions.error
  if (firstError) {
    return (
      <PageFrame>
        <ErrorState message={firstError.message} />
      </PageFrame>
    )
  }

  const activeMemories = memories.data ?? []
  const deletedMemories = deleted.data ?? []
  const sessionList = sessions.data ?? []
  const goalList = goals.data ?? []
  const graphList = graphs.data ?? []
  const typeList = memoryTypes.data ?? []
  const recoverableCount = deletedMemories.filter((item) => item.restore_available).length
  const zoneBusy = remove.isPending || update.isPending || restoreRevision.isPending
  const composerBusy = editProfile.isPending || regenerateProfile.isPending || migrateAtoms.isPending

  // “告诉 AI 导师需要记住、修改或忘记什么”优先走自然语言意图（LLM 原子化整理）。
  // 当工作区未配置记忆提取/摘要模型时，后端会返回 memory_profile_model_unconfigured ——
  // 此时降级为直接创建一条普通记忆，保证输入框在无模型状态也能发送并立即在报告与
  // 记忆列表中生效（原子快照模式会逐条列出，配置模型后可一键整理为原子）。
  const quickAdd = async (
    content: string,
    selectedText?: string,
    selectedAtomIds?: string[],
  ) => {
    try {
      await editProfile.mutateAsync({
        text: content,
        selected_text: selectedText,
        selected_atom_ids: selectedAtomIds,
        timezone_name: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
      })
    } catch (error) {
      if (error instanceof ApiError && error.code === 'memory_profile_model_unconfigured') {
        toast.info('未配置记忆提取/摘要模型，已按普通记忆直接保存；配置模型后可自动原子化整理')
        await create.mutateAsync({
          title: memoryTitleFromText(content),
          content,
          zone: 'topics',
          namespace: 'workspace',
          scope_type: 'workspace',
          record_kind: 'semantic_memory',
          source: 'user_confirmed',
        })
        return
      }
      throw error
    }
  }

  async function submitIntent(event: FormEvent) {
    event.preventDefault()
    const content = draft.trim()
    if (!content || composerBusy) return
    try {
      await quickAdd(content, selection?.text, selection?.atomIds)
    } catch {
      // 错误已由 mutation 的 onError 提示，保留输入内容让用户修改后重试。
      return
    }
    setDraft('')
    setSelection(null)
    window.getSelection()?.removeAllRanges()
  }

  function requestCorrection(text: string) {
    setTab('report')
    setSelection({ text, atomIds: [] })
    window.requestAnimationFrame(() => composerRef.current?.focus())
  }

  /**
   * 报告正文里的“忘记这件事”：先用 atom_ids（lg_memory_id）精确定位到真实记忆，
   * 再走一次可恢复的软删除。定位不到时不做任何破坏性操作，只提示去「全部记忆」处理。
   */
  function forgetSelection(target: MemoryReportSelection) {
    const ids = new Set(target.atomIds)
    const matches = activeMemories.filter(
      (item) => ids.has(item.lg_memory_id) && item.view_source !== 'event',
    )
    if (!matches.length) {
      setSelection(null)
      toast.info('没有找到与这句话对应的可删除记忆，可以在「全部记忆」中逐条处理')
      return
    }
    setForgetTarget(matches)
  }

  return (
    <PageFrame>
      {/* 统一容器：页面标题、Tabs、报告、底部输入框共用同一个宽度基准。 */}
      <div className="mx-auto flex w-full max-w-[1040px] flex-col gap-5">
        <PageIntro
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <Button
                disabled={regenerateProfile.isPending}
                onClick={() => regenerateProfile.mutate()}
                size="sm"
                variant="outline"
              >
                <RefreshCw
                  className={regenerateProfile.isPending ? 'size-4 animate-spin' : 'size-4'}
                />
                刷新报告
              </Button>
              <Button
                disabled={exportArchive.isPending || !activeMemories.length}
                onClick={() => exportArchive.mutate()}
                size="sm"
                variant="outline"
              >
                <Download className="size-4" />导出
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button aria-label="更多记忆操作" size="icon-sm" variant="outline">
                    <MoreHorizontal className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-48">
                  <DropdownMenuItem onSelect={() => setSettingsOpen(true)}>
                    <Settings2 className="size-3.5" />记忆设置
                  </DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => setTrashOpen(true)}>
                    <Trash2 className="size-3.5" />回收站
                    {recoverableCount ? (
                      <span className="ml-auto font-mono text-[10px] tabular-nums">
                        {recoverableCount}
                      </span>
                    ) : null}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onSelect={() => setDiagnosticsOpen(true)}>
                    <Activity className="size-3.5" />高级诊断
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          }
          description="这是 AI 导师根据与你的对话、学习记录与长期记忆持续整理的理解。你可以随时纠正它，它也会随着你的学习持续更新。"
          eyebrow="Workspace memory"
          title="我的学习记忆报告"
        />

        <Tabs onValueChange={(value) => setTab(value as MemoryTab)} value={tab}>
          <TabsList className="h-9 max-w-full justify-start overflow-x-auto">
            <TabsTrigger className="px-3" value="report">
              学习报告
            </TabsTrigger>
            <TabsTrigger className="px-3" value="all">
              全部记忆
              <span className="ml-1.5 font-mono text-[10px] text-muted-foreground tabular-nums">
                {activeMemories.length}
              </span>
            </TabsTrigger>
            <TabsTrigger className="px-3" value="pending">
              待确认
              {pendingCount ? (
                <span className="ml-1.5 font-mono text-[10px] text-muted-foreground tabular-nums">
                  {pendingCount}
                </span>
              ) : null}
            </TabsTrigger>
          </TabsList>

          <TabsContent className="mt-0 outline-none" value="report">
            <MemoryReport
              busy={composerBusy}
              legacyCount={
                activeMemories.filter((item) => (item.atom_schema_version ?? 0) === 0).length
              }
              onForgetSelection={forgetSelection}
              onMigrate={() => migrateAtoms.mutateAsync().then(() => undefined)}
              onOpenPending={() => setTab('pending')}
              onRefresh={() => regenerateProfile.mutateAsync().then(() => undefined)}
              onRequestComposer={() => composerRef.current?.focus()}
              onSelectionChange={setSelection}
              pendingCount={pendingCount}
              profile={profile.data}
            />
          </TabsContent>

          <TabsContent className="mt-0 outline-none" value="all">
            <MemoryAllList
              busy={zoneBusy}
              createAction={
                <CreateMemoryDialog
                  busy={create.isPending}
                  goals={goalList}
                  graphs={graphList}
                  onCreate={(payload) => create.mutateAsync(payload).then(() => undefined)}
                  sessions={sessionList}
                  types={typeList}
                  workspaceId={workspaceId}
                />
              }
              items={activeMemories}
              onDelete={(id) => remove.mutate(id)}
              onRestoreRevision={(item, revision) =>
                restoreRevision.mutate({
                  id: item.id,
                  revision,
                  expectedRevision: item.revision,
                })
              }
              onUpdate={(id, payload) =>
                update.mutateAsync({ id, payload }).then(() => undefined)
              }
              workspaceId={workspaceId}
            />
          </TabsContent>

          <TabsContent className="mt-0 outline-none" value="pending">
            <MemoryPendingConfirmationPanel
              onRequestCorrection={requestCorrection}
              sessions={sessionList}
            />
          </TabsContent>
        </Tabs>

        {tab === 'report' ? (
          <div className="sticky bottom-4 z-10 px-6 sm:px-10">
            <MemoryReportComposer
              busy={composerBusy}
              draft={draft}
              inputRef={composerRef}
              onClearSelection={() => setSelection(null)}
              onDraftChange={setDraft}
              onSubmit={(event) => void submitIntent(event)}
              selection={selection}
            />
          </div>
        ) : null}
      </div>

      <Dialog
        onOpenChange={(open) => !open && setForgetTarget(null)}
        open={Boolean(forgetTarget)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>忘记这件事？</DialogTitle>
            <DialogDescription>
              会把下列记忆移入回收站，并从报告中移除。30 分钟内可以在回收站恢复；如果需要彻底销毁，请在待确认页使用「永久忘记」。
            </DialogDescription>
          </DialogHeader>
          <ul className="max-h-56 space-y-1.5 overflow-y-auto rounded-xl border bg-muted/20 p-3 text-sm">
            {(forgetTarget ?? []).map((item) => (
              <li className="line-clamp-2" key={item.id}>
                {item.title}
              </li>
            ))}
          </ul>
          <DialogFooter>
            <Button onClick={() => setForgetTarget(null)} variant="ghost">
              取消
            </Button>
            <Button
              disabled={forgetSelectionMutation.isPending}
              onClick={() => forgetSelectionMutation.mutate(forgetTarget ?? [])}
              variant="destructive"
            >
              {forgetSelectionMutation.isPending ? '处理中…' : '确认忘记'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <MemorySettingsDialog
        exportBusy={exportArchive.isPending}
        onExport={() => exportArchive.mutate()}
        onOpenChange={setSettingsOpen}
        onOpenGovernance={() => navigate('../settings/workspace')}
        onOpenTrash={() => setTrashOpen(true)}
        open={settingsOpen}
      />
      <MemoryTrashDialog
        busy={restore.isPending}
        items={deletedMemories}
        onOpenChange={setTrashOpen}
        onRestore={(id) => restore.mutate(id)}
        open={trashOpen}
      />
      <MemoryDiagnosticsSheet
        goals={goalList}
        isSystemAdmin={Boolean(operator.data?.is_system_admin)}
        onOpenChange={setDiagnosticsOpen}
        open={diagnosticsOpen}
        sessions={sessionList}
        workspaceId={workspaceId}
      />
    </PageFrame>
  )
}

function ScopeFields({
  scopeType,
  onScopeTypeChange,
  goalId,
  onGoalIdChange,
  nodeId,
  onNodeIdChange,
  goals,
  graphs,
  workspaceId,
}: {
  scopeType: MemoryScopeType
  onScopeTypeChange: (value: MemoryScopeType) => void
  goalId: string
  onGoalIdChange: (value: string) => void
  nodeId: string
  onNodeIdChange: (value: string) => void
  goals: Goal[]
  graphs: GraphSummary[]
  workspaceId: string
}) {
  const selectedGraphId = graphs.find((graph) => graph.goal_id === goalId)?.id ?? graphs[0]?.id ?? ''
  const graphDetail = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'graph', selectedGraphId),
    queryFn: () => getGraph(selectedGraphId),
    enabled: scopeType === 'node' && Boolean(selectedGraphId),
  })
  const nodes: GraphNode[] = graphDetail.data?.nodes ?? []

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="space-y-2">
        <Label>作用范围</Label>
        <Select onValueChange={(value) => onScopeTypeChange(value as MemoryScopeType)} value={scopeType}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="workspace">整个工作区</SelectItem>
            <SelectItem value="goal">某个学习目标</SelectItem>
            <SelectItem value="node">某个知识节点</SelectItem>
            <SelectItem value="session">某次对话</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {scopeType === 'goal' || scopeType === 'node' ? (
        <div className="space-y-2">
          <Label>目标</Label>
          <Select onValueChange={(value) => onGoalIdChange(value ?? '')} value={goalId}>
            <SelectTrigger><SelectValue placeholder="选择目标" /></SelectTrigger>
            <SelectContent>
              {goals.map((goal) => (
                <SelectItem key={goal.id} value={goal.id}>{goal.title || goal.id}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}
      {scopeType === 'node' ? (
        <div className="space-y-2 sm:col-span-2">
          <Label>知识节点</Label>
          <Select
            disabled={!selectedGraphId || graphDetail.isPending}
            onValueChange={(value) => onNodeIdChange(value ?? '')}
            value={nodeId}
          >
            <SelectTrigger><SelectValue placeholder={graphDetail.isPending ? '加载节点…' : '选择节点'} /></SelectTrigger>
            <SelectContent>
              {nodes.map((node) => (
                <SelectItem key={node.id} value={node.id}>{node.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          {!goals.length ? (
            <p className="text-xs text-muted-foreground">工作区尚无学习目标；请先在目标页创建并发布图谱。</p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function CreateMemoryDialog({
  sessions,
  goals,
  graphs,
  types,
  busy,
  workspaceId,
  onCreate,
}: {
  sessions: Session[]
  goals: Goal[]
  graphs: GraphSummary[]
  types: Array<{ memory_type: string; description: string }>
  busy: boolean
  workspaceId: string
  onCreate: (payload: MemoryCreateRequest) => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [zone, setZone] = useState<MemoryZone>('topics')
  const [namespace, setNamespace] = useState<MemoryNamespace>('workspace')
  const [sessionId, setSessionId] = useState('')
  const [scopeType, setScopeType] = useState<MemoryScopeType>('workspace')
  const [goalId, setGoalId] = useState('')
  const [nodeId, setNodeId] = useState('')
  const [recordKind, setRecordKind] = useState('semantic_memory')

  async function submit(event: FormEvent) {
    event.preventDefault()
    try {
      await onCreate({
        title: title.trim(),
        content: content.trim(),
        zone,
        namespace,
        session_id: namespace === 'session' ? sessionId : undefined,
        scope_type: scopeType === 'session' ? 'session' : scopeType,
        goal_id: scopeType === 'goal' || scopeType === 'node' ? goalId || undefined : undefined,
        node_id: scopeType === 'node' ? nodeId || undefined : undefined,
        scope_id:
          scopeType === 'goal'
            ? goalId || undefined
            : scopeType === 'node'
              ? nodeId || undefined
              : undefined,
        record_kind: recordKind,
        source: 'user_confirmed',
      })
    } catch {
      return
    }
    setOpen(false)
    setTitle('')
    setContent('')
    setNodeId('')
  }

  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          <Plus className="size-4" />手动新增
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <form onSubmit={(event) => void submit(event)}>
          <DialogHeader>
            <DialogTitle>手动新增一条记忆</DialogTitle>
            <DialogDescription>
              一般不需要手动新增：在报告页底部直接告诉 AI 导师通常更方便。这里用于精确指定作用范围的场景。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-5">
            <div className="space-y-2">
              <Label htmlFor="memory-title">标题</Label>
              <Input id="memory-title" maxLength={240} onChange={(event) => setTitle(event.target.value)} value={title} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="memory-content">内容</Label>
              <Textarea className="min-h-32" id="memory-content" maxLength={50_000} onChange={(event) => setContent(event.target.value)} value={content} />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="memory-zone">读取优先级</Label>
                <Select onValueChange={(value) => setZone(value as MemoryZone)} value={zone}>
                  <SelectTrigger id="memory-zone"><SelectValue /></SelectTrigger>
                  <SelectContent>{zoneDefinitions.map((item) => <SelectItem key={item.zone} value={item.zone}>{item.title}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="memory-namespace">可见范围</Label>
                <Select onValueChange={(value) => setNamespace(value as MemoryNamespace)} value={namespace}>
                  <SelectTrigger id="memory-namespace"><SelectValue /></SelectTrigger>
                  <SelectContent><SelectItem value="workspace">整个工作区</SelectItem><SelectItem value="session">仅当前对话</SelectItem></SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-2">
              <Label>记忆类型</Label>
              <Select onValueChange={(value) => setRecordKind(value ?? 'semantic_memory')} value={recordKind}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(types.length ? types : [{ memory_type: 'semantic_memory', description: '稳定事实' }]).map((item) => (
                    <SelectItem key={item.memory_type} value={item.memory_type}>
                      {item.memory_type}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <ScopeFields
              goalId={goalId}
              goals={goals}
              graphs={graphs}
              nodeId={nodeId}
              onGoalIdChange={setGoalId}
              onNodeIdChange={setNodeId}
              onScopeTypeChange={setScopeType}
              scopeType={scopeType}
              workspaceId={workspaceId}
            />
            {namespace === 'session' ? (
              <div className="space-y-2">
                <Label htmlFor="memory-scope-session">所属对话</Label>
                <Select onValueChange={(value) => setSessionId(value ?? '')} value={sessionId}>
                  <SelectTrigger id="memory-scope-session"><SelectValue placeholder="选择对话" /></SelectTrigger>
                  <SelectContent>{sessions.map((session) => <SelectItem key={session.id} value={session.id}>{session.title}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              disabled={
                busy
                || !title.trim()
                || !content.trim()
                || (namespace === 'session' && !sessionId)
                || (scopeType === 'goal' && !goalId)
                || (scopeType === 'node' && (!goalId || !nodeId))
              }
              type="submit"
            >
              {busy ? '保存中…' : '保存'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
