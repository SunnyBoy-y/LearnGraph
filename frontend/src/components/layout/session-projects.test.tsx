import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode, useState, type ComponentProps, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/tooltip'
import type { SidebarFolderState } from '@/lib/sidebar-folders'
import { markSessionGenerationFinished, markSessionRunning, markSessionViewed, seedSessionActivity } from '@/lib/session-activity'
import { SessionProjects } from './session-projects'
import type { SessionNavigator } from './use-session-navigator'

vi.mock('./virtual-session-list', () => ({
  VirtualSessionList: ({ rows, renderRow }: { rows: { key: string }[]; renderRow: (row: { key: string }) => ReactNode }) => <div>{rows.map(row => <div key={row.key}>{renderRow(row)}</div>)}</div>,
  SessionPageSentinel: () => null,
}))

type SessionProjectsProps = ComponentProps<typeof SessionProjects>

function fixture(initial: SidebarFolderState = {}, overrides: Partial<SessionProjectsProps> = {}) {
  const retainScopes = vi.fn()
  const selectProject = vi.fn()
  const createProject = vi.fn().mockResolvedValue('new-project')
  const load = vi.fn()
  const props: SessionProjectsProps = {
    navigator: { data: [], scopes: new Map(), retainScopes, load } as unknown as SessionNavigator,
    workspaceId: 'sidebar-test', activeSessionId: 's1', creatingConversation: false, folderState: initial,
    onToggleFolder: vi.fn(), onToggleFolders: vi.fn(), onCreateConversation: vi.fn().mockResolvedValue(undefined), onCreateProject: createProject,
    onRequestProjectDeletion: vi.fn(), onRenameProject: vi.fn(), onToggleProjectArchive: vi.fn(), onMoveSession: vi.fn(),
    onRequestSessionBatchDeletion: vi.fn(), onRequestMixedBatchDeletion: vi.fn(), onRequestSessionDeletion: vi.fn(),
    onRenameSession: vi.fn().mockResolvedValue(true), onToggleSessionPin: vi.fn(), onToggleSessionArchive: vi.fn(), onShareSession: vi.fn(),
    onSelectProject: selectProject, onSelectSession: vi.fn(),
    projects: [{ id: 'p1', title: '计算机网络', status: 'active', sessions: [{ id: 's1', title: 'TCP 三次握手', pinned: false, status: 'active', project_id: 'p1' }] }],
    ungroupedSessions: [{ id: 'r1', title: '最近阅读', pinned: false, status: 'active' }],
    ...overrides,
  }
  function Fixture() {
    const [folders, setFolders] = useState(initial)
    return <TooltipProvider><SessionProjects {...props} folderState={folders} onToggleFolder={(kind, id, open) => setFolders(previous => ({ ...previous, [`${kind}:${id}`]: open }))} /></TooltipProvider>
  }
  const view = render(<StrictMode><Fixture /></StrictMode>)
  return {
    retainScopes, selectProject, createProject, load, props,
    update: (patch: Partial<SessionProjectsProps>) => {
      Object.assign(props, patch)
      view.rerender(<StrictMode><Fixture /></StrictMode>)
    },
  }
}

async function openMenu(trigger: string) {
  fireEvent.keyDown(screen.getByRole('button', { name: trigger }), { key: 'Enter' })
  return screen.findByRole('menu')
}

async function enterBatchMode(trigger = '管理项目分组') {
  await openMenu(trigger)
  fireEvent.click(screen.getByRole('menuitem', { name: '批量管理' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '退出批量管理' })).toBeVisible())
}

describe('sidebar generation status', () => {
  it.each(['s1', 'r1'])('replaces the trailing spinner with a completion dot for %s and clears it when viewed', sessionId => {
    seedSessionActivity(new Map())
    fixture({ 'project:p1': true })
    const title = screen.getByText(sessionId === 's1' ? 'TCP 三次握手' : '最近阅读')
    const row = title.closest('button')!
    act(() => markSessionRunning(sessionId, true))
    expect(row.querySelector('.sidebar-session__activity')).toBe(row.lastElementChild)
    expect(row.querySelector('[aria-label="生成中"]')).toBeInTheDocument()
    expect(row.querySelector('.sidebar-session__unread-dot')).toBeNull()
    act(() => markSessionGenerationFinished(sessionId, { viewing: false }))
    expect(row.querySelector('[aria-label="生成中"]')).toBeNull()
    expect(row.querySelector('[aria-label="模型回复已完成"]')).toBeInTheDocument()
    expect(row.querySelector('.sidebar-session__activity')).toBe(row.lastElementChild)
    act(() => markSessionRunning(sessionId, true))
    expect(row.querySelector('.sidebar-session__unread-dot')).toBeNull()
    act(() => markSessionGenerationFinished(sessionId, { viewing: true }))
    expect(row.querySelector('.sidebar-session__activity')).toBeNull()
    act(() => markSessionGenerationFinished(sessionId, { viewing: false }))
    act(() => markSessionViewed(sessionId))
    expect(row.querySelector('.sidebar-session__activity')).toBeNull()
    seedSessionActivity(new Map())
  })
})

