import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { LoaderCircle, Network } from 'lucide-react'
import { Toaster } from 'sonner'

import { WorkspaceShell } from '@/components/layout/workspace-shell'
import { Button } from '@/components/ui/button'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AuthProvider, RequireAuth } from '@/features/auth/auth-context'
import { useAuth } from '@/features/auth/auth-context-value'
import { registerAuthQueryClient } from '@/lib/auth-query-cache'

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
const PracticePage = lazy(() => import('@/features/learning/learning-pages').then((module) => ({ default: module.PracticePage })))
const RoadmapPage = lazy(() => import('@/features/learning/learning-pages').then((module) => ({ default: module.RoadmapPage })))
const ResearchPage = lazy(() => import('@/features/resources/resource-pages').then((module) => ({ default: module.ResearchPage })))
const ResearchNewTaskPage = lazy(() => import('@/features/resources/resource-pages').then((module) => ({ default: module.ResearchNewTaskPage })))
const SearchPage = lazy(() => import('@/features/resources/resource-pages').then((module) => ({ default: module.SearchPage })))
const SourcesPage = lazy(() => import('@/features/resources/resource-pages').then((module) => ({ default: module.SourcesPage })))
const DocumentLearningPage = lazy(() => import('@/features/resources/document-learning-page').then((module) => ({ default: module.DocumentLearningPage })))
const MemoryPage = lazy(() => import('@/features/memory/memory-page').then((module) => ({ default: module.MemoryPage })))
const ProvidersPage = lazy(() => import('@/features/settings/provider-pages').then((module) => ({ default: module.ProvidersPage })))
const UsagePage = lazy(() => import('@/features/settings/usage-pages').then((module) => ({ default: module.UsagePage })))
const ExtensionsPage = lazy(() => import('@/features/settings/extension-pages').then((module) => ({ default: module.ExtensionsPage })))
const ResearchSettingsPage = lazy(() => import('@/features/settings/extension-pages').then((module) => ({ default: module.ResearchSettingsPage })))
const AuditPage = lazy(() => import('@/features/settings/governance-pages').then((module) => ({ default: module.AuditPage })))
const MigrationPage = lazy(() => import('@/features/settings/governance-pages').then((module) => ({ default: module.MigrationPage })))
const WorkspaceSettingsPage = lazy(() => import('@/features/settings/governance-pages').then((module) => ({ default: module.WorkspaceSettingsPage })))
const PersonalizationPage = lazy(() => import('@/features/settings/personalization-page').then((module) => ({ default: module.PersonalizationPage })))
const AccessManagementPage = lazy(() => import('@/features/settings/control-pages').then((module) => ({ default: module.AccessManagementPage })))
const EgressApprovalsPage = lazy(() => import('@/features/settings/egress-approvals-page').then((module) => ({ default: module.EgressApprovalsPage })))
const AboutPage = lazy(() => import('@/features/settings/about-page').then((module) => ({ default: module.AboutPage })))
const ArtifactsPage = lazy(() => import('@/features/artifacts/artifacts-page').then((module) => ({ default: module.ArtifactsPage })))

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
registerAuthQueryClient(queryClient)

function RootRedirect() {
  const auth = useAuth()
  if (auth.mustChangePassword) {
    return <Navigate replace to="/auth/change-password" />
  }
  return <Navigate replace to={auth.authenticated ? `/w/${auth.workspaceId}/home` : '/auth/login'} />
}

function GoalModeRedirect() {
  const { workspaceId = '' } = useParams()
  return <Navigate replace to={`/w/${workspaceId}/chat/new?mode=goal`} />
}

function NotFound() {
  const auth = useAuth()
  const location = useLocation()
  return (
    <main className="grid min-h-svh place-items-center p-6"><div className="max-w-lg text-center"><span className="mx-auto grid size-12 place-items-center rounded-2xl bg-foreground text-background"><Network className="size-5" /></span><h1 className="mt-5 text-2xl font-semibold">页面不存在</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">没有找到 <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">{location.pathname}</code>。它可能属于尚未启用的扩展。</p><Button asChild className="mt-5"><a href={auth.authenticated ? `/w/${auth.workspaceId}/home` : '/auth/login'}>返回 LearnGraph</a></Button></div></main>
  )
}

