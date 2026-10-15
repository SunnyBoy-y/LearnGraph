import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LearningPackageCanvas } from './learning-package-canvas'
import { validateLearningPage } from './learning-response'
import { workspaceQueryKey } from '@/lib/query-keys'
import type { Enrollment, LearningPageData } from './node-learning-api'

const mocks = vi.hoisted(() => ({
  page: vi.fn(), start: vi.fn(), progress: vi.fn(), build: vi.fn(), control: vi.fn(), blob: vi.fn(),
}))
vi.mock('./node-learning-api', () => ({learningApi: mocks}))
vi.mock('./node-learning-page', () => ({ActivityCard: () => <div>实验内容</div>}))
vi.mock('@/api/client', () => ({apiClient: {getBlob: mocks.blob}}))
vi.mock('@/components/ai-elements/incremental-markdown', () => ({IncrementalMarkdown: ({text}: {text: string}) => <p>{text}</p>}))
vi.mock('sonner', () => ({toast: {error: vi.fn()}}))

function page(id: string): LearningPageData {
  return {node: {id, label: `节点${id}`, description: '', graph_id: 'graph', node_type: 'concept', graph_status: 'published'},
    package: {id: `package-${id}`, created_at: '', manifest: {schema_version: 1, blueprint: {title: `教材${id}`, objectives: ['目标'], estimated_minutes: 10},
      lesson: {sections: [{id: `section-${id}`, title: `章节${id}`, body: '内容', takeaway: '小结'}], svg: '<svg />', caption: '', html: ''},
      activity: null, exam: {title: '测评', pass_score: 70, practical_points: 0, questions: []}, notes: [], provenance: '测试'}},
    build: null, stale: false, enrollment: null, achievement: null}
}
const enrollment = (id: string): Enrollment => ({id: `enrollment-${id}`, package_id: `package-${id}`, revision: 1, progress: {sections: [], activity: {values: {}, history: []}}})
const clients: QueryClient[] = []
function client() {
  const c = new QueryClient({defaultOptions: {queries: {retry: false, staleTime: Infinity, gcTime: Infinity}, mutations: {retry: false, gcTime: Infinity}}})
  clients.push(c); return c
}
const key = (id: string) => workspaceQueryKey('workspace', 'learning-page', id)
function view(c: QueryClient, id: string) {
  return <QueryClientProvider client={c}><MemoryRouter><LearningPackageCanvas workspaceId="workspace" sessionId="same-session" learningNode={{graphId: 'graph', nodeId: id}} /></MemoryRouter></QueryClientProvider>
}
function graphPackageView(c: QueryClient, id: string, openKey = 'graph-open-1') {
  return <QueryClientProvider client={c}><MemoryRouter><LearningPackageCanvas workspaceId="workspace" sessionId="same-session" learningNode={{graphId: 'graph', nodeId: id, learningPackage: true, openKey}} /></MemoryRouter></QueryClientProvider>
}
beforeEach(() => {
  mocks.page.mockImplementation(async (id: string) => page(id))
  mocks.start.mockImplementation(async (id: string) => enrollment(id))
  mocks.progress.mockImplementation(async (id: string) => ({...enrollment(id.replace('enrollment-', '')), revision: 2}))
})
afterEach(() => {for (const c of clients.splice(0)) c.clear()})

