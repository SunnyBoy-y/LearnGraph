import { useState } from "react";
import { createRoot } from "react-dom/client";
import { StreamUI } from "@/components/chat/stream-ui";
import type { UISnapshot } from "@/lib/stream-ui";
import "@/index.css";
import "@/components/chat/rich-document.css";

const timeline: UISnapshot = {
  protocol: "suip/0.1", block_id: "tomato", component: "timeline",
  revision: 0, state: "completed",
  props: {
    title: "接下来大致会看到的阶段（经验范围，视品种与阳台条件而定）",
    description: "", chart_type: "line", columns: [], unit: "", source_note: "",
  },
  rows: [], nodes: [], edges: [],
  items: [
    { id: "bud", value: null, date: "约第 6–7 周", label: "现蕾 / 第一花序", description: "留意顶端和叶腋间的小花蕾，保持充足日照，避免氮肥过量。" },
    { id: "flower", value: null, date: "现蕾后约 1 周", label: "开花", description: "黄色花朵逐渐开放。晴天轻摇花序，帮助授粉，并保持通风。" },
    { id: "fruit", value: null, date: "开花后数周", label: "坐果与膨大", description: "花瓣脱落后，小果逐渐长大。浇水保持稳定，及时为枝条提供支撑。" },
    { id: "ripe", value: null, date: "坐果后数周", label: "转色成熟", description: "果实从绿色逐渐转色。具体时间随品种、光照与温度变化。" },
  ],
};

const css = `
body { margin:0; background:var(--background); color:var(--foreground); }
.timeline-preview { max-width:1360px; margin:auto; padding:36px 28px; font-family:var(--font-sans); }
.timeline-preview h1 { font-size:24px; font-weight:650; margin:0 0 8px; }
.timeline-preview__note { color:var(--muted-foreground); font-size:14px; margin:0; }
.timeline-preview__controls { display:flex; flex-wrap:wrap; gap:10px; margin:24px 0; }
.timeline-preview button { border:1px solid var(--border); background:var(--card); border-radius:8px; padding:8px 14px; font-size:14px; cursor:pointer; }
.timeline-preview__grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:24px; align-items:start; }
.timeline-preview__panel { min-width:0; }
.timeline-preview__panel > h2 { font-size:14px; color:var(--muted-foreground); margin:0 0 12px; }
.timeline-preview__frame { border:1px solid var(--border); border-radius:14px; padding:20px; background:var(--card); min-height:450px; }
.timeline-preview--narrow .timeline-preview__frame { max-width:350px; margin:auto; padding:12px; }
/* Reproduce the original rules around the same real Timeline renderer. */
.timeline-preview--before .message-component.stream-ui[data-ui-component=timeline] { font-size:12px; line-height:1.75; }
.timeline-preview--before .stream-ui[data-ui-component=timeline] .stream-ui__title { font-size:12px; line-height:1.5; }
.timeline-preview--before .stream-ui[data-ui-component=timeline] .stream-ui__heading > svg { width:16px; height:16px; margin:0; }
.timeline-preview--before .stream-ui[data-ui-component=timeline] .stream-ui__status { font-size:11px; }
.timeline-preview--before .stream-ui .stream-ui__timeline { margin:12px 14px 14px; padding:0; }
.timeline-preview--before .stream-ui .stream-ui__timeline > li { padding:0 0 22px 22px; border-left:1px solid var(--border); }
.timeline-preview--before .stream-ui .stream-ui__timeline > li::before { top:5px; left:-4px; width:7px; height:7px; }
.timeline-preview--before .stream-ui .stream-ui__timeline time { display:inline; font-size:11px; font-weight:400; color:var(--muted-foreground); }
.timeline-preview--before .stream-ui .stream-ui__timeline details { padding-block:.5rem; border-bottom:1px solid var(--border); }
.timeline-preview--before .stream-ui .stream-ui__timeline summary { font-size:inherit; font-weight:500; line-height:inherit; margin-top:5px; padding:0; }
.timeline-preview--before .stream-ui .stream-ui__timeline p { font-size:inherit; color:var(--muted-foreground); margin:7px 0 0; line-height:inherit; }
@media(max-width:760px) { .timeline-preview { padding:24px 16px; } .timeline-preview__grid { grid-template-columns:minmax(0,1fr); } .timeline-preview__frame { padding:12px; min-height:0; } }
`;

function Preview() {
  const [dark, setDark] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const [expanded, setExpanded] = useState(false);
  return <main className={`timeline-preview${narrow ? " timeline-preview--narrow" : ""}`}>
    <style>{css}</style>
    <h1>时间线 · 修改前后</h1>
    <p className="timeline-preview__note">根据截图重建示例内容；两侧使用同一实际组件，左侧还原旧样式，右侧使用修改后的样式。点击阶段可展开详情。</p>
    <div className="timeline-preview__controls">
      <button aria-pressed={dark} onClick={() => { setDark(!dark); document.documentElement.classList.toggle("dark", !dark); }}>深色模式</button>
      <button aria-pressed={narrow} onClick={() => setNarrow(!narrow)}>窄屏宽度</button>
      <button aria-pressed={expanded} onClick={() => { const next = !expanded; setExpanded(next); document.querySelectorAll<HTMLDetailsElement>(".timeline-preview details").forEach(item => { item.open = next; }); }}>展开全部详情</button>
    </div>
    <div className="timeline-preview__grid">
      {["before", "after"].map(mode => <section key={mode} className={`timeline-preview__panel timeline-preview--${mode}`} aria-label={mode === "before" ? "修改前" : "修改后"}>
        <h2>{mode === "before" ? "修改前 · 旧样式还原" : "修改后 · 当前实际组件"}</h2>
        <div className="timeline-preview__frame rich-doc"><StreamUI embedded partId={`preview-${mode}`} data={timeline} /></div>
      </section>)}
    </div>
  </main>;
}
createRoot(document.getElementById("root")!).render(<Preview />);
