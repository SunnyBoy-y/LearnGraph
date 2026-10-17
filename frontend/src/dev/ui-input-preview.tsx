import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AuthContext, type AuthContextValue } from "@/features/auth/auth-context-value";
import { StreamUI } from "@/components/chat/stream-ui";
import "@/index.css";
import "./ui-input-preview.css";

type Event = { sequence: number; payload?: { ui_operation?: string; part?: {id: string; data: Record<string, unknown>} } };
type Recording = {source?: string; events: Event[]; history: {parts: {type: string; id: string; data: Record<string, unknown>}[]} };

function Preview() {
  const [recording, setRecording] = useState<Recording | null>(null);
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [history, setHistory] = useState(false);
  const [dark, setDark] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const live = new URLSearchParams(location.search).get("source") !== "fixture";
    fetch(live ? "/ui-input-live-events.json" : "/ui-input-events.json").then(r => {if (!r.ok) throw new Error("尚未生成后端录制数据"); return r.json();})
      .then(data => {setRecording(data); setStep(live ? 1 : 0);}).catch(e => setError(String(e)));
  }, []);
  useEffect(() => {document.documentElement.classList.toggle("dark", dark);}, [dark]);
  const events = recording?.events.filter(e => e.payload?.ui_operation) ?? [];
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setStep(value => {
      if (value >= events.length - 1) {setPlaying(false); return value;}
      return value + 1;
    }), 1400);
    return () => window.clearInterval(timer);
  }, [playing, events.length]);
  const current = events[step];
  const commitIndex = events.findIndex(e => e.payload?.ui_operation === "ui.mount");
  const stored = recording?.history.parts.find(p => p.type === "ui");
  const before = step >= commitIndex && commitIndex >= 0 ? current?.payload?.part : null;
  const after = history ? stored : current?.payload?.part;
  return <main className="input-preview">
    <div className="input-preview__eyebrow">LEARNGRAPH · {recording?.source === "live" ? "真实 DEEPSEEK / SSE 事件录制" : "实际组件 / HTTP 事件录制"}</div>
    <h1>内容先到，先读。</h1>
    <p className="input-preview__lead">同一次工具调用中，文字逐段出现。完整参数通过校验后，在原位置正式提交。</p>
    <div className="input-preview__controls">
      <button onClick={() => {setHistory(false); setStep(0); setPlaying(true);}}>重新播放</button>
      <button onClick={() => setPlaying(!playing)}>{playing ? "暂停" : "播放"}</button>
      <button onClick={() => {setPlaying(false); setHistory(!history);}}>{history ? "返回接收过程" : "查看持久化历史"}</button>
      <button onClick={() => setDark(!dark)}>{dark ? "浅色" : "深色"}</button>
    </div>
    <label className="input-preview__slider">接收进度 <input aria-label="接收进度" type="range" min={0} max={Math.max(0, events.length - 1)} value={step}
      onChange={e => {setPlaying(false); setHistory(false); setStep(Number(e.target.value));}} /></label>
    {error && <p role="alert">{error}</p>}
    <div className="input-preview__comparison">
      <article><div className="input-preview__label">修改前 <span>依据旧行为重建</span></div><h2>等完整参数，再显示</h2>
        <div className="input-preview__content">{before ? <StreamUI partId="before-recording" data={before.data} settled={step === events.length - 1} /> :
          <div className="input-preview__waiting"><span/>正在生成内容…<small>第一段已生成，仍需等待本次参数结束。</small></div>}</div>
      </article>
      <article><div className="input-preview__label input-preview__label--after">修改后 <span>{history ? "数据库历史记录" : "实际 SSE 快照回放"}</span></div><h2>{history ? "刷新后，内容仍然在" : "边接收，边显示"}</h2>
        <div className="input-preview__content">{after && <StreamUI key={history ? "history" : "stream"} partId={after.id} data={after.data} settled={history || step === events.length - 1} />}</div>
      </article>
    </div>
    <div className="input-preview__facts"><span>当前事件 <b>{history ? "历史读取" : current?.payload?.ui_operation ?? "等待数据"}</b></span>
      <span>正式 revision <b>{String(after?.data.revision ?? "—")}</b></span><span>记录 ID <b>{after?.id.slice(0, 8) ?? "—"}</b></span></div>
    <p className="input-preview__note">{recording?.source === "live" ? "数据来自隔离数据库副本中的真实 DeepSeek 调用、SSE 与持久化历史。" : "数据来自真实聊天 HTTP 路由、SSE 与数据库历史记录；模型参数由确定性测试提供。"}播放间隔用于观察，不代表真实模型速度。两侧使用相同宽度与内容，右侧复用系统 StreamUI 渲染器。</p>
  </main>;
}
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={new QueryClient()}><MemoryRouter>
    <AuthContext.Provider value={{workspaceId: "preview"} as AuthContextValue}><Preview /></AuthContext.Provider>
  </MemoryRouter></QueryClientProvider>,
);
