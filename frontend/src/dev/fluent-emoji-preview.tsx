import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AuthContext, type AuthContextValue } from '@/features/auth/auth-context-value';
import { MessagePartRenderer } from '@/components/chat/message-part-renderer';
import { Button } from '@/components/ui/button';
import type { MessagePart } from '@/types/sessions';
import '@/index.css';
import 'katex/dist/katex.min.css';

const markdown = '## 🚀 从理解到实践\n\n用 **🧠 概念梳理**建立联系，再通过 📚 阅读与 👩🏽‍💻 编程巩固。\n\n| 阶段 | 状态 |\n| --- | --- |\n| 理解 | ✅ |\n| 实践 | 💡 |\n\n`🚀 代码保持原样`\n\n$$x^2 + y^2 = 1$$';
const blocks = [
  { id: 'start', type: 'icon', icon: 'fluent:rocket', label: '开始探索' },
  { id: 'title', type: 'heading', level: 2, text: '建立知识联系', icon: 'fluent:brain' },
  { id: 'body', type: 'paragraph', text: '💡 将复杂概念拆成可以验证的小问题。' },
  { id: 'features', type: 'feature_list', label: '学习方法', icon: 'fluent:books' },
  { id: 'read', parent_id: 'features', type: 'rail_item', icon: 'fluent:books', label: '阅读', text: '阅读基础资料，记录关键概念。' },
  { id: 'target', parent_id: 'features', type: 'rail_item', icon: 'fluent:direct-hit', label: '验证', text: '通过练习验证自己的理解。' },
  { id: 'legacy', parent_id: 'features', type: 'rail_item', icon: 'lightbulb', label: '复盘', text: '沿用已有线性图标。' },
  { id: 'tabs', type: 'tabs', label: '选择学习方式' },
  { id: 'theory', parent_id: 'tabs', type: 'section', label: '概念', icon: 'fluent:light-bulb', text: '从熟悉的例子出发。' },
  { id: 'practice', parent_id: 'tabs', type: 'section', label: '练习', icon: 'fluent:direct-hit', text: '动手调整参数，观察变化。' },
  { id: 'simulation', type: 'simulation', engine: 'quadratic', label: '二次函数' },
  { id: 'status', type: 'status_list', label: '进度' },
  { id: 'ready', parent_id: 'status', type: 'rail_item', icon: 'fluent:check-mark-button', item_status: 'completed', label: '完成准备', text: '准备工作已完成。' },
];

export function Preview() {
  const [stage, setStage] = useState(3);
  const [playing, setPlaying] = useState(false);
  const [dark, setDark] = useState(false);
  useEffect(() => { document.documentElement.classList.toggle('dark', dark); }, [dark]);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setStage(value => {
      if (value >= 3) { setPlaying(false); return value; }
      return value + 1;
    }), 1000);
    return () => window.clearInterval(timer);
  }, [playing]);
  const documentPart: MessagePart = { id: 'emoji-document', type: 'ui', status: stage === 3 ? 'completed' : 'streaming', data: {
    protocol: 'suip/0.1', block_id: 'emoji-document', component: 'document', revision: stage,
    state: stage === 3 ? 'completed' : 'streaming', props: { title: '知识探索', icon: 'fluent:sparkles' },
    blocks: blocks.slice(0, [3, 7, 11, blocks.length][stage]),
  } };
  const metrics: MessagePart = { id: 'emoji-metrics', type: 'ui', status: 'completed', data: {
    protocol: 'suip/0.1', block_id: 'emoji-metrics', component: 'metric', revision: 0, state: 'completed',
    props: { title: '练习记录', icon: 'fluent:chart-increasing', data_kind: 'example' },
    items: [ { id: 'books', label: '阅读', value: 3, icon: 'fluent:books' }, { id: 'goals', label: '目标', value: 2, icon: 'fluent:direct-hit' } ],
  } };
  const timeline: MessagePart = { id: 'emoji-timeline', type: 'ui', status: 'completed', data: {
    protocol: 'suip/0.1', block_id: 'emoji-timeline', component: 'timeline', revision: 0, state: 'completed',
    props: { title: '学习过程', icon: 'fluent:rocket' },
    items: [ { id: 'first', label: '开始', description: '准备资料', icon: 'fluent:books' }, { id: 'second', label: '验证', description: '运行练习', icon: 'fluent:check-mark-button' } ],
  } };
  return <main className="mx-auto max-w-3xl space-y-8 px-5 py-8">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <h1 className="text-xl font-semibold">Fluent Emoji</h1>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => setDark(value => !value)}>{dark ? '浅色' : '深色'}</Button>
        <Button variant="outline" onClick={() => { setStage(0); setPlaying(true); }}>回放</Button>
        <Button variant="ghost" onClick={() => { setPlaying(false); setStage(3); }}>完整内容</Button>
      </div>
    </header>
    <section data-case="markdown"><MessagePartRenderer part={{ id: 'emoji-markdown', type: 'text', status: stage === 3 ? 'completed' : 'streaming', content: stage === 3 ? markdown : markdown.slice(0, [15, 55, 110][stage]) }} streaming={stage !== 3} /></section>
    <section data-case="document"><MessagePartRenderer part={documentPart} streaming={stage !== 3} /></section>
    <section data-case="metric"><MessagePartRenderer part={metrics} /></section>
    <section data-case="timeline"><MessagePartRenderer part={timeline} /></section>
  </main>;
}

createRoot(document.getElementById('root')!).render(<QueryClientProvider client={new QueryClient()}><MemoryRouter>
  <AuthContext.Provider value={{ workspaceId: 'preview' } as AuthContextValue}><Preview /></AuthContext.Provider>
</MemoryRouter></QueryClientProvider>);
