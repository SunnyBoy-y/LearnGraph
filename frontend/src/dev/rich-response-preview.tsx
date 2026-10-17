import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { MessagePartRenderer } from "@/components/chat/message-part-renderer";
import { AuthContext, type AuthContextValue } from "@/features/auth/auth-context-value";
import { groupPartsForDisplay } from "@/features/chat/chat-message-parts";
import { Button } from "@/components/ui/button";
import type { MessagePart } from "@/types/sessions";
import "@/index.css";
import "katex/dist/katex.min.css";

const blocks = [
  {id: "title", type: "heading", level: 2, text: "无限的步骤，有限的时间"},
  {id: "intro", type: "paragraph", text: "阿基里斯每到达乌龟刚才的位置，乌龟又前进了一小段。**这并不意味着他永远追不上。**"},
  {id: "layout", type: "columns", columns: 2},
  {id: "explanation", type: "section", parent_id: "layout", label: "把过程不断细分"},
  {id: "text", type: "paragraph", parent_id: "explanation", text: "问题在于，把「有无限多个阶段」误当成「需要无限长时间」。每段用时都更短，总和可以收敛。"},
  {id: "formula", type: "math", parent_id: "explanation", text: "\\sum_{n=0}^{\\infty} ar^n = \\frac{a}{1-r},\\quad |r|<1"},
  {id: "sim", type: "simulation", parent_id: "layout", engine: "chase", label: "动手试一试", caption: "示意参数，可在回答继续生成时操作。"},
  {id: "tabs", type: "tabs", label: "换个视角"},
  {id: "intuition", parent_id: "tabs", type: "paragraph", label: "直觉", text: "切分的方式不会改变真实运动；数学上的无限细分，可以发生在有限时间内。"},
  {id: "math", parent_id: "tabs", type: "paragraph", label: "计算", text: "示例中领先距离为 10，速度分别为 2 和 1，因此相遇时间为 10 ÷ (2 − 1) = 10。"},
  {id: "plot", type: "chart", visual: {props: {title: "每一步的时间逐渐变短", columns: [{key: "time", label: "阶段用时"}], source_note: "数学示例，非观测数据"}, rows: [8,4,2,1,.5].map((time,index)=>({id: `r${index}`, label: `第${index+1}步`, values:{time}}))}},
];
export function Preview() {
  const [stage, setStage] = useState(4);
  const [playing, setPlaying] = useState(false);
  const [dark, setDark] = useState(false);
  useEffect(() => { document.documentElement.classList.toggle("dark", dark); }, [dark]);
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => setStage(value => { if(value >= 4) {setPlaying(false); return 4;} return value + 1; }), 1800);
    return () => clearInterval(timer);
  }, [playing]);
  const count = [0,2,7,10,11][stage];
  const documentPart: MessagePart = {id: "preview-rich-document", type: "ui", status: stage === 4 ? "completed" : "streaming", sequence: 2, data: {
    protocol: "suip/0.1", block_id: "zeno", component: "document", revision: stage, state: stage === 4 ? "completed" : "streaming", props:{title: "芝诺悖论"}, blocks: blocks.slice(0,count),
  }};
  const text: MessagePart = {id: "preview-live-text", type: "text", status: stage === 4 ? "completed" : "streaming", sequence: 1,
    content: "芝诺的阿基里斯悖论，关键在于如何理解无限。\n\n[[artifact:block_id:zeno]]\n\n" + (stage >= 3 ? "**结论：**无限个越来越小的时间段，可以加成一个有限的总时间。" : "")};
  const parts = groupPartsForDisplay([text, documentPart], {live:true}).flatMap(group=>group.parts);
  return <main className="mx-auto max-w-[1500px] px-5 py-8 md:px-10">
    <header className="mb-8 flex flex-wrap justify-between gap-4">
      <div><h1 className="text-xl font-semibold">LearnGraph · 同一条回答，自然组织内容</h1>
        <p className="mt-2 max-w-3xl text-sm text-muted-foreground">同视口、同内容的代码重建对照。右侧使用正式消息分组与渲染组件；这里的流式节奏为本地回放，无模型请求。正式系统仍在原聊天入口。</p></div>
      <div className="flex gap-2"><Button variant="outline" onClick={()=>setDark(!dark)}>{dark ? "浅色" : "深色"}</Button>
        <Button variant="outline" onClick={()=>{setStage(0);setPlaying(true);}}>回放流式生成</Button>
        <Button variant="ghost" onClick={()=>{setPlaying(false);setStage(4);}}>完整内容</Button></div>
    </header>
    <div className="grid gap-10 lg:grid-cols-2">
      <section data-preview="before" className="min-w-0"><h2 className="mb-6 border-b pb-3 text-sm font-medium text-muted-foreground">原有表达 · 单列文字与独立图表（重建）</h2>
        <MessagePartRenderer part={{id:"before-text",type:"text",status:"completed",content:"芝诺的阿基里斯悖论，关键在于如何理解无限。\n\n## 无限的步骤，有限的时间\n\n阿基里斯每到达乌龟刚才的位置，乌龟又前进了一小段。**这并不意味着他永远追不上。**\n\n### 把过程不断细分\n\n问题在于，把「有无限多个阶段」误当成「需要无限长时间」。每段用时都更短，总和可以收敛。\n\n$$\\sum_{n=0}^{\\infty} ar^n = \\frac{a}{1-r}$$\n\n示例中领先距离为 10，速度分别为 2 和 1，因此相遇时间为 10。\n\n**结论：**无限个越来越小的时间段，可以加成一个有限的总时间。"}} />
        <MessagePartRenderer part={{id:"before-chart",type:"ui",status:"completed",data:{protocol:"suip/0.1",block_id:"before",component:"chart",revision:0,state:"completed",...blocks[10].visual}}} />
      </section>
      <section data-preview="after" className="min-w-0"><h2 className="mb-6 border-b pb-3 text-sm font-medium text-muted-foreground">融合后 · 图文布局与本地交互{playing && " · 正在回放"}</h2>
        <div className="space-y-5">{parts.map(part=><MessagePartRenderer key={part.id} part={part} streaming={stage < 4} />)}</div>
      </section>
    </div>
  </main>;
}
const previewRoot = createRoot(document.getElementById("root")!);
function SvgFallbackPreview() {
  const [pending, setPending] = useState(false);
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400"><path d="M320 310Q170 245 125 110Q230 45 320 150Q410 45 515 110Q470 245 320 310Z" fill="#c9d99b" stroke="#526c35" stroke-width="4"/><path d="M320 310L320 160M320 310L190 130M320 310L450 130" fill="none" stroke="#526c35" stroke-width="3"/><text x="320" y="365" text-anchor="middle" font-size="26" fill="#202020">扇形叶片 · 分叉叶脉</text></svg>';
  const part = (after: boolean): MessagePart => ({id: `svg-preview-${after}`, type: "ui", status: pending ? "streaming" : "completed", data: {
    protocol: "suip/0.1", block_id: `svg-preview-${after}`, component: "document", revision: pending ? 0 : 1, state: pending ? "streaming" : "completed", props: {title: "银杏叶的识别要点"}, blocks: [
      {id: "layout", type: "media_text", columns: 2},
      {id: "image", parent_id: "layout", type: "image", alt: "银杏叶的扇形轮廓和分叉叶脉", resource_state: pending ? "pending" : "unavailable", resource_reason: pending ? "" : "timeout", fallback_svg: after ? svg : ""},
      {id: "explanation", parent_id: "layout", type: "paragraph", text: "银杏叶呈扇形，叶脉从叶柄向外反复分叉。顶部可能有一道浅裂口；辨认时结合整体轮廓和叶脉，不只看某一片叶子的缺口。"},
    ],
  }});
  return <main className="mx-auto max-w-[1400px] px-5 py-8">
    <h1 className="text-xl font-semibold">图库失败 → SVG 示意图</h1>
    <p className="my-4 text-sm text-muted-foreground">同内容、同宽度的代码重建对照，使用正式消息组件。SVG 为固定演示素材；这里不发起模型或图库请求，正式验收另在聊天页进行。</p>
    <Button variant="outline" onClick={() => setPending(value => !value)}>{pending ? "模拟图库超时" : "查看检索中状态"}</Button>
    <div className="mt-6 grid gap-8 lg:grid-cols-2">{[false,true].map(after => <section key={String(after)} className="min-w-0">
      <h2 className="mb-4 border-b pb-3">{after ? "修改后：自动切换 SVG，保留相邻讲解" : "修改前：图库失败只显示替代文字"}</h2>
      <MessagePartRenderer part={part(after)} streaming={pending} />
    </section>)}</div>
  </main>;
}
// oxlint-disable-next-line react/only-export-components -- Dev-only entry for replaying saved messages in the browser check.
export function renderVerifiedResponse(parts: MessagePart[], title: string) {
  previewRoot.render(<QueryClientProvider client={new QueryClient()}><MemoryRouter>
    <AuthContext.Provider value={{workspaceId:"preview"} as AuthContextValue}>
      <main className="mx-auto max-w-4xl p-8"><header className="mb-8 border-b pb-5"><h1 className="text-xl font-semibold">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">真实模型经正式 SSE 生成并保存的消息；此处用正式组件回放最终快照。</p></header>
        <div className="space-y-5">{groupPartsForDisplay(parts).filter(group=>group.kind==="parts").flatMap(group=>group.parts).map(part=><MessagePartRenderer key={part.id} part={part} interactive={false} />)}</div>
      </main>
    </AuthContext.Provider>
  </MemoryRouter></QueryClientProvider>);
}
previewRoot.render(<QueryClientProvider client={new QueryClient()}><MemoryRouter>
  <AuthContext.Provider value={{workspaceId:"preview"} as AuthContextValue}>{new URLSearchParams(location.search).get("case") === "svg-fallback" ? <SvgFallbackPreview /> : <Preview />}</AuthContext.Provider>
</MemoryRouter></QueryClientProvider>);
