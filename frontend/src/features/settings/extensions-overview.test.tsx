import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes, useLocation } from 'react-router-dom'
import { listMcpServers, listPlugins, listProviders, listSkills, togglePlugin } from '@/api'
import { renderWithProviders } from '@/test/render'
import type { Plugin } from '@/types/plugins'
import type { MCPServer, Skill } from '@/types/extensions'
import { ExtensionsOverviewPanel } from './extension-pages'

vi.mock('@/api', () => ({ listPlugins: vi.fn(), listProviders: vi.fn(), listSkills: vi.fn(), listMcpServers: vi.fn(), togglePlugin: vi.fn() }))
vi.mock('./control-pages', () => ({ ComponentAdministration: () => null, McpRevisionPanel: () => null, SandboxAdministration: () => null, SkillRevisionPanel: () => null }))
vi.mock('./skill-package-editor', () => ({ SkillPackageEditor: () => null }))
vi.mock('./skills-hub-extras', () => ({ AddSkillDialog: () => null }))

const plugin: Plugin = { id: 'parser', workspace_id: 'ws', plugin_key: 'local-file-parser', name: '本地文件解析', version: '1.2.0', plugin_type: 'document_processor', status: 'enabled', enabled: true, permissions: ['files.read'], capabilities: ['document.parse'] }
const subapp: Plugin = { ...plugin, id: 'app', plugin_key: 'agent-subapp-123', name: '子应用练习', plugin_type: 'agent_subapp', enabled: false }

beforeEach(() => {
  vi.mocked(listPlugins).mockResolvedValue([plugin, subapp])
  vi.mocked(listSkills).mockResolvedValue([])
  vi.mocked(listMcpServers).mockResolvedValue([])
})

function Location() { return <output aria-label="地址">{useLocation().pathname}</output> }
function showOverview() {
  return renderWithProviders(<Routes><Route path="/w/:workspaceId/settings/tools" element={<><ExtensionsOverviewPanel embedded/><Location/></>}/></Routes>, { initialEntries: ['/w/ws/settings/tools'] })
}

describe('extensions overview', () => {
  it('shows Skills, MCP and plugins while excluding internal subapps and model services', async () => {
    vi.mocked(listSkills).mockResolvedValue([{ id: 'skill', name: '复习助手', version: '1.0', source: 'builtin', enabled: true, status: 'enabled', manifest_json: { description: '安排间隔复习。' }, required_permissions: [], required_tools: [] } as unknown as Skill])
    vi.mocked(listMcpServers).mockResolvedValue([{ id: 'mcp', display_name: '文献检索', version: '2.0', source: 'remote', enabled: true, status: 'enabled', manifest_json: {}, required_permissions: [], discovered_tools: ['search_papers'] } as unknown as MCPServer])
    showOverview()
    expect(await screen.findByText('复习助手')).toBeInTheDocument()
    expect(screen.getByText('文献检索')).toBeInTheDocument()
    expect(screen.getAllByText('本地文件解析')).toHaveLength(2)
    expect(screen.queryByText('子应用练习')).not.toBeInTheDocument()
    expect(screen.queryByText('查看记录')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '查看介绍' })).toHaveLength(3)
    expect(listProviders).not.toHaveBeenCalled()
  })

  it('opens an introduction with purpose, capabilities and permissions without navigating or toggling', async () => {
    showOverview()
    await userEvent.click(await screen.findByRole('button', { name: '查看介绍' }))
    const dialog = await screen.findByRole('dialog', { name: '本地文件解析' })
    expect(within(dialog).getByText('处理上传的文档，为学习与对话提供可读取的内容。')).toBeInTheDocument()
    expect(within(dialog).getByText('插件 · v1.2.0')).toBeInTheDocument()
    expect(within(dialog).getByText('document.parse')).toBeInTheDocument()
    expect(within(dialog).getByText('files.read')).toBeInTheDocument()
    expect(screen.getByLabelText('地址')).toHaveTextContent('/w/ws/settings/tools')
    expect(togglePlugin).not.toHaveBeenCalled()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows the empty state when the server returns only subapp ownership records', async () => {
    vi.mocked(listPlugins).mockResolvedValue([subapp])
    showOverview()
    expect(await screen.findByText('当前工作区没有已安装扩展。可前往 Skills Hub 或 MCP 分区添加。')).toBeInTheDocument()
    expect(screen.getByText('没有已安装插件。')).toBeInTheDocument()
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })
})
