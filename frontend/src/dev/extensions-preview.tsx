import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ExtensionsOverviewPanel } from '@/features/settings/extension-pages'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { StatePill, Surface, SectionHeading } from '@/components/shared/page-elements'
import '@/index.css'

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } })
const plugins = [
  { id: 'parser', name: '本地文件解析', plugin_key: 'local-file-parser', workspace_id: 'preview', version: '1.0.0', plugin_type: 'document_processor', enabled: true, status: 'enabled', permissions: ['files.read'], capabilities: ['document.parse'] },
  { id: 'subapp', name: '数据库索引交互练习', plugin_key: 'agent-subapp-demo', workspace_id: 'preview', version: '1.0.0', plugin_type: 'agent_subapp', enabled: false, status: 'configured', permissions: [], capabilities: [] },
]
queryClient.setQueryData(['plugins'], plugins)
queryClient.setQueryData(['skills'], [{ id: 'review', name: '学习复习助手', version: '1.1.0', source: 'builtin', enabled: true, status: 'enabled', manifest_json: { description: '根据到期知识点安排复习，帮助巩固已有知识。' }, required_permissions: ['mastery.read'], required_tools: ['builtin.review.list_due'] }])
queryClient.setQueryData(['mcp-servers'], [{ id: 'search', display_name: '文献检索服务', version: '2.0.0', source: '工作区配置', enabled: true, status: 'enabled', manifest_json: { description: '检索学术文献，读取论文摘要与引用信息。' }, required_permissions: ['network'], discovered_tools: ['search_papers', 'read_abstract'] }])

function Before() {
  const rows = [
    { name: '通义千问', status: 'disabled', detail: 'qwen · 未声明远程能力' },
    { name: 'OpenAI', status: 'disabled', detail: 'codex_chatgpt · 未声明远程能力' },
    { name: plugins[0].name, status: 'enabled', detail: 'document_processor · v1.0.0 · document.parse' },
    { name: plugins[1].name, status: 'disabled', detail: 'agent_subapp · v1.0.0 · 未声明 capability' },
  ]
  return <><Surface className="p-5"><SectionHeading title="实际能力记录" description="状态、版本和远程能力均来自服务端"/>
    <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{rows.map(item => <div className="rounded-xl border p-4" key={item.name}>
      <p className="text-sm font-semibold">{item.name}</p><div className="mt-2"><StatePill status={item.status}/></div>
      <p className="mt-2 text-[11px] leading-5 text-muted-foreground">{item.detail}</p><Button className="mt-3" variant="outline" size="xs" disabled>查看记录</Button>
    </div>)}</div></Surface>
    <Surface className="mt-5 p-5"><SectionHeading title="已安装插件"/>{plugins.map(plugin => <div key={plugin.id} className="flex items-center justify-between gap-4 border-b py-4 last:border-0"><div><p className="text-sm font-semibold">{plugin.name}</p><p className="mt-1 text-xs text-muted-foreground">{plugin.plugin_key} · v{plugin.version} · {plugin.plugin_type}</p></div><Badge variant="outline">{plugin.enabled ? '启用' : '停用'}</Badge></div>)}</Surface></>
}

function Demo() {
  const [before, setBefore] = useState(false)
  return <main className="mx-auto max-w-[1120px] space-y-5 px-5 py-8" onClickCapture={event => {
    if ((event.target as HTMLElement).closest('[role="switch"], a')) { event.preventDefault(); event.stopPropagation() }
  }} onKeyDownCapture={event => {
    if ((event.target as HTMLElement).closest('[role="switch"]')) { event.preventDefault(); event.stopPropagation() }
  }}>
    <header className="flex flex-wrap items-center justify-between gap-4 border-b pb-5"><div><h1 className="text-2xl font-semibold">工具与扩展</h1><p className="mt-2 text-sm text-muted-foreground">同一视口的修改对比 · 示例数据，不连接工作区</p></div><div className="flex flex-wrap gap-2">
      <Button variant={before ? 'default' : 'outline'} onClick={() => setBefore(true)}>修改前（代码重建）</Button><Button variant={before ? 'outline' : 'default'} onClick={() => setBefore(false)}>修改后（实际组件）</Button>
    </div></header>
    <p className="text-sm text-muted-foreground">{before ? '原页面重建：模型服务混入总览，子应用被列作插件。' : '当前组件：按扩展类型展示，子应用不进入列表。点击「查看介绍」可体验弹窗；管理操作在预览中禁用。'}</p>
    {before ? <Before/> : <ExtensionsOverviewPanel embedded/>}
  </main>
}

createRoot(document.getElementById('root')!).render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={['/w/preview/settings/tools']}><Routes><Route path="/w/:workspaceId/settings/tools" element={<Demo/>}/></Routes></MemoryRouter></QueryClientProvider>)