describe('learning canvas recovery', () => {
  it('folds a graph-open package and replays cached lesson text on expansion', () => {
    vi.useFakeTimers()
    const c = client(); c.setQueryData(key('A'), page('A'))
    const ui = render(graphPackageView(c, 'A'))
    try {
      const expand = screen.getByRole('button', {name: '展开学习包'})
      expect(expand).toHaveAttribute('aria-expanded', 'false')
      expect(screen.getByText('教材A')).toBeInTheDocument()
      expect(screen.queryByText('内容')).toBeNull()
      fireEvent.click(expand)
      expect(screen.getByRole('button', {name: '折叠学习包'})).toHaveAttribute('aria-expanded', 'true')
      expect(screen.queryByText('内容')).toBeNull()
      act(() => vi.advanceTimersByTime(22))
      expect(screen.getByText('内')).toBeInTheDocument()
      act(() => vi.advanceTimersByTime(4_000))
      expect(screen.getByText('内容')).toBeInTheDocument()
      ui.rerender(graphPackageView(c, 'A', 'graph-open-2'))
      const replayExpand = screen.getByRole('button', {name: '展开学习包'})
      expect(replayExpand).toHaveAttribute('aria-expanded', 'false')
      fireEvent.click(replayExpand)
      expect(screen.queryByText('内容')).toBeNull()
    } finally {
      ui.unmount()
      vi.useRealTimers()
    }
  })

  it('does not submit node B progress to cached node A enrollment', async () => {
    const c = client(); c.setQueryData(key('A'), page('A')); c.setQueryData(key('B'), page('B'))
    const ui = render(view(c, 'A'))
    fireEvent.click(screen.getByRole('button', {name: /开始学习/}))
    await waitFor(() => expect(c.getQueryData<LearningPageData>(key('A'))?.enrollment?.id).toBe('enrollment-A'))
    ui.rerender(view(c, 'B'))
    expect(screen.queryByRole('button', {name: '标记本节已读'})).toBeNull()
    fireEvent.click(screen.getByRole('button', {name: /开始学习/}))
    await waitFor(() => expect(screen.getByRole('button', {name: '标记本节已读'})).toBeEnabled())
    fireEvent.click(screen.getByRole('button', {name: '标记本节已读'}))
    await waitFor(() => expect(mocks.progress).toHaveBeenCalledWith('enrollment-B', expect.objectContaining({section_id: 'section-B'})))
  })

  it('keeps a late A start response out of the mounted B canvas', async () => {
    const c = client(); c.setQueryData(key('A'), page('A')); c.setQueryData(key('B'), page('B'))
    let resolve!: (row: Enrollment) => void
    mocks.start.mockImplementationOnce(() => new Promise<Enrollment>(done => {resolve = done}))
    const ui = render(view(c, 'A'))
    fireEvent.click(screen.getByRole('button', {name: /开始学习/}))
    await waitFor(() => expect(mocks.start).toHaveBeenCalled())
    ui.rerender(view(c, 'B'))
    await act(async () => resolve(enrollment('A')))
    expect(screen.getByRole('heading', {name: '教材B'})).toBeInTheDocument()
    expect(screen.queryByRole('button', {name: '标记本节已读'})).toBeNull()
  })

  it('uses the failed-stage retry endpoint rather than starting a new build', async () => {
    const c = client(); const data = page('A'); const manifest = data.package!.manifest
    data.package = null; data.preview = {...manifest, training_ready: true}
    data.build = {id: 'failed-build', node_id: 'A', status: 'failed', stage: '实验', completed_stages: 2, total_stages: 7, notes: []}
    mocks.control.mockResolvedValue({...data.build, status: 'queued'})
    mocks.page.mockResolvedValue(data); c.setQueryData(key('A'), data)
    render(view(c, 'A'))
    fireEvent.click(screen.getByRole('button', {name: '重试失败阶段'}))
    await waitFor(() => expect(mocks.control).toHaveBeenCalledWith('failed-build', 'retry'))
    expect(mocks.build).not.toHaveBeenCalled()
  })

  it.each(['cancelled', 'skipped'])('offers recovery for a partial %s build', status => {
    const c = client(); const data = page('A'); data.preview = {...data.package!.manifest}; data.package = null
    data.build = {id: 'partial', node_id: 'A', status, stage: '教材', completed_stages: 1, total_stages: 7, notes: []}
    c.setQueryData(key('A'), data); render(view(c, 'A'))
    expect(screen.getByRole('button', {name: '重新创建学习包'})).toBeEnabled()
  })

  it('recovers a failed initial read using an actual retry button', async () => {
    const c = client(); mocks.page.mockRejectedValueOnce(new Error('网络失败'))
    render(view(c, 'A'))
    fireEvent.click(await screen.findByRole('button', {name: '重新读取学习包'}))
    expect(await screen.findByRole('heading', {name: '教材A'})).toBeInTheDocument()
  })

  it('keeps cached material visible when a background read fails', async () => {
    const c = client(); c.setQueryData(key('A'), page('A')); mocks.page.mockRejectedValue(new Error('断网'))
    render(view(c, 'A'))
    await act(async () => {await c.invalidateQueries({queryKey: key('A')})})
    expect(await screen.findByText(/更新失败，已保留/)).toBeInTheDocument()
    expect(screen.getByRole('heading', {name: '教材A'})).toBeInTheDocument()
  })

  it('shows an image download error and retries successfully', async () => {
    const c = client(); const data = page('A'); data.package!.manifest.image = {file_id: 'image', alt: '示意插图'}
    c.setQueryData(key('A'), data); mocks.blob.mockRejectedValueOnce(new Error('404')).mockResolvedValue(new Blob(['image']))
    Object.defineProperty(URL, 'createObjectURL', {configurable: true, value: vi.fn(() => 'blob:test')})
    Object.defineProperty(URL, 'revokeObjectURL', {configurable: true, value: vi.fn()})
    render(view(c, 'A'))
    fireEvent.click(await screen.findByRole('button', {name: '重试插图'}))
    expect(await screen.findByRole('img', {name: '示意插图'})).toHaveAttribute('src', 'blob:test')
    expect(mocks.blob).toHaveBeenCalledTimes(2)
  })
})

describe('learning response validation', () => {
  it('accepts a valid package and partial preview without requiring future stages', () => {
    expect(validateLearningPage(page('A')).node.id).toBe('A')
    const data = page('A'); data.package = null
    data.preview = {blueprint: null, lesson: null, activity: null, exam: null, notes: [], provenance: 'partial'}
    expect(validateLearningPage(data).preview?.lesson).toBeNull()
  })
  it('rejects HTML returned with HTTP success before rendering', () => {
    expect(() => validateLearningPage('<html>proxy error</html>')).toThrow(/数据格式不完整/)
  })
  it('rejects malformed arrays instead of crashing inside map', () => {
    const data = page('A') as unknown as {package: {manifest: {blueprint: {objectives: unknown}}}}
    data.package.manifest.blueprint.objectives = null
    expect(() => validateLearningPage(data)).toThrow(/数据格式不完整/)
  })
})
