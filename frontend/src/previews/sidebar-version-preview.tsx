import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Files, Folder, MessageSquareText, MoreHorizontal, Network, Package, PanelLeftClose } from 'lucide-react'
import { Toaster } from 'sonner'
import '@/index.css'
import './sidebar-version-preview.css'

// Isolated preview: all API requests here are fixtures, including install.
let mode = 'docker'
let update = true
let pending = false
window.fetch = async input => {
  const path = String(input)
  const status = { enabled: true, current: { version: 'v0.5' }, has_update: update,
    latest: { version: update ? 'v0.6' : 'v0.5', notes: '预览示例', url: 'https://github.com/SunnyBoy-y/LearnGraph/releases', published_at: '' },
    jobs: pending ? [{ id: 'preview', status: 'applying', kind: 'update', message: '正在更新，服务切换后会自动重连…', target_version: 'v0.6', created_at: '' }] : [] }
  if (path.endsWith('/version')) return Response.json({ version: 'v0.5', runtime_mode: mode })
  if (path.endsWith('/updates/jobs')) { pending = true; return Response.json({ id: 'preview', status: 'queued' }) }
  if (path.includes('/updates/')) return Response.json(status)
  return Response.json({ error: { message: '预览不访问真实接口' } }, { status: 404 })
}
const { SidebarVersion } = await import('@/components/layout/sidebar-version')
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
export function Preview() {
  const [scenario, setScenario] = useState('docker')
  const [dark, setDark] = useState(false)
  function change(value: string) {
    mode = value === 'source' ? 'source' : 'docker'; update = value !== 'current'; pending = false
    setScenario(value)
    void client.invalidateQueries()
  }
  return <>
    <header className="version-preview-toolbar"><div><strong>版本入口 · 前后对比</strong><p>相同尺寸的侧栏重建 · 修改后使用实际组件 · 版本与更新均为模拟数据</p></div><div>
      <select aria-label="预览运行场景" value={scenario} onChange={event => change(event.target.value)}><option value="docker">Docker · 有新版本</option><option value="current">Docker · 已是最新</option><option value="source">源码部署</option><option value="member">普通用户</option></select>
      <button onClick={() => { document.documentElement.classList.toggle('dark'); setDark(!dark) }}>{dark ? '浅色' : '深色'}</button>
    </div></header>
    <div className="version-preview-grid">{[false, true].map(after => <section className="version-preview-section" key={String(after)}>
      <div className="version-preview-label">{after ? '修改后 · 点击版本徽标查看更新' : '修改前 · 原有侧栏'}</div>
      <div className="version-preview-workspace"><aside className="sidebar-nav version-preview-sidebar">
        <div className="sidebar-nav__top flex items-center justify-between"><div className={after ? 'sidebar-brand-group' : undefined}>
          <a href="#" className="sidebar-brand flex items-center gap-2 px-2 py-2"><span className="sidebar-brand__mark grid size-8 place-items-center rounded-xl"><Network size={16}/></span><span className="sidebar-text text-lg font-semibold">LearnGraph</span></a>
          {after ? <SidebarVersion isSystemAdmin={scenario !== 'member'}/> : null}
        </div><PanelLeftClose size={17} className="text-muted-foreground"/></div>
        <nav className="version-preview-nav">{[[MessageSquareText,'新对话'],[Network,'图谱'],[Files,'资料'],[Package,'产物'],[MoreHorizontal,'更多']].map(([Icon, label]) => { const Glyph = Icon as typeof Network; return <div key={String(label)}><Glyph size={18}/>{String(label)}</div> })}</nav>
        <div className="version-preview-sessions"><strong>会话</strong>{['操作系统原理','操作系统原理（面试导向）','辣椒炒肉（家常湘菜小炒）','数据结构与算法·整体框架','SQL 查询入门与实践'].map(name => <div key={name}><Folder size={16}/><span>{name}</span></div>)}</div>
        <div className="version-preview-user"><span>H</span><div><strong>helloworld</strong><small>helloworld 的学习空间</small></div></div>
      </aside><main><small>新会话</small><h1>从一个问题开始</h1><p>我现在最应该先学什么？</p><p>请根据我当前的学习目标，帮我梳理可学的主题。</p><div className="version-preview-composer">＋　输入消息，或输入 @ 选择模式 / 文件…</div></main></div>
    </section>)}</div><Toaster/>
  </>
}
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}><Preview/></QueryClientProvider>)
