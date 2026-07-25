import { type FormEvent, useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Archive,
  Check,
  Clock3,
  Download,
  Eye,
  FileClock,
  FileText,
  History,
  Inbox,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react'
import { toast } from 'sonner'

import {
  createMemory,
  createMemoryDraft,
  decideMemoryDraft,
  deleteMemory,
  exportMemoryMarkdown,
  getCurrentUser,
  getGraph,
  getMemory,
  getMemoryPolicy,
  getMemoryProviderStatus,
  listGoals,
  listGraphs,
  listMemoryBindings,
  listMemoryDrafts,
  listMemories,
  listMemoryRevisions,
  listMemoryTypes,
  listSessions,
  probeMemoryProvider,
  purgeExpiredMemoryContent,
  restoreDeletedMemory,
  restoreMemoryRevision,
  updateMemory,
  updateMemoryPolicy,
} from '@/api'
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
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import type {
  MemoryCreateRequest,
  MemoryDraft,
  MemoryDraftCreateRequest,
  MemoryEntry,
  MemoryNamespace,
  MemoryRevision,
  MemoryScopeType,
  MemoryZone,
} from '@/types/memory'
import type { Goal } from '@/types/goals'
import type { GraphNode, GraphSummary } from '@/types/graphs'
import type { Session } from '@/types/sessions'

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
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedSessionId, setSelectedSessionId] = useState('')
  const memories = useQuery({ queryKey: ['memory', 'active'], queryFn: () => listMemories() })
  const drafts = useQuery({
    queryKey: ['memory', 'drafts', 'PENDING'],
    queryFn: () => listMemoryDrafts({ status: 'PENDING' }),
  })
  const memoryTypes = useQuery({ queryKey: ['memory', 'types'], queryFn: listMemoryTypes })
  const goals = useQuery({ queryKey: ['goals'], queryFn: listGoals })
  const graphs = useQuery({ queryKey: ['graphs'], queryFn: listGraphs })
  const deleted = useQuery({
    queryKey: ['memory', 'deleted'],
    queryFn: async () => [
      ...(await listMemories({ state: 'deleted' })),
      ...(await listMemories({ state: 'destroyed' })),
    ],
  })
  const provider = useQuery({ queryKey: ['memory-provider'], queryFn: getMemoryProviderStatus })
  const policy = useQuery({ queryKey: ['memory-policy'], queryFn: () => getMemoryPolicy() })
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: listSessions })
  const operator = useQuery({ queryKey: ['current-user'], queryFn: getCurrentUser })
  const sessionPolicy = useQuery({
    queryKey: ['memory-policy', selectedSessionId],
    queryFn: () => getMemoryPolicy(selectedSessionId),
    enabled: Boolean(selectedSessionId),
  })

  useEffect(() => {
    if (!selectedSessionId && sessions.data?.[0]) setSelectedSessionId(sessions.data[0].id)
  }, [selectedSessionId, sessions.data])

  const refreshMemory = async () => {
    await queryClient.invalidateQueries({ queryKey: ['memory'] })
  }
  const create = useMutation({
    mutationFn: createMemory,
    onSuccess: async (item) => {
      toast.success(`已创建 ${item.lg_memory_id} · revision ${item.revision}`)
      await refreshMemory()
    },
    onError: (error) => toast.error(error.message),
  })
  const createDraft = useMutation({
    mutationFn: createMemoryDraft,
    onSuccess: async (item) => {
      toast.success(
        item.status === 'COMMITTED'
          ? `草稿已自动提交 · ${item.result_memory_id ?? item.id}`
          : `草稿待审核 · ${item.id}`,
      )
      await refreshMemory()
      await queryClient.invalidateQueries({ queryKey: ['memory', 'drafts'] })
    },
    onError: (error) => toast.error(error.message),
  })
  const decideDraft = useMutation({
    mutationFn: ({
      id,
      decision,
      reason,
    }: {
      id: string
      decision: 'commit' | 'reject'
      reason?: string
    }) => decideMemoryDraft(id, { decision, reason }),
    onSuccess: async (item) => {
      toast.success(item.status === 'COMMITTED' ? '草稿已提交为正式记忆' : '草稿已拒绝')
      await refreshMemory()
      await queryClient.invalidateQueries({ queryKey: ['memory', 'drafts'] })
    },
    onError: (error) => toast.error(error.message),
  })
  const update = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Parameters<typeof updateMemory>[1] }) =>
      updateMemory(id, payload),
    onSuccess: async (item) => {
      toast.success(`Revision ${item.revision} 已保存`)
      await refreshMemory()
      await queryClient.invalidateQueries({ queryKey: ['memory-detail', item.id] })
      await queryClient.invalidateQueries({ queryKey: ['memory-revisions', item.id] })
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
      await queryClient.invalidateQueries({ queryKey: ['memory-revisions', item.id] })
    },
    onError: (error) => toast.error(error.message),
  })
  const savePolicy = useMutation({
    mutationFn: updateMemoryPolicy,
    onSuccess: async () => {
      toast.success('共同记忆策略已持久化')
      await queryClient.invalidateQueries({ queryKey: ['memory-policy'] })
      await queryClient.invalidateQueries({ queryKey: ['sessions'] })
    },
    onError: (error) => toast.error(error.message),
  })
  const probe = useMutation({
    mutationFn: probeMemoryProvider,
    onSuccess: async (result) => {
      toast.success(`Provider 探测：${result.status}`)
      await queryClient.invalidateQueries({ queryKey: ['memory-provider'] })
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
  const purge = useMutation({
    mutationFn: purgeExpiredMemoryContent,
    onSuccess: async (result) => {
      toast.success(
        `维护完成：销毁 ${result.content_keys_destroyed} 个到期内容密钥，清理 ${result.journal_entries_removed} 条 Journal`,
      )
      await refreshMemory()
      await queryClient.invalidateQueries({ queryKey: ['memory-detail'] })
      await queryClient.invalidateQueries({ queryKey: ['memory-bindings'] })
    },
    onError: (error) => toast.error(error.message),
  })

  if (
    memories.isPending ||
    deleted.isPending ||
    provider.isPending ||
    policy.isPending ||
    sessions.isPending ||
    drafts.isPending
  ) {
    return <PageFrame><LoadingState /></PageFrame>
  }
  const firstError =
    memories.error ?? deleted.error ?? provider.error ?? policy.error ?? sessions.error ?? drafts.error
  if (firstError) return <PageFrame><ErrorState message={firstError.message} /></PageFrame>
  if (!provider.data || !policy.data) {
    return <PageFrame><ErrorState message="服务端未返回记忆 Provider 或策略状态" /></PageFrame>
  }

  const activeMemories = memories.data ?? []
  const deletedMemories = deleted.data ?? []
  const pendingDrafts = drafts.data ?? []
  const sessionList = sessions.data ?? []
  const goalList = goals.data ?? []
  const graphList = graphs.data ?? []
  const typeList = memoryTypes.data ?? []
  const grouped = Object.fromEntries(
    zoneDefinitions.map(({ zone }) => [zone, activeMemories.filter((item) => item.zone === zone)]),
  ) as Record<MemoryZone, MemoryEntry[]>

  const recoverableCount = deletedMemories.filter((item) => item.restore_available).length
  const zoneBusy = remove.isPending || update.isPending || restoreRevision.isPending

  return (
    <PageFrame>
      <PageIntro
        actions={
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={exportArchive.isPending || !activeMemories.length}
              onClick={() => exportArchive.mutate()}
              size="sm"
              variant="outline"
            >
              <Download className="size-4" />导出 Markdown
            </Button>
            <CreateDraftDialog
              busy={createDraft.isPending}
              goals={goalList}
              graphs={graphList}
              onCreate={(payload) => createDraft.mutateAsync(payload).then(() => undefined)}
              sessions={sessionList}
              types={typeList}
            />
            <CreateMemoryDialog
              busy={create.isPending}
              goals={goalList}
              graphs={graphList}
              onCreate={(payload) => create.mutateAsync(payload).then(() => undefined)}
              sessions={sessionList}
              types={typeList}
            />
          </div>
        }
        description="记忆 ID、Revision、来源和删除恢复状态都由服务端持久化；草稿需审核后才进入 Active Memory。"
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
            label: '待审核草稿',
            value: pendingDrafts.length,
            hint: '提交后进入 Active',
            tone: pendingDrafts.length ? 'warning' : 'default',
          },
          {
            label: '可恢复删除',
            value: recoverableCount,
            hint: '30 分钟恢复窗口',
            tone: recoverableCount ? 'warning' : 'default',
          },
          {
            label: 'Provider',
            value: provider.data.remote_capability ? '远程' : '本地',
            hint: provider.data.display_name,
            tone: provider.data.status.includes('healthy') ? 'positive' : 'info',
          },
        ]}
      />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0">
          <Tabs className="gap-4" defaultValue="zones">
            <TabsList className="h-9 w-full justify-start sm:w-auto">
              <TabsTrigger className="px-3" value="zones">
                冷热分层
                <span className="ml-1.5 font-mono text-[10px] text-muted-foreground tabular-nums">
                  {activeMemories.length}
                </span>
              </TabsTrigger>
              <TabsTrigger className="px-3" value="drafts">
                待审核草稿
                {pendingDrafts.length ? (
                  <span className="ml-1.5 font-mono text-[10px] text-muted-foreground tabular-nums">
                    {pendingDrafts.length}
                  </span>
                ) : null}
              </TabsTrigger>
              <TabsTrigger className="px-3" value="deleted">
                删除恢复
                {deletedMemories.length ? (
                  <span className="ml-1.5 font-mono text-[10px] text-muted-foreground tabular-nums">
                    {deletedMemories.length}
                  </span>
                ) : null}
              </TabsTrigger>
            </TabsList>

            <TabsContent className="mt-0 outline-none" value="zones">
              {!activeMemories.length ? (
                <Surface className="p-2">
                  <EmptyState
                    description="新增确认记忆或审核草稿后，会按所选层级写入真实 Provider。"
                    title="当前工作区还没有 Active 记忆"
                  />
                </Surface>
              ) : (
                <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-4">
                  {zoneDefinitions.map(({ zone, title, description, icon: Icon }) => {
                    const items = grouped[zone]
                    return (
                      <Surface className="flex min-h-0 flex-col overflow-hidden p-0" key={zone}>
                        <div className="flex items-start gap-3 border-b bg-muted/25 px-4 py-3.5">
                          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
                            <Icon className="size-4" />
                          </span>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <h2 className="truncate text-sm font-semibold">{title}</h2>
                              <Badge className="font-mono text-[10px]" variant="outline">
                                {items.length}
                              </Badge>
                            </div>
                            <p className="mt-0.5 truncate text-xs text-muted-foreground">{description}</p>
                          </div>
                        </div>
                        <div className="flex flex-1 flex-col gap-2 p-3">
                          {items.map((item) => (
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
                            />
                          ))}
                          {!items.length ? (
                            <div className="grid flex-1 place-items-center rounded-xl border border-dashed px-3 py-10 text-center text-xs text-muted-foreground">
                              此层暂无真实记忆
                            </div>
                          ) : null}
                        </div>
                      </Surface>
                    )
                  })}
                </div>
              )}
            </TabsContent>

            <TabsContent className="mt-0 outline-none" value="drafts">
              <Surface className="overflow-hidden">
                <div className="flex flex-wrap items-start justify-between gap-3 border-b px-5 py-4">
                  <SectionHeading
                    description="Agent / 子会话提出的变更缓冲层；提交后才写入正式记忆与 Journal"
                    title={`待审核草稿 · ${pendingDrafts.length}`}
                  />
                  <Button
                    disabled={drafts.isFetching}
                    onClick={() => void queryClient.invalidateQueries({ queryKey: ['memory', 'drafts'] })}
                    size="sm"
                    variant="outline"
                  >
                    <RefreshCw className={drafts.isFetching ? 'size-4 animate-spin' : 'size-4'} />
                    刷新草稿
                  </Button>
                </div>
                {pendingDrafts.length ? (
                  <div className="divide-y">
                    {pendingDrafts.map((draft) => (
                      <DraftReviewRow
                        busy={decideDraft.isPending}
                        draft={draft}
                        key={draft.id}
                        onCommit={() =>
                          decideDraft.mutate({
                            id: draft.id,
                            decision: 'commit',
                            reason: 'user_review_commit',
                          })
                        }
                        onReject={() =>
                          decideDraft.mutate({
                            id: draft.id,
                            decision: 'reject',
                            reason: 'user_review_reject',
                          })
                        }
                      />
                    ))}
                  </div>
                ) : (
                  <EmptyState
                    description="Agent 或「提出草稿」会把候选变更放在这里，提交后才进入 Active Memory。"
                    title="当前没有待审核草稿"
                  />
                )}
              </Surface>
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
            </TabsContent>
          </Tabs>
        </div>

        <aside className="flex min-w-0 flex-col gap-4 xl:sticky xl:top-5 xl:self-start">
          <Surface className="p-4">
            <div className="flex items-start justify-between gap-3">
              <SectionHeading
                description={`Epoch ${provider.data.provider_epoch} · ${provider.data.provider_type}`}
                title="Provider 状态"
              />
              <StatePill status={provider.data.status} />
            </div>
            <div className="mt-4 space-y-2 border-t pt-4">
              <p className="text-sm font-semibold">{provider.data.display_name}</p>
              <p className="break-all font-mono text-[10px] text-muted-foreground">
                {provider.data.provider_id}
              </p>
              <p className="text-xs leading-5 text-muted-foreground">
                {provider.data.remote_capability
                  ? '远程 Provider；不可用时操作会显式失败，不回退本地伪装成功。'
                  : '本地 Markdown Provider；SQLite Journal 保持业务身份与历史权威。'}
              </p>
            </div>
            <Button
              className="mt-4 w-full"
              disabled={probe.isPending}
              onClick={() => probe.mutate()}
              size="sm"
              variant="outline"
            >
              <RefreshCw className={probe.isPending ? 'size-4 animate-spin' : 'size-4'} />
              健康探测
            </Button>
          </Surface>

          <Surface className="p-4">
            <SectionHeading
              description="工作区总开关与 Session 开关必须同时启用"
              title="共同记忆策略"
            />
            <div className="mt-4 flex items-start justify-between gap-3 border-t pt-4">
              <div className="min-w-0">
                <Label htmlFor="workspace-memory">工作区共同记忆</Label>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  关闭后任何 Session 都不能跨会话注入。
                </p>
              </div>
              <Switch
                checked={policy.data.workspace_enabled}
                disabled={savePolicy.isPending}
                id="workspace-memory"
                onCheckedChange={(workspace_enabled) => savePolicy.mutate({ workspace_enabled })}
              />
            </div>
            <div className="mt-4 space-y-3 border-t pt-4">
              <div className="space-y-2">
                <Label htmlFor="memory-session">Session 策略</Label>
                <Select
                  onValueChange={(value) => setSelectedSessionId(value ?? '')}
                  value={selectedSessionId}
                >
                  <SelectTrigger id="memory-session">
                    <SelectValue placeholder="选择 Session" />
                  </SelectTrigger>
                  <SelectContent>
                    {sessionList.map((session) => (
                      <SelectItem key={session.id} value={session.id}>
                        {session.title}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">
                  {sessionPolicy.data?.effective_enabled ? '当前有效' : '当前隔离'}
                </span>
                <Switch
                  checked={sessionPolicy.data?.session_enabled ?? false}
                  disabled={!selectedSessionId || sessionPolicy.isPending || savePolicy.isPending}
                  onCheckedChange={(session_enabled) =>
                    savePolicy.mutate({ session_id: selectedSessionId, session_enabled })
                  }
                />
              </div>
              {!sessionList.length ? (
                <p className="text-xs text-muted-foreground">
                  当前没有 Session；创建会话后可设置独立策略。
                </p>
              ) : sessionPolicy.isError ? (
                <p className="text-xs text-destructive">
                  Session 策略读取失败：{sessionPolicy.error.message}
                </p>
              ) : null}
            </div>
          </Surface>

          {operator.data?.is_system_admin ? (
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
                  <Button className="mt-4 w-full" disabled={purge.isPending} size="sm" variant="outline">
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
          ) : null}

          <div className="flex items-start gap-2.5 rounded-xl border bg-muted/25 px-3.5 py-3 text-xs leading-5 text-muted-foreground">
            <ShieldCheck className="mt-0.5 size-4 shrink-0 text-primary" />
            <span>
              Mem0 UUID 仅作可重建 Binding；lg_memory_id、Revision、Hash、来源与 Journal 始终是权威。
            </span>
          </div>
        </aside>
      </div>

      <MemoryDetailDialog memoryId={selectedId} onClose={() => setSelectedId(null)} />
    </PageFrame>
  )
}

function MemoryRow({
  item,
  busy,
  onOpen,
  onUpdate,
  onDelete,
  onRestoreRevision,
}: {
  item: MemoryEntry
  busy: boolean
  onOpen: () => void
  onUpdate: (payload: Parameters<typeof updateMemory>[1]) => Promise<void>
  onDelete: () => void
  onRestoreRevision: (revision: number) => void
}) {
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
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-0.5 border-t pt-2">
        <EditMemoryDialog busy={busy} item={item} onUpdate={onUpdate} />
        <RevisionDialog busy={busy} item={item} onRestore={onRestoreRevision} />
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
    </div>
  )
}

function DraftReviewRow({
  draft,
  busy,
  onCommit,
  onReject,
}: {
  draft: MemoryDraft
  busy: boolean
  onCommit: () => void
  onReject: () => void
}) {
  return (
    <div className="flex flex-col gap-3 px-5 py-4 lg:flex-row lg:items-start">
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-semibold">{draft.title || '未命名草稿'}</p>
          <Badge>{draft.operation}</Badge>
          <Badge variant="outline">{draft.memory_type}</Badge>
          <Badge variant="secondary">{draft.proposed_scope_type}</Badge>
          <Badge variant="outline">conf {draft.confidence.toFixed(2)}</Badge>
        </div>
        <p className="whitespace-pre-wrap text-xs leading-5 text-muted-foreground">
          {draft.content || '（无正文）'}
        </p>
        <p className="font-mono text-[10px] text-muted-foreground">
          {draft.id}
          {draft.goal_id ? ` · goal ${draft.goal_id}` : ''}
          {draft.node_id ? ` · node ${draft.node_id}` : ''}
          {draft.created_by ? ` · by ${draft.created_by}` : ''}
          {` · ${formatTime(draft.created_at)}`}
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        <Button disabled={busy} onClick={onCommit} size="sm">
          <Check className="size-4" />提交
        </Button>
        <Button disabled={busy} onClick={onReject} size="sm" variant="outline">
          <X className="size-4" />拒绝
        </Button>
      </div>
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
}: {
  scopeType: MemoryScopeType
  onScopeTypeChange: (value: MemoryScopeType) => void
  goalId: string
  onGoalIdChange: (value: string) => void
  nodeId: string
  onNodeIdChange: (value: string) => void
  goals: Goal[]
  graphs: GraphSummary[]
}) {
  const selectedGraphId = graphs.find((graph) => graph.goal_id === goalId)?.id ?? graphs[0]?.id ?? ''
  const graphDetail = useQuery({
    queryKey: ['graph-detail', selectedGraphId],
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
  onCreate,
}: {
  sessions: Session[]
  goals: Goal[]
  graphs: GraphSummary[]
  types: Array<{ memory_type: string; description: string }>
  busy: boolean
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

function CreateDraftDialog({
  sessions,
  goals,
  graphs,
  types,
  busy,
  onCreate,
}: {
  sessions: Session[]
  goals: Goal[]
  graphs: GraphSummary[]
  types: Array<{ memory_type: string; description: string }>
  busy: boolean
  onCreate: (payload: MemoryDraftCreateRequest) => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [memoryType, setMemoryType] = useState('ai_observation')
  const [scopeType, setScopeType] = useState<MemoryScopeType>('workspace')
  const [goalId, setGoalId] = useState('')
  const [nodeId, setNodeId] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [confidence, setConfidence] = useState('0.7')

  async function submit(event: FormEvent) {
    event.preventDefault()
    try {
      await onCreate({
        operation: 'CREATE',
        title: title.trim(),
        content: content.trim(),
        memory_type: memoryType,
        proposed_scope_type: scopeType,
        proposed_scope_id:
          scopeType === 'goal' ? goalId || undefined : scopeType === 'node' ? nodeId || undefined : undefined,
        goal_id: scopeType === 'goal' || scopeType === 'node' ? goalId || undefined : undefined,
        node_id: scopeType === 'node' ? nodeId || undefined : undefined,
        session_id: scopeType === 'session' ? sessionId || undefined : undefined,
        confidence: Number(confidence) || 0.7,
        importance: 0.55,
        auto_commit: false,
        created_by: 'user_review_ui',
      })
    } catch {
      return
    }
    setOpen(false)
    setTitle('')
    setContent('')
  }

  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline"><Inbox className="size-4" />提出草稿</Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <form onSubmit={(event) => void submit(event)}>
          <DialogHeader>
            <DialogTitle>提出记忆草稿</DialogTitle>
            <DialogDescription>
              草稿不会立即成为 Active Memory；需在上方列表中提交或拒绝。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-5">
            <div className="space-y-2">
              <Label>标题</Label>
              <Input maxLength={240} onChange={(event) => setTitle(event.target.value)} value={title} />
            </div>
            <div className="space-y-2">
              <Label>内容</Label>
              <Textarea className="min-h-32" maxLength={50_000} onChange={(event) => setContent(event.target.value)} value={content} />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>类型</Label>
                <Select onValueChange={(value) => setMemoryType(value ?? 'ai_observation')} value={memoryType}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(types.length ? types : [{ memory_type: 'ai_observation', description: '' }]).map((item) => (
                      <SelectItem key={item.memory_type} value={item.memory_type}>{item.memory_type}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>置信度</Label>
                <Input
                  max={1}
                  min={0}
                  onChange={(event) => setConfidence(event.target.value)}
                  step="0.05"
                  type="number"
                  value={confidence}
                />
              </div>
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
            />
            {scopeType === 'session' ? (
              <div className="space-y-2">
                <Label>Session</Label>
                <Select onValueChange={(value) => setSessionId(value ?? '')} value={sessionId}>
                  <SelectTrigger><SelectValue placeholder="选择 Session" /></SelectTrigger>
                  <SelectContent>
                    {sessions.map((session) => (
                      <SelectItem key={session.id} value={session.id}>{session.title}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              disabled={
                busy
                || !content.trim()
                || (scopeType === 'goal' && !goalId)
                || (scopeType === 'node' && (!goalId || !nodeId))
                || (scopeType === 'session' && !sessionId)
              }
              type="submit"
            >
              {busy ? '提交中…' : '创建待审草稿'}
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
  onUpdate,
}: {
  item: MemoryEntry
  busy: boolean
  onUpdate: (payload: Parameters<typeof updateMemory>[1]) => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const detail = useQuery({
    queryKey: ['memory-detail', item.id],
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
        expected_revision: item.revision,
        title: title.trim(),
        content: content.trim(),
        zone,
        reason: 'user_edit',
      })
    } catch {
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
  onRestore,
}: {
  item: MemoryEntry
  busy: boolean
  onRestore: (revision: number) => void
}) {
  const [open, setOpen] = useState(false)
  const revisions = useQuery({
    queryKey: ['memory-revisions', item.id],
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

function MemoryDetailDialog({ memoryId, onClose }: { memoryId: string | null; onClose: () => void }) {
  const memory = useQuery({
    queryKey: ['memory-detail', memoryId],
    queryFn: () => getMemory(memoryId ?? ''),
    enabled: Boolean(memoryId),
  })
  const bindings = useQuery({
    queryKey: ['memory-bindings', memoryId],
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
      <DialogContent className="sm:max-w-2xl">
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
