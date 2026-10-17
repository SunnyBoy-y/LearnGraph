import { legacySettingsRoutes } from '@/components/shared/settings-nav-data'
import { lazy, Suspense } from 'react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { Network } from 'lucide-react'
import { Toaster } from 'sonner'

import { WorkspaceShell } from '@/components/layout/workspace-shell'
import { RouteErrorBoundary } from '@/components/shared/route-error-boundary'
import { Button } from '@/components/ui/button'
import { TooltipProvider } from '@/components/ui/tooltip'
import { listGraphs } from '@/api'
import { workspaceQueryKey } from '@/lib/query-keys'
import { AuthProvider, RequireAuth } from '@/features/auth/auth-context'
import { RouteLoading, WorkspaceRouteGuard } from '@/features/auth/workspace-route-guard'
import { useAuth } from '@/features/auth/auth-context-value'
import { registerAuthQueryClient } from '@/lib/auth-query-cache'
import { hydratePersistedQueryCache, startPersistedQueryCache } from '@/lib/query-persistence'
import { VoiceSessionProvider } from '@/features/voice/voice-session-controller'

const LoginPage = lazy(() => import('@/features/auth/login-page').then((module) => ({ default: module.LoginPage })))
const ChangePasswordPage = lazy(() => import('@/features/auth/change-password-page').then((module) => ({ default: module.ChangePasswordPage })))
const DashboardPage = lazy(() => import('@/features/dashboard/dashboard-page').then((module) => ({ default: module.DashboardPage })))
const GoalConfirmPage = lazy(() => import('@/features/goals/goal-pages').then((module) => ({ default: module.GoalConfirmPage })))
const GraphReviewPage = lazy(() => import('@/features/goals/goal-pages').then((module) => ({ default: module.GraphReviewPage })))
const CapabilityGraphPage = lazy(() => import('@/features/graph/graph-pages').then((module) => ({ default: module.CapabilityGraphPage })))
const GraphWorkspacePage = lazy(() => import('@/features/graph/graph-pages').then((module) => ({ default: module.GraphWorkspacePage })))
const JointStudyPage = lazy(() => import('@/features/graph/graph-pages').then((module) => ({ default: module.JointStudyPage })))
const ChatCanvasPage = lazy(() => import('@/features/chat/chat-pages').then((module) => ({ default: module.ChatCanvasPage })))
const VersionsPage = lazy(() => import('@/features/chat/chat-pages').then((module) => ({ default: module.VersionsPage })))
const EvidenceReviewPage = lazy(() => import('@/features/learning/learning-pages').then((module) => ({ default: module.EvidenceReviewPage })))
const ExerciseAnswerPage = lazy(() => import('@/features/learning/learning-pages').then((module) => ({ default: module.ExerciseAnswerPage })))
const NodeLearningPage = lazy(() => import('@/features/learning/node-learning-page').then((module) => ({ default: module.NodeLearningPage })))
const PracticeCenterPage = lazy(() => import('@/features/practice/practice-center-page').then((module) => ({ default: module.PracticeCenterPage })))
const PracticeSessionPage = lazy(() => import('@/features/practice/practice-session-page').then((module) => ({ default: module.PracticeSessionPage })))
const PracticeReportPage = lazy(() => import('@/features/practice/practice-report-page').then((module) => ({ default: module.PracticeReportPage })))
const RoadmapPage = lazy(() => import('@/features/learning/learning-pages').then((module) => ({ default: module.RoadmapPage })))
const SourcesPage = lazy(() => import('@/features/resources/resource-pages').then((module) => ({ default: module.SourcesPage })))
const DocumentLearningPage = lazy(() => import('@/features/resources/document-learning-page').then((module) => ({ default: module.DocumentLearningPage })))
const MemoryPage = lazy(() => import('@/features/memory/memory-page').then((module) => ({ default: module.MemoryPage })))
const PersonalizationPage = lazy(() => import('@/features/settings/personalization-page').then((module) => ({ default: module.PersonalizationPage })))
const ModelSettingsPage = lazy(() => import('@/features/settings/settings-center-pages').then(m => ({ default: m.ModelSettingsPage })))
const VoiceSettingsPage = lazy(() => import('@/features/settings/settings-center-pages').then(m => ({ default: m.VoiceSettingsPage })))
const ToolsSettingsPage = lazy(() => import('@/features/settings/settings-center-pages').then(m => ({ default: m.ToolsSettingsPage })))
const WorkspaceSettingsPage = lazy(() => import('@/features/settings/settings-center-pages').then(m => ({ default: m.WorkspaceSettingsPage })))
const DataSettingsPage = lazy(() => import('@/features/settings/settings-center-pages').then(m => ({ default: m.DataSettingsPage })))
const MemorySettingsPage = lazy(() => import('@/features/settings/memory-settings-page').then(m => ({ default: m.MemorySettingsPage })))
const ConversationSettingsPage = lazy(() => import('@/features/settings/conversation-settings-page').then(m => ({ default: m.ConversationSettingsPage })))
const AccountSettingsPage = lazy(() => import('@/features/settings/account-settings-page').then(m => ({ default: m.AccountSettingsPage })))
const AboutPage = lazy(() => import('@/features/settings/settings-center-pages').then((module) => ({ default: module.AboutSettingsPage })))
const ArtifactsPage = lazy(() => import('@/features/artifacts/artifacts-page').then((module) => ({ default: module.ArtifactsPage })))
const SessionShareViewPage = lazy(() => import('@/features/chat/session-share-view').then((module) => ({ default: module.SessionShareViewPage })))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      // U1-2: remounted page queries (dashboard/sessions/settings/workspaces)
      // stay fresh for 30s so page switches reuse the cache; gcTime stays 30s
      // so the RAM tradeoff for session message history is unchanged. Queries
      // with explicit refetchInterval / refetchOnMount:"always" are unaffected.
      staleTime: 30_000,
      // Drop inactive query data quickly. Per-session message history and
      // derived message caches are the main multi-session RAM cost; chat also
      // actively evicts non-active/non-streaming session caches on switch.
      gcTime: 30_000,
      refetchOnWindowFocus: false,
    },
    mutations: { retry: 0 },
  },
})
hydratePersistedQueryCache(queryClient)
startPersistedQueryCache(queryClient)
registerAuthQueryClient(queryClient)

