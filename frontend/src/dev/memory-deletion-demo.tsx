import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MoreHorizontal, Plus } from 'lucide-react'
import { MemoryAllList } from '@/features/memory/components/memory-all-list'
import { memoryCategoryLabel } from '@/features/memory/memory-display'
import { Button } from '@/components/ui/button'
import type { MemoryEntry } from '@/types/memory'
import '@/index.css'

const sample = [
  { id: 'pref-example', title: '学习新知识时，先看一个具体例子，再理解背后的原理。', record_kind: 'learning_preference' },
  { id: 'pref-time', title: '更习惯在晚上集中学习，每次专注 45 分钟。', record_kind: 'learning_preference' },
  { id: 'goal-db', title: '本周目标是掌握数据库索引和事务隔离。', record_kind: 'teacher_focus' },
  { id: 'episode', title: '今天完成了 B+ 树与哈希索引的比较练习。', record_kind: 'event_summary', view_source: 'event' },
].map((item) => ({ ...item, source_ids: ['source'], revision: 1, zone: 'topics', state: 'active', namespace: 'workspace' })) as MemoryEntry[]

function Demo() {
  const [items, setItems] = useState(sample)
  const [notice, setNotice] = useState('示例数据 · 删除只作用于当前预览，不连接工作区')
  const [before, setBefore] = useState(false)
  async function remove(ids: string[]) {
    setItems((previous) => previous.filter((item) => !ids.includes(item.id)))
    setNotice(`已删除 ${ids.length} 条示例记忆。点击「重置示例」可重新体验。`)
  }
  return <main className="mx-auto max-w-[1120px] space-y-6 px-5 py-12">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><p className="mb-3 text-xs tracking-widest text-muted-foreground">WORKSPACE MEMORY</p>
        <h1 className="text-3xl font-semibold tracking-tight">我的学习记忆报告</h1>
        <p className="mt-3 text-sm text-muted-foreground">管理 AI 导师记住的学习偏好、目标与学习记录。</p></div>
      <div className="flex gap-2"><Button onClick={() => setBefore(!before)} variant="outline">{before ? '查看修改后' : '查看修改前（重建）'}</Button>
        <Button onClick={() => { setItems(sample); setNotice('已重置示例数据') }} variant="outline">重置示例</Button></div>
    </header>
    <p aria-live="polite" className="rounded-xl border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">{notice}</p>
    <div className="border-b pb-3 text-sm font-medium">全部记忆 <span className="ml-2 text-muted-foreground">{before ? sample.length : items.length}</span></div>
    {before ? <section className="space-y-3">
      <p className="text-xs text-muted-foreground">修改前界面重建 · 与修改后使用相同宽度和示例内容</p>
      <div className="flex flex-wrap gap-2">{['全部', '学习偏好', '学习目标', '学习记录'].map((label) => <Button key={label} size="sm" variant="outline">{label}</Button>)}<Button className="ml-auto" size="sm" variant="outline"><Plus className="size-4" />手动新增</Button></div>
      <div className="surface divide-y overflow-hidden">{sample.map((item) => <div className="flex items-start gap-3 px-5 py-3.5" key={item.id}><div className="flex-1"><p className="text-sm font-medium leading-5">{item.title}</p><p className="mt-1.5 text-xs text-muted-foreground">{memoryCategoryLabel(item)} · 1 个来源</p></div><MoreHorizontal className="size-4" /></div>)}</div>
    </section> : <MemoryAllList busy={false} createAction={<Button size="sm" variant="outline" disabled><Plus className="size-4" />手动新增</Button>}
      items={items} workspaceId="preview" onDelete={(id) => void remove([id])} onDeleteMany={remove}
      onRestoreRevision={() => undefined} onUpdate={async () => undefined} />}
  </main>
}

createRoot(document.getElementById('root')!).render(<QueryClientProvider client={new QueryClient()}><Demo /></QueryClientProvider>)
