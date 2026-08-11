import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'motion/react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Archive,
  ArrowUp,
  BookOpenText,
  Clock3,
  Download,
  Eye,
  FileClock,
  FileText,
  History,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Settings2,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { toast } from 'sonner'

import {
  applyMemoryProfileIntent,
  archiveGoalMemories,
  createMemory,
  deleteMemory,
  exportMemoryMarkdown,
  extractSessionMemories,
  getCurrentUser,
  getEffectiveMemoryPackage,
  getGoalMemoryOverview,
  getGraph,
  getMemory,
  getMemoryProfile,
  getMemoryPolicy,
  getSessionContextSummary,
  listGoals,
  listGraphs,
  listMemoryBindings,
  listMemories,
  listMemoryRevisions,
  listMemoryTypes,
  listMemoryViews,
  listSessions,
  migrateLegacyMemoryAtoms,
  purgeExpiredMemoryContent,
  restoreDeletedMemory,
  restoreMemoryRevision,
  refreshMemoryProfile,
  summarizeSessionContext,
  updateMemory,
} from '@/api'
import { authStore } from '@/api/auth-store'
import {
  identityQueryKey,
  workspaceQueryKey,
  workspaceResourcePrefix,
} from '@/lib/query-keys'
import { MessageResponse } from '@/components/ai-elements/message'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  MetricStrip,
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from '@/components/shared/page-elements'
import { SessionCombobox } from '@/components/shared/session-combobox'
import { ContextManifestPanel } from './components/context-manifest-panel'
import { MemoryGovernancePanel } from './components/memory-governance-panel'
import { MemoryPendingConfirmationPanel } from './components/memory-pending-confirmation-panel'
import { MemorySettingsExportPanel } from './components/memory-settings-export-panel'
import { MemoryTaskEpisodePanel } from './components/memory-task-episode-panel'
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
  MemoryProfile,
  MemoryRevision,
  MemoryScopeType,
  MemoryZone,
} from '@/types/memory'
import type { Goal } from '@/types/goals'
import type { GraphNode, GraphSummary } from '@/types/graphs'
import type { Session } from '@/types/sessions'

const memoryGovernanceV2Enabled =
  import.meta.env.VITE_MEMORY_GOVERNANCE_V2 !== '0'

const zoneDefinitions: Array<{
  zone: MemoryZone
  title: string
  description: string
  icon: typeof Eye
}> = [
  { zone: 'hot', title: '热摘要', description: '当前目标与活跃约束', icon: Eye },
  { zone: 'recent', title: '近期事件', description: '尚未闭环的工作记忆', icon: Clock3 },
  { zone: 'topics', title: '主题记忆', description: '稳定且按需读取的事实', icon: FileText },
  { zone: 'archive', title: '冷区归档', description: '已闭环事件与完整来源', icon: Archive },
]

function memoryBody(markdown: string | null): string {
  if (!markdown) return ''
  const lines = markdown.split('\n')
  if (lines[0]?.startsWith('# ')) lines.shift()
  while (lines[0] === '') lines.shift()
  return lines.join('\n').trimEnd()
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—'
}

function profileUpdatedHint(latest: string | null): string | null {
  if (!latest) return null
  const elapsedMinutes = Math.max(
    0,
    Math.floor((Date.now() - new Date(latest).getTime()) / 60_000),
  )
  if (elapsedMinutes < 5) return '刚刚更新'
  if (elapsedMinutes < 60) return `${elapsedMinutes} 分钟前更新`
  const hours = Math.floor(elapsedMinutes / 60)
  if (hours < 24) return `${hours} 小时前更新`
  return `${new Date(latest).toLocaleDateString()} 更新`
}

function downloadBlob(blob: Blob): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `learngraph-memory-${new Date().toISOString().slice(0, 10)}.zip`
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

