import { Component, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MotionConfig } from 'motion/react'
import { Toaster, toast } from 'sonner'
import { authStore } from '@/api/auth-store'
import { confirmGraphChangeSet, rejectGraphChangeSet, submitUIAction, undoGraphChangeSet } from '@/api/sessions'
import { AnswerParts } from '@/components/chat/answer-parts'
import { ChatStreamPartRenderer } from '@/components/chat/chat-stream-part-renderer'
import { StreamStatsBadge } from '@/components/chat/stream-stats-badge'
import type { TrustedComponentAction } from '@/components/chat/trusted-component-renderer'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Textarea } from '@/components/ui/textarea'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AuthContext, type AuthContextValue } from '@/features/auth/auth-context-value'
import { registerAuthInvalidationHandler } from '@/lib/auth-query-cache'
import type { MessagePart } from '@/types/sessions'
import { dispatchRendererAction, type GraphReviewResult } from './actions'
import { installRendererDrawer } from './drawer'
import { NativeProcessChain } from './process-chain'
import { NativeSuggestedPrompts } from './suggested-prompts'
import { composeRequest, contentHeight, normalizePayload, RENDERER_OVERLAY_SELECTOR, type NativeRenderPayload, type NativeSendRequest } from './protocol'
import '@xyflow/react/dist/style.css'
import 'katex/dist/katex.min.css'
import 'streamdown/styles.css'
import '@/index.css'
import '@/styles/motion-feedback.css'
import './renderer.css'

const previewParts: MessagePart[] = [
  { id: 'preview-text', type: 'text', status: 'completed', content: '## 原生会话中的共享 Web 组件\n\n这段内容直接使用现有渲染器，支持 **Markdown**、数学 $E=mc^2$、链接与可独立勾选的任务。\n\n- [ ] 阅读材料\n- [x] 检查已有结论\n\n```mermaid\nflowchart LR\n  A[提问] --> B[理解] --> C[练习]\n```' },
  { id: 'preview-choice-1', type: 'component', status: 'completed', data: { component_type: 'single_choice', schema_version: '1', allowed_events: ['submit'], props: { title: '更喜欢哪种学习方式？', options: [{ id: 'example', label: '先看例子' }, { id: 'theory', label: '先学原理' }], allow_custom: false, allow_skip: true } } },
  { id: 'preview-choice-2', type: 'component', status: 'completed', data: { component_type: 'multiple_choice', schema_version: '1', allowed_events: ['submit'], props: { title: '接下来要做什么？', options: [{ id: 'review', label: '复习' }, { id: 'practice', label: '练习' }], allow_custom: false, allow_skip: true } } },
  { id: 'preview-ui', type: 'ui', status: 'completed', data: { protocol: 'suip/0.1', block_id: 'preview-chart', component: 'chart', revision: 1, state: 'completed', props: { title: '学习进度', columns: [{ key: 'count', label: '已完成' }], source_note: '预览示例数据' }, rows: [{ id: 'a', label: '周一', values: { count: 3 } }, { id: 'b', label: '周二', values: { count: 7 } }], nodes: [], edges: [], items: [] } },
]
const previewLong: MessagePart[] = [{ id: 'preview-long', type: 'text', status: 'completed', content: Array.from({ length: 28 }, (_, index) => `### 第 ${index + 1} 个学习要点\n\n这一段用于检查长文换行、段落间距、增量内容和滚动位置。真正的页面仍然使用 WebUI 的同一套组件和样式。\n\n- 记录已经理解的概念\n- 用具体例子检查自己的理解`).join('\n\n') }]
const previewReasoning: MessagePart[] = [{ id: 'preview-reasoning', type: 'reasoning_content', status: 'completed', content: '这是折叠交互的示例内容。\n展开后可以查看完整过程，并保留当前组件的交互状态。' }]