function RouteLoading() {
  return <main aria-live="polite" className="grid min-h-svh place-items-center bg-background"><div className="flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />正在载入工作区…</div></main>
}

function WorkspaceRouteGuard({ children }: { children: ReactNode }) {
  const { workspaceId: activeWorkspaceId, setWorkspaceId } = useAuth()
  const { workspaceId = '' } = useParams()
  const [denied, setDenied] = useState(false)
  useEffect(() => {
    setDenied(false)
    if (workspaceId && workspaceId !== activeWorkspaceId) {
      void setWorkspaceId(workspaceId).catch(() => setDenied(true))
    }
  }, [activeWorkspaceId, setWorkspaceId, workspaceId])
  if (!workspaceId || denied) {
    return <Navigate replace to={`/w/${activeWorkspaceId}/home`} />
  }
  if (workspaceId !== activeWorkspaceId) return <RouteLoading />
  return children
}

function AppRoutes() {
  return (
    <Suspense fallback={<RouteLoading />}><Routes>
      <Route element={<RootRedirect />} path="/" />
      <Route element={<LoginPage />} path="/auth/login" />
      <Route element={<ChangePasswordPage />} path="/auth/change-password" />
      <Route element={<Navigate replace to="/auth/login" />} path="/login" />
      <Route
        element={<RequireAuth><WorkspaceRouteGuard><WorkspaceShell /></WorkspaceRouteGuard></RequireAuth>}
        path="/w/:workspaceId"
      >
        <Route element={<Navigate replace to="home" />} index />
        <Route element={<DashboardPage />} path="home" />
        <Route element={<GoalModeRedirect />} path="goals/new/clarify" />
        <Route element={<GoalConfirmPage />} path="goals/:goalId/confirm" />
        <Route element={<GraphReviewPage />} path="goals/:goalId/graph-review" />
        <Route element={<RoadmapPage />} path="goals/:goalId/roadmap" />
        <Route element={<GraphWorkspacePage />} path="graphs" />
        <Route element={<GraphWorkspacePage />} path="graphs/:graphId" />
        <Route element={<CapabilityGraphPage />} path="capabilities" />
        <Route element={<JointStudyPage />} path="learn/joint" />
        <Route element={<ChatCanvasPage />} path="chat/:sessionId" />
        <Route element={<VersionsPage />} path="chat/:sessionId/versions" />
        <Route element={<SourcesPage />} path="sources" />
        <Route element={<DocumentLearningPage />} path="documents/:fileId" />
        <Route element={<SearchPage />} path="research/search" />
        <Route element={<ResearchNewTaskPage />} path="research/tasks/new" />
        <Route element={<ResearchPage />} path="research/tasks/:taskId" />
        <Route element={<EvidenceReviewPage />} path="evidence/review" />
        <Route element={<PracticePage />} path="practice" />
        <Route element={<ExerciseAnswerPage />} path="practice/:setId/:questionId" />
        <Route element={<MemoryPage />} path="memory" />
        <Route element={<Navigate replace to="../settings/workspace" />} path="memory/settings" />
        <Route element={<ProvidersPage />} path="settings/providers" />
        <Route element={<UsagePage />} path="settings/usage" />
        <Route element={<ExtensionsPage />} path="settings/extensions" />
        <Route element={<ResearchSettingsPage />} path="settings/research" />
        <Route element={<MigrationPage />} path="settings/storage/migrations" />
        <Route element={<AuditPage />} path="settings/audit" />
        <Route element={<WorkspaceSettingsPage />} path="settings/workspace" />
        <Route element={<PersonalizationPage />} path="settings/personalization" />
        <Route element={<AccessManagementPage />} path="settings/access" />
        <Route element={<EgressApprovalsPage />} path="settings/egress" />
        <Route element={<AboutPage />} path="settings/about" />
        <Route element={<ArtifactsPage />} path="settings/artifacts" />
      </Route>
      <Route element={<NotFound />} path="*" />
    </Routes></Suspense>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={220}>
        <AuthProvider>
          <BrowserRouter><AppRoutes /></BrowserRouter>
          <Toaster closeButton position="top-right" richColors />
        </AuthProvider>
      </TooltipProvider>
    </QueryClientProvider>
  )
}
