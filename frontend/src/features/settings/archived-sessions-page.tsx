import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { listSessionPage, type SessionPageParam, type SessionScope } from '@/api/session-browser'
import { listProjects, restoreSession } from '@/api/workflow'
import { EmptyState, PageFrame, PageIntro } from '@/components/shared/page-elements'
import { Button } from '@/components/ui/button'
import { toast } from '@/lib/notifications'
import { workspaceQueryKey } from '@/lib/query-keys'
import { sessionBrowserKey, updateSessionCaches } from '@/lib/session-queries'
import { scheduleSessionRefresh } from '@/lib/session-refresh'

const archiveScope: SessionScope = { archived_only: true, roots_only: false, pinned_first: false }

export function ArchivedSessionsPage() {
  const { workspaceId = '' } = useParams()
  return <WorkspaceArchive key={workspaceId} workspaceId={workspaceId} />
}

function WorkspaceArchive({ workspaceId }: { workspaceId: string }) {
  const client = useQueryClient()
  const archive = useInfiniteQuery({
    queryKey: [...sessionBrowserKey(workspaceId), archiveScope],
    queryFn: ({ pageParam, signal }) => listSessionPage(archiveScope, pageParam, { signal }),
    initialPageParam: {} as SessionPageParam,
    getNextPageParam: page => page.has_more && page.next_cursor ? { cursor: page.next_cursor } : undefined,
    retry: false,
  })
  const projects = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'projects'),
    queryFn: () => listProjects(true),
    staleTime: 30_000,
  })
  const restore = useMutation({
    mutationFn: restoreSession,
    onSuccess: session => {
      updateSessionCaches(client, workspaceId, session)
      void scheduleSessionRefresh(client, workspaceId)
      toast.success('会话已恢复')
    },
    onError: error => toast.error(error.message || '恢复会话失败'),
  })
  const items = new Map(archive.data?.pages.flatMap(page => page.items).map(session => [session.id, session]))
  const sessions = [...items.values()].filter(session => session.status === 'archived')
  const projectTitles = new Map(projects.data?.map(project => [project.id, project.title]))

  return (
    <PageFrame>
      <PageIntro title="已归档会话" />
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
      {sessions.length ? (
        <ul aria-label="已归档会话列表" className="divide-y rounded-xl border">
          {sessions.map(session => {
            const title = session.title || '新会话'
            const projectTitle = session.project_id ? projectTitles.get(session.project_id) : undefined
            return (
              <li className="flex min-w-0 items-center gap-3 px-3 py-2.5 sm:px-4" key={session.id}>
                <div className="min-w-0 flex-1">
                  <Link className="block truncate rounded-sm text-sm font-medium hover:underline focus-visible:outline-2 focus-visible:outline-ring"
                    title={title} to={`/w/${workspaceId}/chat/${encodeURIComponent(session.id)}`}>
                    {title}
                  </Link>
                  {projectTitle ? <p className="truncate text-xs text-muted-foreground">{projectTitle}</p> : null}
                </div>
                <Button aria-label={`恢复会话 ${title}`} disabled={restore.isPending} size="sm" variant="outline"
                  onClick={() => restore.mutate(session.id)}>
                  {restore.isPending && restore.variables === session.id ? '恢复中…' : '恢复'}
                </Button>
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
    </PageFrame>
  )
}