describe('sidebar sections', () => {
  it('renders project and recent headings without the redundant heading, search, or triangles', () => {
    fixture()
    expect(screen.getByRole('button', { name: '折叠项目分组' })).toHaveTextContent('项目')
    expect(screen.getByRole('button', { name: '折叠最近分组' })).toHaveTextContent('最近')
    expect(screen.queryByText('会话', { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '新建对话' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /搜索/ })).not.toBeInTheDocument()
    expect(document.querySelector('.lucide-chevron-down, .lucide-chevron-right')).toBeNull()
  })
  it('creates an ungrouped conversation from the recent header and reveals the group', async () => {
    const { props, selectProject } = fixture({ 'section:recent': false }, { selectedProjectId: 'p1' })
    fireEvent.click(screen.getByRole('button', { name: '新建最近对话' }))
    expect(props.onCreateConversation).toHaveBeenCalledExactlyOnceWith(null)
    expect(selectProject).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '折叠最近分组' })).toHaveAttribute('aria-expanded', 'true')
  })
  it('disables the recent create action while a conversation is being created', () => {
    fixture({}, { creatingConversation: true })
    expect(screen.getByRole('button', { name: '新建最近对话' })).toBeDisabled()
  })
  it('collapses each group independently and preserves the inner folder preference', () => {
    const { retainScopes } = fixture({ 'project:p1': true })
    expect(screen.getByText('TCP 三次握手')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '折叠项目分组' }))
    expect(screen.queryByText('TCP 三次握手')).not.toBeInTheDocument()
    expect(screen.getByText('最近阅读')).toBeInTheDocument()
    expect(retainScopes).toHaveBeenLastCalledWith(new Set(['recent']))
    fireEvent.click(screen.getByRole('button', { name: '折叠最近分组' }))
    expect(screen.queryByText('最近阅读')).not.toBeInTheDocument()
    expect(retainScopes).toHaveBeenLastCalledWith(new Set())
    fireEvent.click(screen.getByRole('button', { name: '展开项目分组' }))
    expect(screen.getByText('TCP 三次握手')).toBeInTheDocument()
    expect(screen.queryByText('最近阅读')).not.toBeInTheDocument()
    expect(retainScopes).toHaveBeenLastCalledWith(new Set(['project:p1']))
  })
  it('keeps project selection and folder disclosure as separate actions', () => {
    const { selectProject } = fixture({ 'project:p1': true })
    fireEvent.click(screen.getByRole('button', { name: '选择项目 计算机网络' }))
    expect(selectProject).toHaveBeenCalledWith('p1')
    expect(screen.getByRole('button', { name: '折叠文件夹 计算机网络' })).toHaveAttribute('aria-expanded', 'true')
    selectProject.mockClear()
    fireEvent.click(screen.getByRole('button', { name: '折叠文件夹 计算机网络' }))
    expect(selectProject).not.toHaveBeenCalled()
    expect(screen.queryByText('TCP 三次握手')).not.toBeInTheDocument()
  })
  it('respects persisted collapsed groups even when they contain the active session', () => {
    fixture({ 'section:projects': false, 'section:recent': false, 'project:p1': true })
    expect(screen.getByRole('button', { name: '展开项目分组' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('计算机网络')).not.toBeInTheDocument()
    expect(screen.queryByText('TCP 三次握手')).not.toBeInTheDocument()
    expect(screen.queryByText('最近阅读')).not.toBeInTheDocument()
  })
  it('reveals a collapsed project group before displaying the create form', () => {
    fixture({ 'section:projects': false })
    fireEvent.click(screen.getByRole('button', { name: '新建项目' }))
    expect(screen.getByRole('button', { name: '折叠项目分组' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('textbox', { name: '项目名称' })).toHaveFocus()
    expect(screen.getByRole('button', { name: '确认新建项目' })).toBeDisabled()
    fireEvent.keyDown(screen.getByRole('textbox', { name: '项目名称' }), { key: 'Escape' })
    expect(screen.queryByRole('textbox', { name: '项目名称' })).not.toBeInTheDocument()
  })
})

describe('sidebar batch management', () => {
  it('offers batch management alongside existing project actions and preselects that project', async () => {
    const { props, selectProject } = fixture()
    await openMenu('管理项目 计算机网络')
    for (const name of ['改名', '归档项目', '永久删除', '批量管理']) {
      expect(screen.getByRole('menuitem', { name })).toBeInTheDocument()
    }
    fireEvent.click(screen.getByRole('menuitem', { name: '批量管理' }))
    await waitFor(() => expect(screen.getByRole('checkbox', { name: '选择项目 计算机网络' })).toBeChecked())
    expect(screen.getByRole('checkbox', { name: '选择会话 TCP 三次握手' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: '选择会话 最近阅读' })).not.toBeChecked()
    expect(selectProject).not.toHaveBeenCalled()
    expect(props.onRequestProjectDeletion).not.toHaveBeenCalled()
    expect(props.onRequestMixedBatchDeletion).not.toHaveBeenCalled()
  })

  it('also exposes batch management for an archived project', async () => {
    const project = { id: 'archived', title: '旧项目', status: 'archived', sessions: [] }
    const { props } = fixture({}, { projects: [project] })
    await enterBatchMode('管理项目 旧项目')
    expect(screen.getByRole('checkbox', { name: '选择项目 旧项目' })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(props.onRequestMixedBatchDeletion).toHaveBeenCalledExactlyOnceWith([project], [])
  })

  it.each(['管理项目分组', '管理最近会话'])('enters with no selections from %s', async trigger => {
    fixture()
    await enterBatchMode(trigger)
    expect(screen.getAllByRole('checkbox')).toHaveLength(3)
    expect(screen.getAllByRole('checkbox').every(checkbox => checkbox.getAttribute('aria-checked') === 'false')).toBe(true)
    expect(screen.getByRole('button', { name: '批量删除' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '全选已加载' })).toBeInTheDocument()
  })

  it('selects through checkboxes or row titles without navigation and unchecks a project with its child', async () => {
    const { props, selectProject } = fixture()
    await enterBatchMode()
    fireEvent.click(screen.getByRole('button', { name: '选择项目 计算机网络' }))
    expect(screen.getByRole('checkbox', { name: '选择项目 计算机网络' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: '选择会话 TCP 三次握手' })).toBeChecked()
    fireEvent.click(screen.getByRole('checkbox', { name: '选择会话 TCP 三次握手' }))
    expect(screen.getByRole('checkbox', { name: '选择项目 计算机网络' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: '批量删除' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '最近阅读', exact: true }))
    expect(screen.getByRole('checkbox', { name: '选择会话 最近阅读' })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(props.onRequestSessionBatchDeletion).toHaveBeenCalledExactlyOnceWith(props.ungroupedSessions)
    expect(props.onRequestMixedBatchDeletion).not.toHaveBeenCalled()
    expect(props.onSelectSession).not.toHaveBeenCalled()
    expect(selectProject).not.toHaveBeenCalled()
  })

  it('selects all loaded projects and sessions and delegates deduplicated mixed deletion to its parent', async () => {
    const { props, load } = fixture()
    await enterBatchMode()
    fireEvent.click(screen.getByRole('button', { name: '全选已加载' }))
    expect(screen.getAllByRole('checkbox').every(checkbox => checkbox.getAttribute('aria-checked') === 'true')).toBe(true)
    expect(screen.getByRole('button', { name: '取消全选' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(props.onRequestMixedBatchDeletion).toHaveBeenCalledExactlyOnceWith(props.projects, props.ungroupedSessions)
    expect(props.onRequestSessionBatchDeletion).not.toHaveBeenCalled()
    expect(props.onRequestProjectDeletion).not.toHaveBeenCalled()
    expect(props.onRequestSessionDeletion).not.toHaveBeenCalled()
    // Confirmation/cancellation belongs to the parent; requesting deletion keeps the UI and selection intact.
    expect(screen.getByRole('checkbox', { name: '选择会话 最近阅读' })).toBeChecked()
    expect(screen.getByText('TCP 三次握手')).toBeInTheDocument()
    expect(load).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '取消全选' }))
    expect(screen.getAllByRole('checkbox').every(checkbox => checkbox.getAttribute('aria-checked') === 'false')).toBe(true)
    expect(screen.getByRole('button', { name: '批量删除' })).toBeDisabled()
  })

  it('does not automatically select subsequently loaded sessions or fetch more pages for select-all', async () => {
    const { props, load, update } = fixture()
    await enterBatchMode()
    fireEvent.click(screen.getByRole('button', { name: '全选已加载' }))
    const original = props.ungroupedSessions[0]
    const next = { id: 'r2', title: '下一页会话', pinned: false, status: 'active' }
    update({ ungroupedSessions: [next] })
    expect(screen.getByRole('checkbox', { name: '选择会话 下一页会话' })).not.toBeChecked()
    expect(screen.getByRole('button', { name: '全选已加载' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(props.onRequestMixedBatchDeletion).toHaveBeenLastCalledWith(props.projects, [original])
    fireEvent.click(screen.getByRole('button', { name: '全选已加载' }))
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(props.onRequestMixedBatchDeletion).toHaveBeenLastCalledWith(props.projects, [original, next])
    expect(load).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '取消全选' }))
    expect(screen.getByRole('button', { name: '批量删除' })).toBeDisabled()
  })

  it('preserves selected sessions when their folder collapses and the loaded page is evicted', async () => {
    const { props, update } = fixture({ 'project:p1': true })
    const project = props.projects[0]
    const selectedSession = project.sessions[0]
    await enterBatchMode()
    fireEvent.click(screen.getByRole('checkbox', { name: '选择会话 TCP 三次握手' }))
    fireEvent.click(screen.getByRole('button', { name: '折叠项目分组' }))
    expect(screen.queryByRole('checkbox', { name: '选择会话 TCP 三次握手' })).not.toBeInTheDocument()
    update({ projects: [{ ...project, sessions: [] }] })
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(props.onRequestSessionBatchDeletion).toHaveBeenCalledExactlyOnceWith([selectedSession])
    fireEvent.click(screen.getByRole('button', { name: '展开项目分组' }))
    update({ projects: [project] })
    expect(screen.getByRole('checkbox', { name: '选择会话 TCP 三次握手' })).toBeChecked()
  })

  it('deduplicates an evicted session if its project is selected afterwards', async () => {
    const { props, update } = fixture()
    await enterBatchMode()
    fireEvent.click(screen.getByRole('checkbox', { name: '选择会话 TCP 三次握手' }))
    update({ projects: [{ ...props.projects[0], sessions: [] }] })
    fireEvent.click(screen.getByRole('checkbox', { name: '选择项目 计算机网络' }))
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(props.onRequestMixedBatchDeletion).toHaveBeenCalledExactlyOnceWith(props.projects, [])
  })

  it('clears retained child selections when their project is explicitly unchecked', async () => {
    const { props, update } = fixture()
    await enterBatchMode('管理项目 计算机网络')
    update({ projects: [{ ...props.projects[0], sessions: [] }] })
    fireEvent.click(screen.getByRole('checkbox', { name: '选择项目 计算机网络' }))
    expect(screen.getByRole('button', { name: '批量删除' })).toBeDisabled()
    expect(props.onRequestSessionBatchDeletion).not.toHaveBeenCalled()
  })

  it('keeps newly loaded children covered by the selected project without sending duplicate session deletions', async () => {
    const { props, update } = fixture()
    await enterBatchMode()
    fireEvent.click(screen.getByRole('button', { name: '全选已加载' }))
    const next = { id: 's2', title: '项目下一页', pinned: false, status: 'active', project_id: 'p1' }
    update({ projects: [{ ...props.projects[0], sessions: [...props.projects[0].sessions, next] }] })
    expect(screen.getByRole('checkbox', { name: '选择会话 项目下一页' })).toBeChecked()
    expect(screen.getByRole('button', { name: '取消全选' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(props.onRequestMixedBatchDeletion).toHaveBeenCalledExactlyOnceWith(props.projects, props.ungroupedSessions)
  })

  it.each(['button', 'menu'])('exits via the %s and clears both selection types before re-entry', async exit => {
    fixture()
    await enterBatchMode()
    fireEvent.click(screen.getByRole('button', { name: '全选已加载' }))
    if (exit === 'menu') {
      await openMenu('管理项目分组')
      fireEvent.click(screen.getByRole('menuitem', { name: '退出批量管理' }))
    } else {
      fireEvent.click(screen.getByRole('button', { name: '退出批量管理' }))
    }
    await waitFor(() => expect(screen.queryByRole('checkbox')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: '批量删除' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '管理项目 计算机网络' })).toBeInTheDocument()
    await enterBatchMode()
    expect(screen.getAllByRole('checkbox').every(checkbox => checkbox.getAttribute('aria-checked') === 'false')).toBe(true)
    expect(screen.getByRole('button', { name: '批量删除' })).toBeDisabled()
  })

  it('keeps the 100-session limit across evicted pages and still allows cancelling all selections', async () => {
    const sessions = Array.from({ length: 105 }, (_, index) => ({ id: `r${index}`, title: `会话 ${index}`, pinned: false, status: 'active' }))
    const { props, load, update } = fixture({}, { projects: [], ungroupedSessions: sessions })
    await enterBatchMode()
    fireEvent.click(screen.getByRole('button', { name: '全选已加载' }))
    expect(screen.getAllByRole('checkbox').filter(checkbox => checkbox.getAttribute('aria-checked') === 'true')).toHaveLength(100)
    const unchecked = screen.getAllByRole('checkbox').find(checkbox => checkbox.getAttribute('aria-checked') === 'false')!
    fireEvent.click(unchecked)
    expect(unchecked).not.toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(vi.mocked(props.onRequestSessionBatchDeletion).mock.lastCall?.[0]).toHaveLength(100)
    const next = { id: 'next', title: '后续会话', pinned: false, status: 'active' }
    update({ ungroupedSessions: [next] })
    fireEvent.click(screen.getByRole('checkbox', { name: '选择会话 后续会话' }))
    expect(screen.getByRole('checkbox', { name: '选择会话 后续会话' })).not.toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(vi.mocked(props.onRequestSessionBatchDeletion).mock.lastCall?.[0]).toHaveLength(100)
    fireEvent.click(screen.getByRole('button', { name: '取消全选' }))
    expect(screen.getByRole('button', { name: '批量删除' })).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: '选择会话 后续会话' }))
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(props.onRequestSessionBatchDeletion).toHaveBeenLastCalledWith([next])
    expect(load).not.toHaveBeenCalled()
  })

  it('shows project-covered sessions as selected even beyond the standalone-session limit', async () => {
    const sessions = Array.from({ length: 101 }, (_, index) => ({ id: `s${String(index).padStart(3, '0')}`, title: `项目会话 ${index}`, pinned: false, status: 'active', project_id: 'p1' }))
    const { props } = fixture({ 'project:p1': true }, { projects: [{ id: 'p1', title: '大项目', status: 'active', sessions }], ungroupedSessions: [] })
    await enterBatchMode('管理项目 大项目')
    expect(screen.getByRole('checkbox', { name: '选择会话 项目会话 100' })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(props.onRequestMixedBatchDeletion).toHaveBeenCalledExactlyOnceWith(props.projects, [])
    // Unchecking a project-covered child must remove project selection, not accidentally add that child.
    fireEvent.click(screen.getByRole('checkbox', { name: '选择会话 项目会话 100' }))
    expect(screen.getByRole('checkbox', { name: '选择项目 大项目' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: '选择会话 项目会话 100' })).not.toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: '批量删除' }))
    expect(vi.mocked(props.onRequestSessionBatchDeletion).mock.lastCall?.[0]).toHaveLength(100)
  })

  it('only removes confirmed deletions for the matching workspace', async () => {
    fixture()
    await enterBatchMode()
    fireEvent.click(screen.getByRole('button', { name: '全选已加载' }))
    const deleted = (workspaceId: string) => act(() => {
      window.dispatchEvent(new CustomEvent('learngraph:sessions-deleted', { detail: { workspaceId, projectIds: ['p1'], sessionIds: ['r1'] } }))
    })
    deleted('other-workspace')
    expect(screen.getByRole('button', { name: '批量删除' })).toBeEnabled()
    deleted('sidebar-test')
    expect(screen.getByRole('button', { name: '批量删除' })).toBeDisabled()
    expect(screen.getAllByRole('checkbox').every(checkbox => checkbox.getAttribute('aria-checked') === 'false')).toBe(true)
  })
})