let payload = normalizePayload(window.__lgNativeInitial ?? { messageId: 'preview', sessionId: 'preview-session', workspaceId: 'preview-workspace', parts: previewParts, siblingParts: previewParts, streaming: false, dark: false })
const listeners = new Set<() => void>()
function publish(value: NativeRenderPayload) { payload = value; listeners.forEach(listener => listener()) }
window.__lgRender = (value) => {
  try { publish(normalizePayload(value)) }
  catch (error) { window.LearnGraphRenderer?.failed(error instanceof Error ? error.message : '消息数据无效') }
}
window.addEventListener('error', event => { if (event.message) window.LearnGraphRenderer?.failed(event.message) })
window.addEventListener('unhandledrejection', event => {
  const reason = event.reason instanceof Error ? event.reason.message : String(event.reason ?? '组件执行失败')
  window.LearnGraphRenderer?.failed(reason)
})
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener) } }
const overlaySelector = RENDERER_OVERLAY_SELECTOR
window.__lgDismissOverlay = () => {
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true }))
  requestAnimationFrame(() => {
    const overlay = document.querySelector(overlaySelector)
    if (!overlay) return
    const close = overlay.matches('button[title="Exit fullscreen"],button[aria-label="Exit fullscreen"]') ? overlay as HTMLElement
      : overlay.querySelector<HTMLElement>('[data-slot="dialog-close"], button[data-preview-control][aria-label^="退出"]')
    close?.click()
  })
}
const client = new QueryClient({ defaultOptions: { queries: { retry: 0, staleTime: 30_000, refetchOnWindowFocus: false }, mutations: { retry: 0 } } })
const SharedContextRail = lazy(() => import('@/components/layout/workspace-shell').then(module => ({ default: module.ContextRail })))

function GraphSurface({ workspaceId }: { workspaceId: string }) {
  if (!window.LearnGraphRenderer || !authStore.getAccessToken() || authStore.getWorkspaceId() !== workspaceId) {
    return <div className="native-renderer-graph-empty" role="status"><strong>当前未连接学习图谱</strong><p>登录并打开真实会话后，这里会显示同一个 Web 图谱侧栏。此预览没有请求账户数据。</p></div>
  }
  return <Suspense fallback={<p className="p-4 text-sm text-muted-foreground" role="status">正在加载图谱侧栏…</p>}><SharedContextRail collapsed={false} drawerSwipe="allow" /></Suspense>
}

function LightweightAuth({ workspaceId, children }: { workspaceId: string; children: ReactNode }) {
  const value = useMemo<AuthContextValue>(() => {
    const session = authStore.getSession()
    const accountAction = async (): Promise<never> => {
      window.LearnGraphRenderer?.navigate('/auth/login')
      throw new Error('请在应用的账户页面执行此操作')
    }
    return {
      authenticated: !!session, workspaceId: workspaceId || session?.workspaceId || '', userId: session?.userId || '',
      username: session?.username || '', avatarUrl: session?.avatarUrl || null, workspaceName: '',
      isSystemAdmin: false, effectivePermissions: [], mustChangePassword: session?.mustChangePassword ?? null,
      updateProfile: profile => authStore.setProfile(profile),
      setWorkspaceId: accountAction, login: accountAction, demoLogin: accountAction, register: accountAction,
      changePassword: accountAction, deleteAccount: accountAction,
      logout: async () => { authStore.clear(); window.LearnGraphRenderer?.clearAuth() },
    }
  }, [workspaceId])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

function NavigationBridge({ onNavigate }: { onNavigate(path: string): void }) {
  const location = useLocation()
  const initial = useRef(`${location.pathname}${location.search}${location.hash}`)
  useEffect(() => {
    const path = `${location.pathname}${location.search}${location.hash}`
    if (path !== initial.current) { initial.current = path; onNavigate(path) }
  }, [location, onNavigate])
  return null
}

class RendererBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null }
  static getDerivedStateFromError(error: Error) { return { error: error.message } }
  componentDidCatch(error: Error) { window.LearnGraphRenderer?.failed(error.message) }
  render() { return this.state.error ? <p role="alert">组件加载失败：{this.state.error}</p> : this.props.children }
}

