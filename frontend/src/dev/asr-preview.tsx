import { useState } from "react";
import { createRoot } from "react-dom/client";
import { DictationBar } from "@/components/chat/dictation-bar";
import "@/index.css";

function Preview() {
  const [state, setState] = useState<"listening" | "finalizing" | "canceling" | "done">("listening");
  const [mobile, setMobile] = useState(false);
  const [message, setMessage] = useState("");
  const [dark, setDark] = useState(false);
  return <main style={{ maxWidth: 1020, margin: "0 auto", padding: "40px 20px", fontFamily: "inherit" }}>
    <h1 style={{ fontSize: 24, fontWeight: 650 }}>ASR 语音输入</h1>
    <p style={{ marginTop: 8, color: "var(--muted-foreground)", fontSize: 13 }}>修改前按本次截图重建双层边框；修改后使用实际 DictationBar 组件。同宽展示。</p>
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, margin: "24px 0 32px" }}>
      {["listening", "finalizing", "canceling"].map((value, i) => <button key={value} onClick={() => { setState(value as typeof state); setMessage(""); }} style={control(state === value)}>{["聆听", "等待转写", "上滑取消"][i]}</button>)}
      <button style={control(mobile)} onClick={() => setMobile(!mobile)}>{mobile ? "恢复桌面宽度" : "手机宽度"}</button>
      <button style={control(dark)} onClick={() => { document.documentElement.classList.toggle("dark"); setDark(!dark); }}>切换主题</button>
    </div>
    <div style={{ maxWidth: mobile ? 350 : "100%", margin: "0 auto", display: "grid", gap: 34 }}>
      <section><h2 style={caption}>修改前 · 语音条多了一层边框</h2><div style={{ ...surface, position: "relative", minHeight: 148 }}>
        <p style={{ color: "var(--muted-foreground)", opacity: .55, fontSize: 18 }}>输入消息…</p>
        <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 64, border: "1px solid var(--border)", borderRadius: 28, background: "var(--background)", boxShadow: "0 2px 12px rgb(0 0 0 / 3%)" }}>
          <DictationBar level={.2} finalizing={state === "finalizing"} canceling={state === "canceling"} onCancel={() => {}} onStop={() => {}} onSend={() => {}} />
        </div>
      </div></section>
      <section><h2 style={caption}>修改后 · 去掉语音条边框，直接融入输入框</h2><div style={{ ...surface, position: "relative", minHeight: 148 }}>
        <p style={{ color: "var(--muted-foreground)", opacity: .55, fontSize: 18 }}>输入消息…</p>
        {state !== "done" ? <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 64 }}><DictationBar level={.2} finalizing={state === "finalizing"} canceling={state === "canceling"}
          onCancel={() => { setState("done"); setMessage("已取消语音输入"); }}
          onStop={() => { setState("finalizing"); setMessage("等待 ASR 最后一段结果"); }}
          onSend={() => { setState("finalizing"); setMessage("转写完成后发送"); }}/></div> : null}
      </div><p style={{ marginTop: 12, minHeight: 20, fontSize: 12, color: "var(--muted-foreground)" }}>{message || (state === "finalizing" ? "正在转写语音…" : state === "canceling" ? "松开取消" : "空输入框长按 500ms 开始；松开发送，上滑取消。")}</p></section>
      <p style={{ fontSize: 12, lineHeight: 1.8, color: "var(--muted-foreground)" }}>本页用于检查动画和布局，按钮演示状态切换。真实录音与 ASR 请求在聊天页执行。</p>
    </div>
  </main>;
}
const surface = { border: "1px solid var(--border)", borderRadius: 28, padding: "20px 22px 12px", background: "var(--background)", boxShadow: "0 2px 12px rgb(0 0 0 / 3%)" } as const;
const caption = { fontSize: 13, fontWeight: 550, marginBottom: 14 } as const;
const control = (active: boolean) => ({ border: "1px solid var(--border)", borderRadius: 9, padding: "7px 12px", fontSize: 13, cursor: "pointer", color: active ? "var(--background)" : "var(--foreground)", background: active ? "var(--foreground)" : "var(--background)" });
createRoot(document.getElementById("root")!).render(<Preview />);