export function MemoryPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { workspaceId = '' } = useParams()
  const [selectedId, setSelectedId] = useState<string | null>(null)
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

  const refreshMemory = async () => {
    await queryClient.invalidateQueries({ queryKey: workspaceResourcePrefix(workspaceId, 'memory') })
  }
  const create = useMutation({
    mutationFn: createMemory,
    onSuccess: async (item) => {
      toast.success(`已创建 ${item.lg_memory_id} · revision ${item.revision}`)
      await refreshMemory()
    },
    onError: (error) => toast.error(error.message),
  })
  const update = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Parameters<typeof updateMemory>[1] }) =>
      updateMemory(id, payload),
    onSuccess: async (item) => {
      toast.success(`Revision ${item.revision} 已保存`)
      await refreshMemory()
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, 'memory', 'detail', item.id) })
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, 'memory', 'revisions', item.id) })
    },
    onError: (error) => toast.error(error.message),
  })
  const remove = useMutation({
    mutationFn: deleteMemory,
    onSuccess: async (item) => {
      toast.success(`已软删除；可恢复至 ${formatTime(item.recoverable_until)}`)
      setSelectedId(null)
      await refreshMemory()
    },
    onError: (error) => toast.error(error.message),
  })
  const restore = useMutation({
    mutationFn: restoreDeletedMemory,
    onSuccess: async (item) => {
      toast.success(`已恢复 ${item.lg_memory_id}，当前 revision ${item.revision}`)
      await refreshMemory()
    },
    onError: (error) => toast.error(error.message),
  })
  const restoreRevision = useMutation({
    mutationFn: ({ item, revision }: { item: MemoryEntry; revision: number }) =>
      restoreMemoryRevision(item.id, revision, item.revision),
    onSuccess: async (item) => {
      toast.success(`已从历史版本生成 revision ${item.revision}`)
      await refreshMemory()
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, 'memory', 'revisions', item.id) })
    },
    onError: (error) => toast.error(error.message),
  })
  const exportArchive = useMutation({
    mutationFn: exportMemoryMarkdown,
    onSuccess: (blob) => {
      downloadBlob(blob)
      toast.success('Markdown 与 manifest 已导出')
    },
    onError: (error) => toast.error(error.message),
  })
  const editProfile = useMutation({
    mutationFn: applyMemoryProfileIntent,
    onSuccess: async (result) => {
      if (result.status === 'no_change') {
        toast.info('没有识别到需要长期保存的变化')
      } else if (result.auto_committed) {
        toast.success(`已整理并更新 ${result.auto_committed} 条原子记忆`)
      } else {
        toast.info('修改已进入待确认记忆')
      }
      await refreshMemory()
    },
    onError: (error) => toast.error(error.message),
  })
  const regenerateProfile = useMutation({
    mutationFn: refreshMemoryProfile,
    onSuccess: async () => {
      toast.success('记忆摘要已整篇重写')
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, 'memory', 'profile') })
    },
    onError: (error) => toast.error(error.message),
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
    onError: (error) => toast.error(error.message),
  })

  if (memories.isPending || profile.isPending || deleted.isPending || sessions.isPending) {
    return <PageFrame><LoadingState /></PageFrame>
  }
  const firstError = memories.error ?? profile.error ?? deleted.error ?? sessions.error
  if (firstError) return <PageFrame><ErrorState message={firstError.message} /></PageFrame>

  const activeMemories = memories.data ?? []
  const deletedMemories = deleted.data ?? []
  const sessionList = sessions.data ?? []
  const goalList = goals.data ?? []
  const graphList = graphs.data ?? []
  const typeList = memoryTypes.data ?? []
  const grouped = Object.fromEntries(
    zoneDefinitions.map(({ zone }) => [zone, activeMemories.filter((item) => item.zone === zone)]),
  ) as Record<MemoryZone, MemoryEntry[]>

  const recoverableCount = deletedMemories.filter((item) => item.restore_available).length
  const zoneBusy = remove.isPending || update.isPending || restoreRevision.isPending
  const quickAdd = (
    content: string,
    selectedText?: string,
    selectedAtomIds?: string[],
  ) =>
    editProfile
      .mutateAsync({
        text: content,
        selected_text: selectedText,
        selected_atom_ids: selectedAtomIds,
        timezone_name: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
      })
      .then(() => undefined)

  return (
    <PageFrame>
      <PageIntro
        actions={
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => navigate('../settings/workspace')}
              size="sm"
              variant="outline"
            >
              <Settings2 className="size-4" />配置
            </Button>
            <Button
              disabled={exportArchive.isPending || !activeMemories.length}
              onClick={() => exportArchive.mutate()}
              size="sm"
              variant="outline"
            >
              <Download className="size-4" />导出 Markdown
            </Button>
            <CreateMemoryDialog
              busy={create.isPending}
              goals={goalList}
              graphs={graphList}
              onCreate={(payload) => create.mutateAsync(payload).then(() => undefined)}
              sessions={sessionList}
              types={typeList}
              workspaceId={workspaceId}
            />
          </div>
        }
        description="原始消息先被整理为可追溯原子，再由模型持续重写成一篇当前有效的记忆摘要。"
        eyebrow="Workspace memory"
        title="工作区记忆中心"
      />

      <MetricStrip
        items={[
          {
            label: 'Active 记忆',
            value: activeMemories.length,
            hint: '服务端 active 状态',
            tone: activeMemories.length ? 'positive' : 'default',
          },
          {
            label: '热区记忆',
            value: grouped.hot.length + grouped.recent.length,
            hint: '热摘要与近期事件',
            tone: grouped.hot.length + grouped.recent.length ? 'info' : 'default',
          },
          {
            label: '主题与归档',
            value: grouped.topics.length + grouped.archive.length,
            hint: '稳定事实与冷区记录',
          },
          {
            label: '可恢复删除',
            value: recoverableCount,
            hint: '30 分钟恢复窗口',
            tone: recoverableCount ? 'warning' : 'default',
          },
        ]}
      />

      <GoalMemoryToolsCard goals={goals.data ?? []} workspaceId={workspaceId} />

      <Tabs className="gap-4" defaultValue="now">
        <TabsList className="h-9 w-full justify-start sm:w-auto">
          <TabsTrigger className="px-3" value="now">
            现在
          </TabsTrigger>
          <TabsTrigger className="px-3" value="in-progress">
            进行中
          </TabsTrigger>
          <TabsTrigger className="px-3" value="memory">
            记忆
            <span className="ml-1.5 font-mono text-[10px] text-muted-foreground tabular-nums">
              {activeMemories.length}
            </span>
          </TabsTrigger>
          <TabsTrigger className="px-3" value="usage">
            使用记录
          </TabsTrigger>
          <TabsTrigger className="px-3" value="deleted">
            删除恢复
            {deletedMemories.length ? (
              <span className="ml-1.5 font-mono text-[10px] text-muted-foreground tabular-nums">
                {deletedMemories.length}
              </span>
            ) : null}
          </TabsTrigger>
          {memoryGovernanceV2Enabled ? (
            <TabsTrigger className="px-3" value="settings-export">
              设置与导出
            </TabsTrigger>
          ) : null}
        </TabsList>

        <TabsContent className="mt-0 outline-none" value="now">
          <MemorySummaryCard
            busy={editProfile.isPending || regenerateProfile.isPending || migrateAtoms.isPending}
            legacyCount={
              activeMemories.filter((item) => (item.atom_schema_version ?? 0) === 0).length
            }
            onMigrate={() => migrateAtoms.mutateAsync().then(() => undefined)}
            onRefresh={() => regenerateProfile.mutateAsync().then(() => undefined)}
            onQuickAdd={quickAdd}
            profile={profile.data}
          />
          {memoryGovernanceV2Enabled ? (
            <div className="mt-4">
              <MemoryPendingConfirmationPanel />
            </div>
          ) : null}
        </TabsContent>

        <TabsContent className="mt-0 outline-none" value="in-progress">
          {memoryGovernanceV2Enabled ? (
            <MemoryTaskEpisodePanel />
          ) : (
            <Surface className="p-2">
              <EmptyState
                description="任务与 Episode 视角需要启用高级记忆治理。"
                title="进行中视图暂未开启"
              />
            </Surface>
          )}
        </TabsContent>

        <TabsContent className="mt-0 outline-none" value="memory">
          {!activeMemories.length ? (
            <Surface className="p-2">
              <EmptyState
                description="新增记忆后会出现在这里；事件投影记忆同样可见。"
                title="当前工作区还没有可见记忆"
              />
            </Surface>
          ) : (
            <div className="grid gap-2">
              {activeMemories.map((item) => (
                <MemoryRow
                  busy={zoneBusy}
                  item={item}
                  key={item.id}
                  onDelete={() => remove.mutate(item.id)}
                  onOpen={() => setSelectedId(item.id)}
                  onRestoreRevision={(revision) => restoreRevision.mutate({ item, revision })}
                  onUpdate={(payload) =>
                    update.mutateAsync({ id: item.id, payload }).then(() => undefined)
                  }
                  workspaceId={workspaceId}
                />
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent className="mt-0 outline-none" value="usage">
          <Surface className="p-5">
            <SectionHeading
              description="本轮真实提供给模型的记忆回执（候选/检索/选择/注入/排除）。"
              title="使用记录"
            />
            <div className="mt-4">
              <ContextManifestPanel />
            </div>
          </Surface>
          <div className="mt-4">
            <MemoryInjectionPreviewTab sessions={sessionList} workspaceId={workspaceId} />
          </div>
        </TabsContent>

        <TabsContent className="mt-0 outline-none" value="deleted">
          <Surface className="overflow-hidden">
            <div className="border-b px-5 py-4">
              <SectionHeading
                description="30 分钟内可恢复；过窗后正文与内容密钥不可逆销毁"
                title="删除恢复窗口"
              />
            </div>
            {deletedMemories.length ? (
              <div className="divide-y">
                {deletedMemories.map((item) => (
                  <div
                    className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center"
                    key={item.id}
                  >
                    <FileClock className="size-4 shrink-0 text-amber-600" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="text-sm font-semibold">{item.title}</p>
                        <Badge variant="secondary">{item.state}</Badge>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {item.restore_available
                          ? `可恢复至 ${formatTime(item.recoverable_until)}`
                          : `正文已于 ${formatTime(item.content_destroyed_at)} 销毁`}
                      </p>
                    </div>
                    <Button
                      disabled={!item.restore_available || restore.isPending}
                      onClick={() => restore.mutate(item.id)}
                      size="sm"
                      variant="outline"
                    >
                      <RotateCcw className="size-4" />恢复
                    </Button>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState
                description="软删除的记忆会短暂出现在这里，过窗后仅保留无正文审计元数据。"
                title="当前没有删除记录"
              />
            )}
          </Surface>
          {operator.data?.is_system_admin ? <RetentionMaintenanceCard workspaceId={workspaceId} /> : null}
        </TabsContent>

        {memoryGovernanceV2Enabled ? (
          <TabsContent className="mt-0 outline-none" value="settings-export">
            <MemorySettingsExportPanel />
          </TabsContent>
        ) : null}
      </Tabs>

      <MemoryDetailDialog
        memoryId={selectedId}
        onClose={() => setSelectedId(null)}
        workspaceId={workspaceId}
      />
    </PageFrame>
  )
}

function MemorySummaryCard({
  busy,
  legacyCount,
  profile,
  onMigrate,
  onRefresh,
  onQuickAdd,
}: {
  busy: boolean
  legacyCount: number
  profile: MemoryProfile | undefined
  onMigrate: () => Promise<void>
  onRefresh: () => Promise<void>
  onQuickAdd: (
    content: string,
    selectedText?: string,
    selectedAtomIds?: string[],
  ) => Promise<void>
}) {
  const [draft, setDraft] = useState('')
  const [selection, setSelection] = useState<{
    text: string
    atomIds: string[]
  } | null>(null)
  const documentRef = useRef<HTMLDivElement>(null)
  const hint = profileUpdatedHint(profile?.generated_at ?? null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    const content = draft.trim()
    if (!content || busy) return
    try {
      await onQuickAdd(content, selection?.text, selection?.atomIds)
    } catch {
      return
    }
    setDraft('')
    setSelection(null)
    window.getSelection()?.removeAllRanges()
  }

  function captureSelection() {
    const selected = window.getSelection()
    const text = selected?.toString().trim() ?? ''
    const anchor = selected?.anchorNode
    if (!text || !anchor || !documentRef.current?.contains(anchor)) {
      setSelection(null)
      return
    }
    const element = anchor instanceof Element ? anchor : anchor.parentElement
    const paragraph = element?.closest<HTMLElement>('[data-memory-atom-ids]')
    const atomIds = paragraph?.dataset.memoryAtomIds?.split(',').filter(Boolean) ?? []
    if (!atomIds.length) {
      setSelection(null)
      return
    }
    setSelection({ text, atomIds })
  }

  return (
    <Surface className="mx-auto flex w-full max-w-4xl flex-col overflow-hidden p-0 shadow-sm">
      <div className="flex items-center gap-3 border-b px-6 py-4">
        <div className="grid size-9 place-items-center rounded-full bg-primary/10 text-primary">
          <BookOpenText className="size-4.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <h2 className="text-lg font-semibold tracking-tight">记忆摘要</h2>
            {hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
          </div>
          <p className="text-xs text-muted-foreground">
            由当前有效原子整篇重写；文件原文和助手回答不会直接成为用户记忆
          </p>
        </div>
        <Button
          aria-label="刷新记忆摘要"
          disabled={busy}
          onClick={() => void onRefresh()}
          size="icon-sm"
          variant="ghost"
        >
          <RefreshCw className={busy ? 'size-4 animate-spin' : 'size-4'} />
        </Button>
      </div>

      <div
        className="min-h-[420px] max-h-[68vh] overflow-y-auto px-7 py-7 sm:px-10 sm:py-9"
        onMouseUp={captureSelection}
        ref={documentRef}
      >
        {profile?.status === 'stale' ? (
          <div className="mb-6 flex items-center justify-between gap-3 border-b border-amber-500/20 pb-3 text-xs text-amber-700 dark:text-amber-300">
            <span>原子记忆已变化，旧摘要不会注入对话。请刷新生成当前版本。</span>
            <Button disabled={busy} onClick={() => void onRefresh()} size="xs" variant="outline">
              立即刷新
            </Button>
          </div>
        ) : profile?.status === 'atomic_snapshot' ? (
          <div className="mb-6 flex items-start gap-2 border-b border-primary/20 pb-3 text-xs text-muted-foreground">
            <Sparkles className="mt-0.5 size-3.5 shrink-0 text-primary" />
            <span>
              当前为原子快照：未配置记忆摘要模型，先把每条记忆清晰列出。配置提取/摘要模型后，点击右上角刷新生成正式摘要。
            </span>
          </div>
        ) : null}

        <AnimatePresence mode="wait">
          {profile?.dimensions.length ? (
            <motion.article
              animate={{ opacity: 1, y: 0 }}
              className="space-y-8"
              exit={{ opacity: 0, y: -6 }}
              initial={{ opacity: 0, y: 8 }}
              key={profile.version}
              transition={{ duration: 0.22, ease: 'easeOut' }}
            >
              {profile.overview ? (
                <section>
                  <h3 className="mb-2.5 text-[17px] font-semibold tracking-tight">概览</h3>
                  <div className="space-y-3">
                    <p className="selection:bg-primary/20 text-[15px] leading-7 text-foreground/88">
                      {profile.overview}
                    </p>
                  </div>
                </section>
              ) : null}
              {profile.dimensions.map((dimension, dimensionIndex) => (
                <section key={dimension.key || `${dimension.title}-${dimensionIndex}`}>
                  <h3 className="mb-2.5 text-[17px] font-semibold tracking-tight">
                    {dimension.title}
                  </h3>
                  <div className="space-y-3">
                    {dimension.paragraphs.map((paragraph, paragraphIndex) => (
                      <p
                        className="selection:bg-primary/20 text-[15px] leading-7 text-foreground/88"
                        data-memory-atom-ids={paragraph.atom_ids.join(',')}
                        key={paragraph.id ?? `${dimensionIndex}-${paragraphIndex}`}
                      >
                        {paragraph.text}
                      </p>
                    ))}
                  </div>
                </section>
              ))}
            </motion.article>
          ) : profile?.structured_sections.length ? (
            <motion.article
              animate={{ opacity: 1, y: 0 }}
              className="space-y-8"
              exit={{ opacity: 0, y: -6 }}
              initial={{ opacity: 0, y: 8 }}
              key={profile.version}
              transition={{ duration: 0.22, ease: 'easeOut' }}
            >
              {profile.structured_sections.map((section, sectionIndex) => (
                <section key={`${section.heading}-${sectionIndex}`}>
                  <h3 className="mb-2.5 text-[17px] font-semibold tracking-tight">
                    {section.heading}
                  </h3>
                  <div className="space-y-3">
                    {section.paragraphs.map((paragraph, paragraphIndex) => (
                      <p
                        className="selection:bg-primary/20 text-[15px] leading-7 text-foreground/88"
                        data-memory-atom-ids={paragraph.atom_ids.join(',')}
                        key={paragraph.id ?? `${sectionIndex}-${paragraphIndex}`}
                      >
                        {paragraph.text}
                      </p>
                    ))}
                  </div>
                </section>
              ))}
            </motion.article>
          ) : profile?.markdown ? (
            <motion.div
              animate={{ opacity: 1 }}
              initial={{ opacity: 0 }}
              key={profile.version}
            >
              <MessageResponse className="text-[15px] leading-7 text-foreground/88">
                {profile.markdown}
              </MessageResponse>
            </motion.div>
          ) : (
            <motion.div animate={{ opacity: 1 }} initial={{ opacity: 0 }}>
              <EmptyState
                description="在下方告诉我值得长期记住的事实、偏好或变化。内容会先被整理为原子，再写入这篇摘要。"
                title="还没有可生成摘要的原子记忆"
              />
              {legacyCount ? (
                <div className="mt-4 flex justify-center">
                  <Button
                    disabled={busy}
                    onClick={() => void onMigrate()}
                    size="sm"
                    variant="outline"
                  >
                    <Sparkles className="size-3.5" />
                    整理 {legacyCount} 条旧记忆
                  </Button>
                </div>
              ) : null}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <form className="border-t bg-muted/15 px-4 py-3.5" onSubmit={(event) => void submit(event)}>
        <AnimatePresence initial={false}>
          {selection ? (
            <motion.div
              animate={{ height: 'auto', opacity: 1, y: 0 }}
              className="mx-2 mb-2 flex items-center gap-2 overflow-hidden text-xs text-muted-foreground"
              exit={{ height: 0, opacity: 0, y: 4 }}
              initial={{ height: 0, opacity: 0, y: 4 }}
            >
              <Pencil className="size-3.5 text-primary" />
              <span className="min-w-0 flex-1 truncate">
                正在纠正：“{selection.text}”
              </span>
              <button
                className="shrink-0 hover:text-foreground"
                onClick={() => setSelection(null)}
                type="button"
              >
                取消
              </button>
            </motion.div>
          ) : null}
        </AnimatePresence>
        <div className="flex items-center gap-2 rounded-[1.4rem] border bg-background py-1.5 pl-5 pr-1.5 shadow-sm transition-[border-color,box-shadow] focus-within:border-primary/40 focus-within:shadow-md">
          <input
            aria-label="添加或更新记忆"
            className="h-9 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            maxLength={2000}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="添加或更新"
            value={draft}
          />
          <motion.div
            whileHover={busy || !draft.trim() ? undefined : { scale: 1.04 }}
            whileTap={busy || !draft.trim() ? undefined : { scale: 0.96 }}
          >
            <Button
              aria-label="提交记忆"
              className="size-9 rounded-full"
              disabled={busy || !draft.trim()}
              size="icon"
              type="submit"
            >
              <ArrowUp className="size-4" />
            </Button>
          </motion.div>
        </div>
      </form>
    </Surface>
  )
}

function MemoryInjectionPreviewTab({
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
      await queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, 'memory', 'package'),
      })
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
        description="透明化：查看所选 Session 下一轮对话实际会注入哪些记忆"
        title="AI 眼中的我"
      />
      <div className="mt-4 flex flex-col gap-2 border-t pt-4 sm:flex-row sm:items-center">
        <SessionCombobox
          className="sm:max-w-sm"
          onChange={setSessionId}
          sessions={sessions}
          value={sessionId}
        />
        <div className="flex flex-wrap gap-2">
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
          {contextSummary.data ? <span className="text-xs text-muted-foreground">v{contextSummary.data.version}</span> : null}
        </div>
        {contextSummary.isLoading ? <p className="mt-2 text-xs text-muted-foreground">正在读取摘要…</p> : contextSummary.isError ? <p className="mt-2 text-xs text-destructive">摘要读取失败：{contextSummary.error.message}</p> : contextSummary.data ? <div className="mt-3 space-y-2"><p className="whitespace-pre-wrap text-sm leading-6">{contextSummary.data.summary}</p><p className="text-[11px] text-muted-foreground">覆盖 {contextSummary.data.source_message_ids.length} 条消息 · {contextSummary.data.estimated_tokens_before} → {contextSummary.data.estimated_tokens_after} tokens</p></div> : <p className="mt-2 text-xs text-muted-foreground">当前 Session 尚未生成会话摘要。</p>}
      </div>
      {preview.isLoading ? (
        <p className="mt-4 text-xs text-muted-foreground">加载中…</p>
      ) : preview.isError ? (
        <p className="mt-4 text-xs text-destructive">{preview.error.message}</p>
      ) : preview.data ? (
        <div className="mt-4 space-y-3 border-t pt-4">
          <p className="text-xs text-muted-foreground">
            命中 {preview.data.effective_memories.length} 条 · 估算{' '}
            {preview.data.token_estimate} tokens
            {preview.data.conflicts.length
              ? ` · ${preview.data.conflicts.length} 处作用域覆盖`
              : ''}
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
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border bg-muted/25 p-3 text-[10px] leading-4 text-muted-foreground">
              {preview.data.prompt_block}
            </pre>
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
      // Prefix match: every memory* entry for the active workspace.
      await queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, 'memory'),
      })
    },
    onError: (error) => toast.error(error.message),
  })

  return (
    <Surface className="mt-4 border-amber-200 bg-amber-50/35 p-4 dark:border-amber-900 dark:bg-amber-950/15">
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
            <AlertDialogMedia className="bg-amber-500/10 text-amber-700">
              <FileClock />
            </AlertDialogMedia>
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

function MemoryRow({
  item,
  busy,
  workspaceId,
  onOpen,
  onUpdate,
  onDelete,
  onRestoreRevision,
}: {
  item: MemoryEntry
  busy: boolean
  workspaceId: string
  onOpen: () => void
  onUpdate: (payload: Parameters<typeof updateMemory>[1]) => Promise<void>
  onDelete: () => void
  onRestoreRevision: (revision: number) => void
}) {
  const readOnly = item.view_source === 'event'
  return (
    <div className="rounded-xl border bg-background/80 p-3 shadow-sm transition-colors hover:border-primary/30">
      <button className="w-full text-left" onClick={onOpen} type="button">
        <p className="line-clamp-2 text-sm font-medium leading-5 hover:text-primary">{item.title}</p>
        <p className="mt-1.5 truncate font-mono text-[10px] text-muted-foreground">
          rev {item.revision} · {item.lg_memory_id}
        </p>
      </button>
      <div className="mt-2.5 flex flex-wrap items-center gap-1">
        <Badge variant="outline">{item.namespace === 'session' ? 'Session' : 'Workspace'}</Badge>
        {item.scope_type ? <Badge variant="secondary">{item.scope_type}</Badge> : null}
        {item.record_kind ? <Badge variant="outline">{item.record_kind}</Badge> : null}
        {readOnly ? <Badge variant="outline">事件派生只读</Badge> : null}
      </div>
      {readOnly ? (
        <div className="mt-2.5 border-t pt-2 text-xs text-muted-foreground">
          该记忆来自事件投影，先在对话中确认或通过记忆治理流程创建记录后可编辑。
        </div>
      ) : (
      <div className="mt-2.5 flex flex-wrap items-center gap-0.5 border-t pt-2">
        <EditMemoryDialog busy={busy} item={item} onUpdate={onUpdate} workspaceId={workspaceId} />
        <RevisionDialog
          busy={busy}
          item={item}
          onRestore={onRestoreRevision}
          workspaceId={workspaceId}
        />
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button aria-label={`删除 ${item.title}`} disabled={busy} size="icon-xs" variant="ghost">
              <Trash2 className="size-3.5 text-destructive" />
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogMedia className="bg-destructive/10 text-destructive">
                <Trash2 />
              </AlertDialogMedia>
              <AlertDialogTitle>删除“{item.title}”？</AlertDialogTitle>
              <AlertDialogDescription>
                删除会同步移除当前 Provider 投影。30 分钟内可以直接恢复；之后内容密钥销毁，正文不可恢复，Journal 只短期保留无正文审计元数据。
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
      )}
    </div>
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
        <Label>知识作用域</Label>
        <Select onValueChange={(value) => onScopeTypeChange(value as MemoryScopeType)} value={scopeType}>
          <SelectTrigger><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="workspace">Workspace</SelectItem>
            <SelectItem value="goal">Goal</SelectItem>
            <SelectItem value="node">Node</SelectItem>
            <SelectItem value="session">Session</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {scopeType === 'goal' || scopeType === 'node' ? (
        <div className="space-y-2">
          <Label>Goal</Label>
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
          <Label>Node</Label>
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
            <p className="text-xs text-muted-foreground">工作区尚无 Goal；请先在目标页创建并发布图谱。</p>
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
      <DialogTrigger asChild><Button size="sm"><Plus className="size-4" />新增记忆</Button></DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <form onSubmit={(event) => void submit(event)}>
          <DialogHeader>
            <DialogTitle>新增确认记忆</DialogTitle>
            <DialogDescription>
              保存后生成永久 lg_memory_id 和 Revision 1。Goal/Node 作用域必须选择工作区内真实资源。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-5">
            <div className="space-y-2">
              <Label htmlFor="memory-title">标题</Label>
              <Input id="memory-title" maxLength={240} onChange={(event) => setTitle(event.target.value)} value={title} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="memory-content">Markdown 内容</Label>
              <Textarea className="min-h-32" id="memory-content" maxLength={50_000} onChange={(event) => setContent(event.target.value)} value={content} />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="memory-zone">冷热层级</Label>
                <Select onValueChange={(value) => setZone(value as MemoryZone)} value={zone}>
                  <SelectTrigger id="memory-zone"><SelectValue /></SelectTrigger>
                  <SelectContent>{zoneDefinitions.map((item) => <SelectItem key={item.zone} value={item.zone}>{item.title}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="memory-namespace">会话命名空间</Label>
                <Select onValueChange={(value) => setNamespace(value as MemoryNamespace)} value={namespace}>
                  <SelectTrigger id="memory-namespace"><SelectValue /></SelectTrigger>
                  <SelectContent><SelectItem value="workspace">Workspace</SelectItem><SelectItem value="session">Session</SelectItem></SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-2">
              <Label>记忆类型</Label>
              <Select onValueChange={(value) => setRecordKind(value ?? 'semantic_memory')} value={recordKind}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(types.length ? types : [{ memory_type: 'semantic_memory', description: '默认' }]).map((item) => (
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
                <Label htmlFor="memory-scope-session">所属 Session</Label>
                <Select onValueChange={(value) => setSessionId(value ?? '')} value={sessionId}>
                  <SelectTrigger id="memory-scope-session"><SelectValue placeholder="选择 Session" /></SelectTrigger>
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
              {busy ? '保存中…' : '保存 Revision 1'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function EditMemoryDialog({
  item,
  busy,
  workspaceId,
  onUpdate,
}: {
  item: MemoryEntry
  busy: boolean
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
      toast.error('保存未完成：记忆可能已在其他窗口更新，已刷新为最新 Revision 后请检查并重试。')
      return
    }
    setOpen(false)
  }

  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild><Button size="xs" variant="ghost"><Pencil className="size-3" />编辑</Button></DialogTrigger>
      <DialogContent>
        <form onSubmit={(event) => void submit(event)}>
          <DialogHeader>
            <DialogTitle>编辑记忆 · revision {item.revision}</DialogTitle>
            <DialogDescription>保存使用 Revision CAS；若其他窗口已提交，会返回冲突而不是覆盖。</DialogDescription>
          </DialogHeader>
          {detail.isPending ? <LoadingState /> : detail.isError ? <ErrorState message={detail.error.message} /> : (
            <div className="space-y-4 py-5">
              <div className="space-y-2"><Label htmlFor={`memory-title-${item.id}`}>标题</Label><Input id={`memory-title-${item.id}`} onChange={(event) => setTitle(event.target.value)} value={title} /></div>
              <div className="space-y-2"><Label htmlFor={`memory-content-${item.id}`}>Markdown 内容</Label><Textarea className="min-h-48 font-mono text-xs" id={`memory-content-${item.id}`} onChange={(event) => setContent(event.target.value)} value={content} /></div>
              <div className="space-y-2"><Label htmlFor={`memory-zone-${item.id}`}>冷热层级</Label><Select onValueChange={(value) => setZone(value as MemoryZone)} value={zone}><SelectTrigger id={`memory-zone-${item.id}`}><SelectValue /></SelectTrigger><SelectContent>{zoneDefinitions.map((definition) => <SelectItem key={definition.zone} value={definition.zone}>{definition.title}</SelectItem>)}</SelectContent></Select></div>
            </div>
          )}
          <DialogFooter><Button disabled={busy || detail.isPending || !title.trim() || !content.trim()} type="submit">保存新 Revision</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function RevisionDialog({
  item,
  busy,
  workspaceId,
  onRestore,
}: {
  item: MemoryEntry
  busy: boolean
  workspaceId: string
  onRestore: (revision: number) => void
}) {
  const [open, setOpen] = useState(false)
  const revisions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'revisions', item.id),
    queryFn: () => listMemoryRevisions(item.id),
    enabled: open,
  })
  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild><Button size="xs" variant="ghost"><History className="size-3" />历史</Button></DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revision 历史 · {item.title}</DialogTitle>
          <DialogDescription>恢复旧版不会覆盖历史，而是基于旧内容创建新的 Revision。</DialogDescription>
        </DialogHeader>
        {revisions.isPending ? <LoadingState /> : revisions.isError ? <ErrorState message={revisions.error.message} /> : (
          <div className="max-h-[55vh] divide-y overflow-auto border-y">
            {revisions.data.map((revision) => (
              <RevisionRow
                busy={busy}
                currentRevision={item.revision}
                key={revision.id}
                onRestore={onRestore}
                revision={revision}
              />
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function RevisionRow({
  revision,
  currentRevision,
  busy,
  onRestore,
}: {
  revision: MemoryRevision
  currentRevision: number
  busy: boolean
  onRestore: (revision: number) => void
}) {
  return (
    <div className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2"><p className="font-mono text-xs">revision {revision.revision}</p><Badge variant="outline">{revision.operation}</Badge>{revision.is_active ? <Badge>active</Badge> : null}</div>
        <p className="mt-2 line-clamp-2 text-xs leading-5 text-muted-foreground">{revision.content ?? '正文已按保留策略销毁'}</p>
        <p className="mt-1 text-[10px] text-muted-foreground">{formatTime(revision.created_at)} · {revision.reason}</p>
      </div>
      <Button disabled={busy || revision.revision === currentRevision || revision.content === null} onClick={() => onRestore(revision.revision)} size="sm" variant="outline"><RotateCcw className="size-4" />恢复此版</Button>
    </div>
  )
}

function MemoryDetailDialog({
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
  const bindings = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'memory', 'bindings', memoryId),
    queryFn: () => listMemoryBindings(memoryId ?? ''),
    enabled: Boolean(memoryId),
  })
  const metadata = memory.data ? [
    ['lg_memory_id', memory.data.lg_memory_id],
    ['revision', String(memory.data.revision)],
    ['zone', memory.data.zone],
    ['scope', memory.data.session_id ?? memory.data.namespace],
    ['provider', memory.data.provider_id],
    ['sha256', memory.data.content_hash],
  ] : []
  return (
    <Dialog onOpenChange={(open) => !open && onClose()} open={Boolean(memoryId)}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{memory.data?.title ?? '记忆详情'}</DialogTitle>
          <DialogDescription>Canonical 内容与 Provider Binding 元数据</DialogDescription>
        </DialogHeader>
        {memory.isPending ? <LoadingState /> : memory.isError ? <ErrorState message={memory.error.message} /> : memory.data ? (
          <div className="space-y-4">
            <dl className="grid gap-x-4 gap-y-2 rounded-xl bg-muted/45 p-4 text-xs sm:grid-cols-[110px_1fr]">
              {metadata.map(([key, value]) => <div className="contents" key={key}><dt className="text-muted-foreground">{key}</dt><dd className="break-all font-mono">{value}</dd></div>)}
            </dl>
            <pre className="max-h-[42vh] overflow-auto whitespace-pre-wrap rounded-xl border p-4 font-mono text-xs leading-6">{memory.data.content ?? '正文不可用'}</pre>
            <MemoryGovernancePanel memory={memory.data} />
            <section>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold">Provider Binding</p>
                <p className="text-xs text-muted-foreground">Binding 可重建，不能替代 lg_memory_id、Revision 或 ACL。</p>
              </div>
              {bindings.isPending ? <p className="mt-3 text-xs text-muted-foreground">正在读取服务端 Binding…</p> : bindings.isError ? (
                <p className="mt-3 text-xs text-destructive">Binding 读取失败：{bindings.error.message}</p>
              ) : (
                <div className="mt-3 max-h-56 divide-y overflow-auto rounded-xl border">
                  {(bindings.data ?? []).map((binding) => (
                    <div className="p-3 text-xs" key={binding.id}>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono">revision {binding.revision}</span>
                        <StatePill status={binding.binding_status} />
                        <span className="text-muted-foreground">验证：{formatTime(binding.verified_at)}</span>
                      </div>
                      <dl className="mt-2 grid gap-x-3 gap-y-1 sm:grid-cols-[110px_1fr]">
                        <dt className="text-muted-foreground">provider record</dt><dd className="break-all font-mono">{binding.provider_record_id}</dd>
                        <dt className="text-muted-foreground">entity</dt><dd className="break-all font-mono">{binding.provider_entity_kind} / {binding.provider_entity_value}</dd>
                        <dt className="text-muted-foreground">write/readback hash</dt><dd className="break-all font-mono">{binding.source_content_hash} / {binding.target_readback_hash}</dd>
                        {binding.import_event_id ? <><dt className="text-muted-foreground">import event</dt><dd className="break-all font-mono">{binding.import_event_id}</dd></> : null}
                        {binding.last_error ? <><dt className="text-muted-foreground">last error</dt><dd className="break-all text-destructive">{binding.last_error}</dd></> : null}
                      </dl>
                    </div>
                  ))}
                  {!(bindings.data ?? []).length ? <p className="p-4 text-xs text-muted-foreground">该记忆尚无 Provider Binding 记录。</p> : null}
                </div>
              )}
            </section>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
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
      await queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, 'goals'),
      })
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
        <div className="flex flex-wrap items-center gap-2">
          <Select value={selectedGoalId} onValueChange={setSelectedGoalId}>
            <SelectTrigger className="w-52"><SelectValue placeholder="选择目标" /></SelectTrigger>
            <SelectContent>
              {goals.map((goal) => (
                <SelectItem key={goal.id} value={goal.id}>{goal.title || goal.id}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Dialog open={overviewOpen} onOpenChange={setOverviewOpen}>
            <DialogTrigger asChild>
              <Button
                disabled={!selectedGoalId}
                onClick={() => setOverviewOpen(true)}
                type="button"
                variant="outline"
              >
                <Eye className="mr-1.5 h-4 w-4" />
                生成总览
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
                <Archive className="mr-1.5 h-4 w-4" />
                {archive.isPending ? '归档中…' : '归档 Goal 记忆'}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
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