function patchGraph(result: GraphReviewResult) {
  const replace = (parts: MessagePart[]) => parts.map(part => {
    const props = part.data?.props as Record<string, unknown> | undefined
    if (part.type !== 'component' || part.data?.component_type !== 'graph_update_proposal' || props?.proposal_id !== result.id) return part
    return { ...part, data: { ...part.data, allowed_events: result.status === 'confirmed' ? ['undo'] : [], props: { ...props, graph_id: result.graph_id, status: result.status, confirmed_revision: result.confirmed_revision, confirmation_required: false, can_undo: result.status === 'confirmed', rejection_reason: result.rejection_reason } } }
  })
  publish({ ...payload, parts: replace(payload.parts), siblingParts: replace(payload.siblingParts) })
}

function NativeRenderer() {
  const current = useSyncExternalStore(subscribe, () => payload)
  const content = useRef<HTMLDivElement>(null)
  const [lastAction, setLastAction] = useState('尚无交互')
  const [rejectId, setRejectId] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const rejectResolver = useRef<((value: string | null) => void) | null>(null)
  const actionBusy = useRef(false)
  const native = !!window.LearnGraphRenderer
  const record = useCallback((kind: string, detail: unknown) => setLastAction(JSON.stringify({ kind, detail }, null, 2)), [])
  const send = useCallback((request: NativeSendRequest) => {
    if (!request.content.trim() || request.content.length > 50_000) throw new Error('交互消息长度无效')
    if (window.LearnGraphRenderer) window.LearnGraphRenderer.send(JSON.stringify(request))
    else record('send', request)
  }, [record])
  const refresh = useCallback(() => { if (window.LearnGraphRenderer) window.LearnGraphRenderer.refresh(); else record('refresh', {}) }, [record])
  const navigate = useCallback((path: string) => { if (window.LearnGraphRenderer) window.LearnGraphRenderer.navigate(path); else record('navigate', path) }, [record])
  const resolveReject = (value: string | null) => {
    rejectResolver.current?.(value); rejectResolver.current = null; setRejectId(null); setReason('')
  }
  const onAction = useCallback(async (action: TrustedComponentAction) => {
    if (payload.streaming) { toast.error('请等待当前回复完成后操作'); return }
    if (actionBusy.current) return
    actionBusy.current = true
    try {
      await dispatchRendererAction(action, {
        sessionId: () => payload.sessionId, send,
        submitUIAction: async (sessionId, uiAction) => {
          if (window.LearnGraphRenderer) return submitUIAction(sessionId, uiAction)
          record('validate-ui-action', uiAction)
          return { prompt: uiAction.node_id ? `请解释图谱节点 ${uiAction.node_id}` : '请解释这个组件' }
        },
        reviewGraph: async (sessionId, proposalId, decision, rejectionReason) => {
          if (!window.LearnGraphRenderer) {
            record('graph-review', { sessionId, proposalId, decision, reason: rejectionReason })
            return { id: proposalId, status: decision === 'confirm' ? 'confirmed' : decision === 'undo' ? 'undone' : 'rejected', graph_id: null, confirmed_revision: 1, rejection_reason: rejectionReason }
          }
          return decision === 'confirm' ? confirmGraphChangeSet(sessionId, proposalId) : decision === 'undo' ? undoGraphChangeSet(sessionId, proposalId) : rejectGraphChangeSet(sessionId, proposalId, rejectionReason)
        },
        rejectReason: proposalId => new Promise(resolve => { rejectResolver.current = resolve; setReason(''); setRejectId(proposalId) }),
        graphResolved: patchGraph, refresh,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : '交互操作失败'
      toast.error(message)
      if (!window.LearnGraphRenderer) record('error', message)
    } finally { actionBusy.current = false }
  }, [record, refresh, send])

  useEffect(() => {
    document.documentElement.dataset.colorMode = 'mono'
    document.documentElement.classList.add('native-renderer-host')
    if (native) document.documentElement.classList.add('is-native-app')
    document.documentElement.classList.toggle('dark', current.dark)
    document.documentElement.dataset.rendererSurface = current.surface ?? 'message'
  }, [current.dark, current.surface, native])
  useEffect(() => {
    const compose = (event: Event) => {
      const detail = (event as CustomEvent<Record<string, unknown>>).detail
      if (!detail) return
      let learning: Record<string, unknown> | undefined
      try { learning = JSON.parse(window.sessionStorage.getItem('learngraph:active-learning-node') ?? 'null') ?? undefined } catch { /* Optional contextual storage. */ }
      try { const request = composeRequest(detail, payload.sessionId, learning); if (request) send(request) }
      catch (error) { toast.error(error instanceof Error ? error.message : '图谱交互失败') }
    }
    const openLearning = (event: Event) => {
      const detail = (event as CustomEvent<Record<string, unknown>>).detail
      if (!detail || typeof detail.graphId !== 'string') return
      const base = `/w/${encodeURIComponent(payload.workspaceId)}`
      navigate(typeof detail.nodeId === 'string'
        ? `${base}/learn/nodes/${encodeURIComponent(detail.nodeId)}?graphId=${encodeURIComponent(detail.graphId)}`
        : `${base}/graphs/${encodeURIComponent(detail.graphId)}`)
    }
    const clearLearning = () => { try { window.sessionStorage.removeItem('learngraph:active-learning-node') } catch { /* Optional storage. */ } }
    window.addEventListener('learngraph:compose', compose)
    window.addEventListener('learngraph:refresh-messages', refresh)
    window.addEventListener('learngraph:open-learning-project', openLearning)
    window.addEventListener('learngraph:learning-node-clear', clearLearning)
    const unregister = registerAuthInvalidationHandler(() => window.LearnGraphRenderer?.clearAuth())
    window.LearnGraphRenderer?.ready()
    return () => { window.removeEventListener('learngraph:compose', compose); window.removeEventListener('learngraph:refresh-messages', refresh); window.removeEventListener('learngraph:open-learning-project', openLearning); window.removeEventListener('learngraph:learning-node-clear', clearLearning); unregister(); rejectResolver.current?.(null) }
  }, [refresh, send, navigate])
  useEffect(() => {
    const bridge = window.LearnGraphRenderer
    if (bridge) return installRendererDrawer(bridge, () => !!document.querySelector(overlaySelector), () => payload.surface === 'graph' ? 'graph' : 'message')
  }, [])
  useEffect(() => {
    const element = content.current
    if (!element || !native) return
    let frame = 0, previous = 0, overlayOpen = false
    const measure = () => {
      frame = 0
      const nextOverlay = !!document.querySelector(overlaySelector)
      if (nextOverlay !== overlayOpen) { overlayOpen = nextOverlay; window.LearnGraphRenderer?.overlay?.(nextOverlay) }
      if (overlayOpen) return
      if (payload.surface === 'graph') return
      const height = contentHeight(element.getBoundingClientRect().height, element.scrollHeight)
      if (height !== previous) { previous = height; window.LearnGraphRenderer?.resize(height) }
    }
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure) }
    const observer = new ResizeObserver(schedule)
    observer.observe(element)
    const mutations = new MutationObserver(schedule)
    mutations.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-state', 'open', 'class'] })
    window.addEventListener('resize', schedule)
    schedule()
    return () => { observer.disconnect(); mutations.disconnect(); cancelAnimationFrame(frame); window.removeEventListener('resize', schedule); if (overlayOpen) window.LearnGraphRenderer?.overlay?.(false) }
  }, [native, current.surface])

  const initialPath = `/w/${encodeURIComponent(current.workspaceId)}/chat/${encodeURIComponent(current.sessionId)}`
  return <QueryClientProvider client={client}><TooltipProvider delayDuration={220}><MotionConfig reducedMotion="user">
    <LightweightAuth workspaceId={current.workspaceId}><MemoryRouter key={`${current.workspaceId}:${current.sessionId}`} initialEntries={[initialPath]}>
      <NavigationBridge onNavigate={navigate} />
      {!native && <header className="native-renderer-preview-controls"><strong>共享 Web 消息组件预览</strong><div className="flex flex-wrap gap-2">
        {([['全部', previewParts], ['数学 / Mermaid', previewParts.slice(0, 1)], ['图表', previewParts.slice(-1)], ['选项', previewParts.slice(1, 3)], ['长文', previewLong]] as const).map(([label, parts]) => <Button key={label} size="sm" variant="outline" onClick={() => publish({ ...payload, surface: 'message', thinkingChain: undefined, parts: [...parts], siblingParts: [...parts] })}>{label}</Button>)}
        <Button size="sm" variant="outline" onClick={() => publish({ ...payload, surface: 'message', parts: previewReasoning, siblingParts: previewReasoning, providerTrace: { thinking_duration_ms: 12_700 }, thinkingChain: { messageStatus: 'completed', startedAt: '2026-10-18T12:00:00Z', finalAnswerStarted: true, defaultOpen: true } })}>思考过程</Button>
        <Button size="sm" variant="outline" onClick={() => publish({ ...payload, surface: 'graph' })}>图谱侧栏</Button>
        <Button size="sm" variant="outline" onClick={() => publish({ ...payload, surface: 'suggestions' })}>追问建议</Button>
        <Button size="sm" variant="outline" onClick={() => publish({ ...payload, dark: !payload.dark })}>切换主题</Button>
      </div></header>}
      <div ref={content} className={current.surface === 'graph' ? 'native-renderer-graph workspace-main--chat' : 'native-renderer-message workspace-main--chat'}>
        {current.surface === 'graph' ? <RendererBoundary><Routes><Route path="/w/:workspaceId/chat/:sessionId" element={<GraphSurface workspaceId={current.workspaceId} />} /></Routes></RendererBoundary>
          : current.surface === 'suggestions' ? <section className="chat-canvas-page"><RendererBoundary><NativeSuggestedPrompts
            workspaceId={current.workspaceId} sessionId={current.sessionId} anchor={current.suggestions} native={native}
            onSelect={content => send({ content, graph_action: 'none', generation_mode: 'text' })}
            onConfigureProvider={() => navigate(`/w/${encodeURIComponent(current.workspaceId)}/settings/providers`)}
          /></RendererBoundary></section> : <section className="chat-canvas-page"><div className="is-assistant"><div data-message-content><div className="message-answer-segment">
          <RendererBoundary>{current.thinkingChain ? <NativeProcessChain parts={current.parts} siblingParts={current.siblingParts}
            providerTrace={current.providerTrace} state={current.thinkingChain}
            renderPart={part => <ChatStreamPartRenderer key={part.id} part={part} siblingParts={current.siblingParts} interactive={!current.streaming} onAction={onAction} streaming={current.streaming && (part.status === 'streaming' || part.status === 'pending')} />} />
            : <AnswerParts parts={current.parts} siblingParts={current.siblingParts} interactive={!current.streaming} onAction={onAction}
              renderPart={part => <ChatStreamPartRenderer key={part.id} part={part} siblingParts={current.siblingParts} interactive={!current.streaming} onAction={onAction} streaming={current.streaming && (part.status === 'streaming' || part.status === 'pending')} />} />}</RendererBoundary>
        </div></div>
          {current.showStreamIndicator && <StreamStatsBadge messageId={current.messageId} status={current.streaming ? 'streaming' : 'completed'} providerTrace={current.providerTrace ?? {}} />}
        </div></section>}
      </div>
      {!native && <aside className="native-renderer-preview-action"><h2>最近交互</h2><pre>{lastAction}</pre></aside>}
      <Dialog open={!!rejectId} onOpenChange={open => { if (!open) resolveReject(null) }}><DialogContent>
        <DialogHeader><DialogTitle>拒绝这份图谱提案？</DialogTitle><DialogDescription>拒绝只会结束本次待审提案，不会修改正式图谱。理由可选，便于后续对话继续调整。</DialogDescription></DialogHeader>
        <Textarea aria-label="拒绝图谱提案的理由" value={reason} onChange={event => setReason(event.currentTarget.value)} placeholder="例如：节点粒度太粗，请拆分后重新生成（可不填）" />
        <DialogFooter><Button variant="outline" onClick={() => resolveReject(null)}>取消</Button><Button variant="destructive" onClick={() => resolveReject(reason.trim())}>确认拒绝</Button></DialogFooter>
      </DialogContent></Dialog>
      <Toaster closeButton position="top-right" richColors />
    </MemoryRouter></LightweightAuth>
  </MotionConfig></TooltipProvider></QueryClientProvider>
}

createRoot(document.getElementById('root')!).render(<NativeRenderer />)
