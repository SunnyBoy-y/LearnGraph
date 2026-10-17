import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { MessageResponse } from "@/components/ai-elements/message";
import { MessagePartRenderer } from "@/components/chat/message-part-renderer";
import { AuthContext, type AuthContextValue } from "@/features/auth/auth-context-value";
import "@/index.css";

const marker = "[[artifact:card_inst_6f265bd7c31c]]";
const intro = "已按你的两点要求更新完成 👇\n\n新增 = {头盔, 风镜, 曲柄/脚踏同步转动}\n\n";
const details = "\n\n**这次改动的内容：**\n\n- **头盔：** 戴在头顶，采用青绿色渐变。\n- **风镜：** 镜片与头盔配色呼应。\n- **同步踩踏：** 双腿、曲柄与脚踏同步转动。";

function Scene() {
  return <figure className="rounded-2xl border p-4 mb-5">
    <figcaption className="text-sm font-semibold mb-3">骑自行车的鹈鹕 · SVG（头盔 + 风镜 + 同步踩踏）</figcaption>
    <svg viewBox="0 0 800 330" className="w-full rounded-xl" role="img" aria-label="重绘的鹈鹕骑自行车示意图">
      <rect width="800" height="330" fill="#a8d9fb" /><circle cx="691" cy="65" r="45" fill="#ffda66" />
      <path d="M65 70h110m-35-15h60" stroke="white" strokeWidth="26" strokeLinecap="round" />
      <path d="M0 300h800v30H0z" fill="#93c861" />
      <g fill="none" stroke="#293b46" strokeWidth="10"><circle cx="260" cy="228" r="68" /><circle cx="543" cy="228" r="68" /></g>
      <g fill="none" stroke="#e85245" strokeWidth="10" strokeLinejoin="round"><path d="M260 228l99-120 45 120H260l250-116 33 116M359 108l151 4" /></g>
      <path d="M500 90h31" stroke="#293b46" strokeWidth="12" strokeLinecap="round" />
      <ellipse cx="375" cy="87" rx="83" ry="51" fill="#f5f8fc" stroke="#d0dbe5" strokeWidth="3" />
      <path d="M407 68q22-73 63-74" fill="none" stroke="#f5f8fc" strokeWidth="30" />
      <circle cx="479" cy="23" r="26" fill="#f5f8fc" stroke="#d0dbe5" strokeWidth="3" />
      <path d="M451 15q2-35 46-19l13 18z" fill="#358d7a" />
      <path d="M500 24l100 13-100 7z" fill="#ffc868" stroke="#de9f42" strokeWidth="2" />
      <path d="M450 18h38" stroke="#243f45" strokeWidth="7" /><circle cx="491" cy="20" r="9" fill="#a6dbea" stroke="#243f45" strokeWidth="4" />
      <path d="M365 129l-5 72 41 13m-9-82 50 109 21 2" fill="none" stroke="#efad58" strokeWidth="10" />
    </svg>
  </figure>;
}

function Preview() {
  const [streaming, setStreaming] = useState(false);
  const content = streaming ? intro + marker.slice(0, -1) : intro + marker + details;
  return <main className="mx-auto max-w-[1440px] p-6 md:p-10">
    <header className="flex flex-wrap items-start justify-between gap-4 mb-8">
      <div><h1 className="text-xl font-semibold">产物占位符 · 修复前后</h1><p className="mt-2 text-sm text-muted-foreground">按截图重建的同尺寸预览；修复后使用实际消息渲染组件。示意图为重绘。</p></div>
      <button className="rounded-lg border px-4 py-2 text-sm" onClick={() => setStreaming(!streaming)}>{streaming ? "查看完整回答" : "查看流式未完成标记"}</button>
    </header>
    <div className="grid gap-8 md:grid-cols-2">
      {[false, true].map(after => <section key={String(after)} data-preview={after ? "after" : "before"} className="min-w-0 rounded-2xl border p-5 md:p-7">
        <h2 className="font-semibold mb-5">{after ? "修复后" : "修复前"}<span className="ml-3 text-xs font-normal text-muted-foreground">{after ? "仅展示正文与产物" : "内部标记直接出现在正文中"}</span></h2>
        <Scene />
        {after ? <MessagePartRenderer streaming={streaming} part={{ id: "preview-answer", type: "text", status: streaming ? "streaming" : "completed", content }} /> : <MessageResponse>{content}</MessageResponse>}
      </section>)}
    </div>
  </main>;
}

createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={new QueryClient()}>
    <MemoryRouter><AuthContext.Provider value={{ workspaceId: "preview" } as AuthContextValue}><Preview /></AuthContext.Provider></MemoryRouter>
  </QueryClientProvider>,
);