function RootRedirect() {
  const auth = useAuth()
  if (auth.mustChangePassword) {
    return <Navigate replace to="/auth/change-password" />
  }
  // New visitors should land in a blank conversation; the LearnGraph mark in
  // the sidebar remains the explicit link back to the workspace dashboard.
  return <Navigate replace to={auth.authenticated ? `/w/${auth.workspaceId}/chat/new` : '/auth/login'} />
}

function GoalModeRedirect() {
  const { workspaceId = '' } = useParams()
  const location = useLocation()
  const search = new URLSearchParams(location.search)
  search.set('mode', 'goal')
  return <Navigate replace to={`/w/${workspaceId}/chat/new?${search.toString()}`} />
}

/** 联网搜索与网页获取页（SearchPage）对全部用户下线：入口与页面同时撤下，
 *  老书签或手输的 /w/:id/research/search 统一回工作台首页。
 *  只动前端 UI，聊天内的联网搜索与网页抓取能力不受影响。 */
function SearchFetchRemovedRedirect() {
  const { workspaceId = '' } = useParams()
  return <Navigate replace to={`/w/${workspaceId}/home`} />
}

function NotFound() {
  const auth = useAuth()
  const location = useLocation()
  return (
    <main className="grid min-h-svh place-items-center p-6"><div className="max-w-lg text-center"><span className="mx-auto grid size-12 place-items-center rounded-2xl bg-foreground text-background"><Network className="size-5" /></span><h1 className="mt-5 text-2xl font-semibold">页面不存在</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">没有找到 <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">{location.pathname}</code>。它可能属于尚未启用的扩展。</p><Button asChild className="mt-5"><a href={auth.authenticated ? `/w/${auth.workspaceId}/chat/new` : '/auth/login'}>返回 LearnGraph</a></Button></div></main>
  )
}

/**
 * 旧「路线」一级页已收敛为图谱里的「学习计划」面板。
 *
 * 老链接（书签 / 历史会话）先尝试把 goalId 解析成本工作区里的对应图谱，再跳到
 * `/w/:id/graphs/:graphId?panel=plan`。如果数据不足以确定图谱（例如目标还没有图
 * 谱），继续渲染原路线页——只做入口收敛，绝不制造 404。
 */
function LegacyRoadmapRoute() {
  const { workspaceId = '', goalId = '' } = useParams()
  const graphs = useQuery({
    queryKey: workspaceQueryKey(workspaceId, 'graphs'),
    queryFn: listGraphs,
    enabled: Boolean(workspaceId),
    staleTime: 30_000,
  })
  if (graphs.isPending) return <RouteLoading />
  const graph = graphs.data?.find((item) => item.goal_id === goalId)
  if (graph) {
    return <Navigate replace to={`/w/${workspaceId}/graphs/${graph.id}?panel=plan`} />
  }
  return <RoadmapPage />
}

