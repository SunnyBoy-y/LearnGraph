import { useMemo, useState } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RotateCcw, SquareCheck, Trash2 } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { listSessionPage, type SessionPageParam, type SessionScope, type SessionSummary } from '@/api/session-browser'
import { deleteSessionsBatch, getSessionBatchDeleteImpact } from '@/api/sessions'
import { deleteSession, getSessionDeleteImpact, listProjects, restoreSession } from '@/api/workflow'
import { ClearableSearchInput } from '@/components/shared/clearable-search-input'
import { DeleteImpactDialog } from '@/components/shared/delete-impact-dialog'
import { EmptyState, PageFrame, PageIntro } from '@/components/shared/page-elements'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { useListSelection } from '@/lib/list-selection'
import { toast } from '@/lib/notifications'
import { workspaceQueryKey } from '@/lib/query-keys'
import { removeSessionsFromCaches, sessionBrowserKey, updateSessionCaches } from '@/lib/session-queries'
import { scheduleSessionRefresh } from '@/lib/session-refresh'

const archiveScope: SessionScope = { archived_only: true, roots_only: false, pinned_first: false }

export function ArchivedSessionsPage() {
  const { workspaceId = '' } = useParams()
  return <WorkspaceArchive key={workspaceId} workspaceId={workspaceId} />
}

