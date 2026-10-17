import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Bot, Brain, ChevronRight, Globe, Layers, RefreshCcw } from "lucide-react";
import { RetryAnswerMenu, RetryMenuSettings } from "@/components/chat/retry-answer-menu";
import type { ResponseMode, ThinkingMode } from "@/lib/session-composer-prefs";
import "@/index.css";

const groups = [{ id: "deepseek", label: "DeepSeek", models: [
  { id: "deepseek-flash", remote: true }, { id: "deepseek-chat", remote: true },
] }];
const trace = { model_id: "deepseek-flash", web_search: true };
const baseline = { providerId: "deepseek", modelId: "deepseek-flash", responseMode: "agentic" as ResponseMode, thinkingMode: "medium" as ThinkingMode, webSearch: true, allowedDomains: "" };

export function Preview() {
  const [settings, setSettings] = useState(baseline);
  const [open, setOpen] = useState(false);
  const [dark, setDark] = useState(false);
  const [submitted, setSubmitted] = useState<object>();
  return <main className={dark ? "dark" : ""} style={{ minHeight: "100vh", background: "var(--background)", color: "var(--foreground)", padding: 24 }}>
    <div style={{ maxWidth: 1040, margin: "0 auto" }}>
      <h1 style={{ fontSize: 24, fontWeight: 650 }}>重试面板 · 设置与摘要同步</h1>
      <p style={{ color: "var(--muted-foreground)", margin: "10px 0 20px", fontSize: 13 }}>左侧为原界面代码重建，右侧使用修改后的实际组件；两侧使用相同示例和面板尺寸。演示不会调用模型。</p>
      <button className="rounded-md border px-3 py-1.5 text-sm" onClick={() => { document.documentElement.classList.toggle("dark", !dark); setDark(!dark); }}>切换{dark ? "浅色" : "深色"}</button>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%,340px),1fr))", gap: 24, marginTop: 20 }}>
        <section>
          <h2 className="mb-3 font-medium">修改前 · 摘要固定显示历史信息</h2>
          <div className="rounded-xl border bg-muted/20" style={{ minHeight: 550, padding: 20 }}>
            <p className="mb-6 text-sm text-muted-foreground">切换模型后，摘要仍显示上次回答。</p>
            <div className="retry-answer-menu rounded-xl border bg-popover text-popover-foreground p-1.5" style={{ width: 280, maxWidth: "100%", margin: "0 auto" }}>
              <p className="px-2.5 py-2 text-[11px] text-muted-foreground">重新生成回答</p>
              <div className="flex gap-2.5 rounded-md bg-muted px-2.5 py-2 text-[13px]">
                <RefreshCcw size={16} className="my-auto text-muted-foreground" />
                <div className="grid gap-0.5"><span className="font-medium">直接重试</span><span className="text-[11px] text-muted-foreground">上次回答：deepseek-flash</span><span className="text-[11px] text-muted-foreground">联网已开启</span></div>
              </div>
              <hr className="mx-1 my-1.5" /><p className="px-2.5 py-1 text-[11px] text-muted-foreground">重试设置 · 仅本次生效</p>
              {[[Layers, "使用模型", "deepseek-chat"], [Bot, "响应模式", "智能体"], [Brain, "思考强度", "高"], [Globe, "联网搜索", "关闭"]].map(([Icon, label, value]) => {
                const RowIcon = Icon as typeof Layers;
                return <div key={String(label)} className="flex min-h-9 items-center gap-2.5 px-2.5 text-[13px]"><RowIcon size={16} className="text-muted-foreground" /><span>{String(label)}</span><span className="ml-auto text-xs text-muted-foreground">{String(value)}</span><ChevronRight size={14} /></div>;
              })}
              <p className="px-2.5 pb-2 pt-2.5 text-[11px] text-muted-foreground">重试后保留原回答及历史版本</p>
            </div>
          </div>
        </section>
        <section>
          <h2 className="mb-3 font-medium">修改后 · 更改选项即可查看本次重试设置</h2>
          <div className="rounded-xl border bg-muted/20" style={{ minHeight: 550, padding: 20, display: "flex", flexDirection: "column", alignItems: "flex-start" }}>
            <p className="text-sm text-muted-foreground">未改动显示“上次回答”；改动后显示“更改后重试”。</p>
            <div style={{ marginTop: "auto", alignSelf: "center" }}>
              <RetryAnswerMenu open={open} onOpenChange={(next) => { if (next) { setSettings(baseline); setSubmitted(undefined); } setOpen(next); }} content={open ? <RetryMenuSettings
                previousAnswerTrace={trace} groups={groups} {...settings}
                thinkingModes={["off", "low", "medium", "high"]} supportsAgent canSearch canRetry modelError={false}
                onModelChange={(providerId, modelId) => setSettings((s) => ({ ...s, providerId, modelId }))}
                onModeChange={(responseMode) => setSettings((s) => ({ ...s, responseMode, thinkingMode: responseMode === "fast" ? "off" : "medium" }))}
                onThinkingChange={(thinkingMode) => setSettings((s) => ({ ...s, thinkingMode }))}
                onSearchChange={(webSearch) => setSettings((s) => ({ ...s, webSearch }))}
                onDomainsChange={(allowedDomains) => setSettings((s) => ({ ...s, allowedDomains }))}
                onRetry={() => { setSubmitted({ provider_id: settings.providerId, model_id: settings.modelId, agent_mode: settings.responseMode === "agentic", thinking_mode: settings.responseMode === "fast" ? "off" : settings.thinkingMode, web_search: settings.webSearch, allowed_domains: settings.webSearch ? [...new Set(settings.allowedDomains.split(/[\s,，]+/u).filter(Boolean))] : [] }); setOpen(false); }}
                onConfigure={() => {}} onRefreshModels={() => {}}
              /> : null}>
                <button className="flex items-center gap-2 rounded-md border bg-background px-3 py-2 text-sm"><RefreshCcw size={16} />打开重试面板</button>
              </RetryAnswerMenu>
            </div>
          </div>
          {submitted && <div className="mt-3 rounded-lg border p-3 text-xs" role="status"><strong>本地演示 · 提交的设置</strong><pre className="mt-2 whitespace-pre-wrap break-all">{JSON.stringify(submitted, null, 2)}</pre></div>}
        </section>
      </div>
    </div>
  </main>;
}

createRoot(document.getElementById("root")!).render(<Preview />);