function LegacySettingsRedirect({ target }: { target: string }) {
  const { workspaceId = '' } = useParams()
  const location = useLocation()
  const [path, query = ''] = target.split('?')
  const search = new URLSearchParams(location.search)
  if (path === 'tools' && search.has('tab')) { search.set('extensionTab', search.get('tab')!); search.delete('tab') }
  for (const [key, value] of new URLSearchParams(query)) search.set(key, value)
  return <Navigate replace to={`/w/${workspaceId}/settings/${path}?${search}${location.hash}`} />
}

function AppRoutes() {
  return (
    <RouteErrorBoundary><Suspense fallback={<RouteLoading />}><Routes>
      <Route element={<RootRedirect />} path="/" />
      <Route element={<LoginPage />} path="/auth/login" />
      <Route element={<ChangePasswordPage />} path="/auth/change-password" />
      <Route element={<Navigate replace to="/auth/login" />} path="/login" />
      <Route
        element={<RequireAuth><WorkspaceRouteGuard><WorkspaceShell /></WorkspaceRouteGuard></RequireAuth>}
        path="/w/:workspaceId"
      >
        <Route element={<Navigate replace to="chat/new" />} index />
        <Route element={<DashboardPage />} path="home" />
        <Route element={<GoalModeRedirect />} path="goals/new/clarify" />
        <Route element={<GoalConfirmPage />} path="goals/:goalId/confirm" />
        <Route element={<GraphReviewPage />} path="goals/:goalId/graph-review" />
        <Route element={<LegacyRoadmapRoute />} path="goals/:goalId/roadmap" />
        <Route element={<GraphWorkspacePage />} path="graphs" />
        <Route element={<GraphWorkspacePage />} path="graphs/:graphId" />
        <Route element={<CapabilityGraphPage />} path="capabilities" />
        <Route element={<JointStudyPage />} path="learn/joint" />
        <Route element={<NodeLearningPage />} path="learn/nodes/:nodeId" />
        <Route element={<ChatCanvasPage />} path="chat/:sessionId" />
        <Route element={<VersionsPage />} path="chat/:sessionId/versions" />
        <Route element={<SourcesPage />} path="sources" />
        <Route element={<DocumentLearningPage />} path="documents/:fileId" />
        <Route
          // 联网搜索与网页获取（Search & fetch）页面已下线：入口与页面同时撤下。
          element={<SearchFetchRemovedRedirect />}
          path="research/search"
        />
        <Route element={<EvidenceReviewPage />} path="evidence/review" />
        <Route element={<PracticeCenterPage />} path="practice" />
        <Route element={<PracticeSessionPage />} path="practice/session/:practiceSessionId" />
        <Route element={<PracticeReportPage />} path="practice/report/:practiceSessionId" />
        <Route element={<ExerciseAnswerPage />} path="practice/:setId/:questionId" />
        <Route element={<MemoryPage />} path="memory" />
        <Route element={<Navigate replace to="../settings/memory" />} path="memory/settings" />
        <Route element={<Navigate replace to="general" />} path="settings" />
        <Route element={<PersonalizationPage />} path="settings/general" />
        <Route element={<ConversationSettingsPage />} path="settings/conversation" />
        <Route element={<MemorySettingsPage />} path="settings/memory" />
        <Route element={<VoiceSettingsPage />} path="settings/voice" />
        <Route element={<ModelSettingsPage />} path="settings/models" />
        <Route element={<ToolsSettingsPage />} path="settings/tools" />
        <Route element={<WorkspaceSettingsPage />} path="settings/workspace" />
        <Route element={<AccountSettingsPage />} path="settings/account" />
        <Route element={<DataSettingsPage />} path="settings/data" />
        {Object.entries(legacySettingsRoutes).map(([oldPath, target]) => <Route key={oldPath} path={`settings/${oldPath}`} element={<LegacySettingsRedirect target={target}/>} />)}
        <Route element={<AboutPage />} path="settings/about" />
        <Route element={<ArtifactsPage />} path="artifacts" />
        <Route element={<Navigate replace to="../artifacts" />} path="settings/artifacts" />
      </Route>
      <Route element={<NotFound />} path="*" />
      <Route element={<SessionShareViewPage />} path="/share/:token" />
    </Routes></Suspense></RouteErrorBoundary>
  )
}

const router = createBrowserRouter([{ path: '*', element: <AppRoutes /> }])

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={220}>
        <AuthProvider>
          <VoiceSessionProvider>
            <RouterProvider router={router} />
          </VoiceSessionProvider>
          <Toaster closeButton position="top-right" richColors />
        </AuthProvider>
      </TooltipProvider>
    </QueryClientProvider>
  )
}