function WorkspaceArchive({ workspaceId }: { workspaceId: string }) {
  const client = useQueryClient()
  const [search, setSearch] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<
    | { kind: 'single'; session: SessionSummary }
    | { kind: 'batch'; sessions: SessionSummary[] }
    | null
  >(null)

  const archive = useInfiniteQuery({
    queryKey: [...sessionBrowserKey(workspaceId), archiveScope],
    queryFn: ({ pageParam, signal }) => listSessionPage(archiveScope, pageParam, { signal }),
    initialPageParam: {} as SessionPageParam,
    getNextPageParam: (page): SessionPageParam | undefined =>
      page.has_more && page.next_cursor ? { cursor: page.next_cursor } : undefined,
    retry: false,
  })
  const projects = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'projects'),
    queryFn: () => listProjects(true),
    staleTime: 30_000,
  })

  const projectTitles = useMemo(
    () => new Map(projects.data?.map(project => [project.id, project.title])),
    [projects.data],
  )
  const sessions = useMemo(() => {
    const items = new Map(archive.data?.pages.flatMap(page => page.items).map(session => [session.id, session]))
    return [...items.values()].filter(session => session.status === 'archived')
  }, [archive.data])

  const filteredSessions = useMemo(() => {
    const q = search.trim().toLocaleLowerCase()
    if (!q) return sessions
    return sessions.filter(session => {
      const title = (session.title || '新会话').toLocaleLowerCase()
      const projectTitle = (session.project_id ? projectTitles.get(session.project_id) : '')?.toLocaleLowerCase() ?? ''
      return title.includes(q) || projectTitle.includes(q)
    })
  }, [projectTitles, search, sessions])

  const visibleIds = useMemo(() => filteredSessions.map(s => s.id), [filteredSessions])
  const selection = useListSelection(visibleIds)

  const selectedSessions = useMemo(
    () => sessions.filter(session => selection.selectedIds.has(session.id)),
    [selection.selectedIds, sessions],
  )

  const restore = useMutation({
    mutationFn: restoreSession,
    onSuccess: session => {
      selection.removeIds([session.id])
      updateSessionCaches(client, workspaceId, session)
      void scheduleSessionRefresh(client, workspaceId)
      toast.success('会话已恢复')
    },
    onError: error => toast.error(error.message || '恢复会话失败'),
  })

  const batchRestore = useMutation({
    mutationFn: async (targets: SessionSummary[]) => {
      const results = await Promise.allSettled(targets.map(session => restoreSession(session.id)))
      const restored: string[] = []
      for (const result of results) {
        if (result.status === 'fulfilled') {
          restored.push(result.value.id)
          updateSessionCaches(client, workspaceId, result.value)
        }
      }
      return { restored, failed: results.length - restored.length }
    },
    onSuccess: ({ restored, failed }) => {
      selection.removeIds(restored)
      void scheduleSessionRefresh(client, workspaceId)
      if (failed === 0) toast.success(`已恢复 ${restored.length} 个会话`)
      else if (restored.length > 0) toast.warning(`已恢复 ${restored.length} 个会话，${failed} 个失败`)
      else toast.error('批量恢复失败')
    },
    onError: error => toast.error(error instanceof Error ? error.message : '批量恢复失败'),
  })

  const deleteImpact = useQuery({
    queryKey: workspaceQueryKey(
      workspaceId,
      'archived-session-delete-impact',
      deleteTarget?.kind === 'single'
        ? deleteTarget.session.id
        : deleteTarget?.sessions.map(s => s.id).join(',') ?? '',
    ),
    queryFn: ({ signal }) => {
      if (!deleteTarget) throw new Error('missing target')
      if (deleteTarget.kind === 'single') return getSessionDeleteImpact(deleteTarget.session.id, { signal })
      return getSessionBatchDeleteImpact(deleteTarget.sessions.map(s => s.id), { signal })
    },
    enabled: Boolean(deleteTarget),
  })

  const confirmDelete = useMutation({
    mutationFn: async (confirmationText: string) => {
      if (!deleteTarget) return [] as string[]
      if (deleteTarget.kind === 'single') {
        await deleteSession(deleteTarget.session.id, confirmationText)
        return [deleteTarget.session.id]
      }
      const ids = deleteTarget.sessions.map(s => s.id)
      await deleteSessionsBatch(ids, confirmationText)
      return ids
    },
    onSuccess: deletedIds => {
      selection.removeIds(deletedIds)
      removeSessionsFromCaches(client, workspaceId, deletedIds)
      setDeleteTarget(null)
      void scheduleSessionRefresh(client, workspaceId)
      void archive.refetch()
      toast.success(deletedIds.length > 1 ? `已删除 ${deletedIds.length} 个会话` : '会话已删除')
    },
    onError: error => toast.error(error instanceof Error ? error.message : '删除会话失败'),
  })

  const busy = restore.isPending || batchRestore.isPending || confirmDelete.isPending

  return (
    <PageFrame>
      <PageIntro
        actions={
          sessions.length ? (
            <Button
              aria-pressed={selection.multiSelect}
              onClick={selection.toggleMultiSelect}
              size="sm"
              variant={selection.multiSelect ? 'secondary' : 'outline'}
            >
              <SquareCheck className="size-4" />
              {selection.multiSelect ? '退出多选' : '多选'}
            </Button>
          ) : null
        }
        title="已归档会话"
      />
      {sessions.length ? (
        <div className="flex flex-wrap items-center gap-2">
          <ClearableSearchInput
            aria-label="搜索已归档会话"
            containerClassName="min-w-52 flex-1 sm:max-w-sm"
            onChange={setSearch}
            placeholder="搜索会话标题或所属项目…"
            value={search}
          />
          {selection.multiSelect ? (
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                aria-label="全选当前列表"
                checked={selection.visibleCheckboxState}
                disabled={busy || !filteredSessions.length}
                onCheckedChange={checked => selection.toggleVisible(checked === true)}
              />
              全选当前列表
            </label>
          ) : null}
        </div>
      ) : null}

      {selection.multiSelect && selection.selectedCount > 0 ? (
        <div
          aria-label="已归档会话批量操作"
          className="flex flex-wrap items-center gap-2 rounded-xl border bg-muted/30 px-3 py-2 text-sm"
          role="region"
        >
          <span>
            已选 <strong>{selection.selectedCount}</strong> 个会话
            {selection.otherSelectedCount > 0 ? `（另有 ${selection.otherSelectedCount} 个不在当前筛选）` : ''}
          </span>
          <div className="ml-auto flex flex-wrap items-center gap-1.5">
            <Button
              disabled={busy}
              onClick={() => batchRestore.mutate(selectedSessions)}
              size="xs"
              variant="outline"
            >
              <RotateCcw className="size-3.5" />
              {batchRestore.isPending ? '恢复中…' : '批量恢复'}
            </Button>
            <Button
              disabled={busy}
              onClick={() => setDeleteTarget({ kind: 'batch', sessions: selectedSessions })}
              size="xs"
              variant="destructive"
            >
              <Trash2 className="size-3.5" />
              批量删除
            </Button>
            <Button disabled={busy} onClick={selection.clear} size="xs" variant="ghost">
              取消选择
            </Button>
          </div>
        </div>
      ) : null}

      {archive.isPending ? <p className="text-sm text-muted-foreground" role="status">正在加载已归档会话…</p> : null}
      {archive.isError ? (
        <div className="flex flex-wrap items-center gap-3" role="alert">
          <p className="text-sm">加载已归档会话失败。</p>
          <Button size="sm" variant="outline" disabled={archive.isFetching} onClick={() => {
            if (archive.isFetchNextPageError) void archive.fetchNextPage()
            else void archive.refetch()
          }}>重试</Button>
        </div>
      ) : null}
      {archive.isSuccess && !sessions.length && !archive.hasNextPage ? (
        <EmptyState title="暂无已归档会话" description="归档后的会话会显示在这里。" />
      ) : null}
      {sessions.length > 0 && !filteredSessions.length ? (
        <EmptyState title="没有匹配的已归档会话" description={`未找到与「${search.trim()}」匹配的会话。`} />
      ) : null}
      {filteredSessions.length ? (
        <ul aria-label="已归档会话列表" className="divide-y rounded-xl border">
          {filteredSessions.map(session => {
            const title = session.title || '新会话'
            const projectTitle = session.project_id ? projectTitles.get(session.project_id) : undefined
            const selected = selection.selectedIds.has(session.id)
            return (
              <li
                className={`flex min-w-0 items-center gap-3 px-3 py-2.5 sm:px-4 ${selected ? 'bg-primary/5' : ''}`}
                key={session.id}
              >
                {selection.multiSelect ? (
                  <Checkbox
                    aria-label={`选择会话 ${title}`}
                    disabled={busy}
                    {...selection.checkboxProps(session.id)}
                  />
                ) : null}
                <div className="min-w-0 flex-1">
                  <Link className="block truncate rounded-sm text-sm font-medium hover:underline focus-visible:outline-2 focus-visible:outline-ring"
                    title={title} to={`/w/${workspaceId}/chat/${encodeURIComponent(session.id)}`}>
                    {title}
                  </Link>
                  {projectTitle ? <p className="truncate text-xs text-muted-foreground">{projectTitle}</p> : null}
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <Button aria-label={`恢复会话 ${title}`} disabled={busy} size="sm" variant="outline"
                    onClick={() => restore.mutate(session.id)}>
                    {restore.isPending && restore.variables === session.id ? '恢复中…' : '恢复'}
                  </Button>
                  <Button aria-label={`删除会话 ${title}`} disabled={busy} size="sm" variant="ghost"
                    onClick={() => setDeleteTarget({ kind: 'single', session })}>
                    <Trash2 className="size-3.5 text-destructive" />
                  </Button>
                </div>
              </li>
            )
          })}
        </ul>
      ) : null}
      {archive.hasNextPage && !archive.isError ? (
        <Button className="self-start" size="sm" variant="outline" disabled={archive.isFetching}
          onClick={() => void archive.fetchNextPage()}>
          {archive.isFetchingNextPage ? '加载中…' : '加载更多'}
        </Button>
      ) : null}

      <DeleteImpactDialog
        confirmLabel="永久删除"
        error={
          (deleteImpact.error instanceof Error ? deleteImpact.error.message : undefined) ??
          (confirmDelete.error instanceof Error ? confirmDelete.error.message : undefined)
        }
        impact={deleteImpact.data}
        isConfirming={confirmDelete.isPending}
        isLoading={deleteImpact.isPending && Boolean(deleteTarget)}
        objectLabel={
          deleteTarget?.kind === 'batch'
            ? `${deleteTarget.sessions.length} 个已归档会话`
            : deleteTarget?.session.title || '新会话'
        }
        onConfirm={async () => {
          if (!deleteImpact.data) return
          await confirmDelete.mutateAsync(deleteImpact.data.confirmation_text)
        }}
        onOpenChange={(open) => {
          if (!open && !confirmDelete.isPending) setDeleteTarget(null)
        }}
        open={Boolean(deleteTarget)}
        title={deleteTarget?.kind === 'batch' ? `永久删除选中的 ${deleteTarget.sessions.length} 个已归档会话？` : undefined}
      />
    </PageFrame>
  )
}
