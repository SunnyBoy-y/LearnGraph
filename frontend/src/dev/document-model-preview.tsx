import { useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ChevronDown, Mic, Plus } from "lucide-react";
import { DocumentChatPanel, type DocumentChatPreferences } from "@/features/resources/document-chat-panel";
import { workspaceQueryKey } from "@/lib/query-keys";
import { documentPreviewFile as file, documentPreviewProviders as providers, documentPreviewModels } from "./document-model-fixtures";
import "@/index.css";

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } });
const workspaceId = file.workspace_id;
queryClient.setQueryData(workspaceQueryKey(workspaceId, "providers"), providers);
queryClient.setQueryData(workspaceQueryKey(workspaceId, "sessions"), []);
for (const provider of providers) {
  queryClient.setQueryData(workspaceQueryKey(workspaceId, "provider-models", provider.id), {
    provider_id: provider.id, status: "ok", models: documentPreviewModels(provider),
  });
}

export function Preview() {
  const [beforeClicked, setBeforeClicked] = useState(false);
  const [sent, setSent] = useState<{ content: string; preferences: DocumentChatPreferences }>();
  return <main style={{ maxWidth: 1440, margin: "32px auto", padding: "0 20px" }}>
    <h1 style={{ fontSize: 25, fontWeight: 650 }}>文档预览 · 模型与推理强度</h1>
    <p style={{ margin: "10px 0 24px", color: "var(--muted-foreground)" }}>
      截图场景代码重建，左右使用相同尺寸与示例内容。右侧为实际修改后的输入栏；模型数据为本地演示，发送仅展示所选参数。
    </p>
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%,440px),1fr))", gap: 24 }}>
      {[false, true].map((after) => <section key={String(after)} aria-label={after ? "修改后" : "修改前"} style={{ minWidth: 0 }}>
        <h2 style={{ fontSize: 17, fontWeight: 600, marginBottom: 12 }}>{after ? "修改后 · 点击选择模型和推理强度" : "修改前 · 点击只弹出当前模式"}</h2>
        <div style={{ border: "1px solid var(--border)", borderRadius: 16, background: "#fafafa", padding: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>{file.original_name}</div>
          <p style={{ color: "#8a8a86", fontSize: 11, margin: "4px 0 16px" }}>尚未建立文档版本</p>
          <div style={{ background: "linear-gradient(145deg,#17233f,#0e1321)", color: "#cbd5e9", borderRadius: 8, padding: 20, height: 420, marginBottom: 22 }}>
            <strong>知识图谱 · 闯关模式</strong>
            <p style={{ fontSize: 11, opacity: .65, marginTop: 6 }}>按图谱的前置关系排列 4 个关卡，答对 2/3 题即可过关。</p>
            {["概念与表示", "抽取与融合", "存储与查询", "推理与补全"].map((label, index) => <div key={label} style={{ border: `1px solid ${index ? "#25304a" : "#567ddd"}`, borderRadius: 10, padding: 14, marginTop: 12, fontSize: 12, opacity: index ? .5 : 1 }}>
              第 {index + 1} 关 · {label}<p style={{ marginTop: 10, fontSize: 10 }}>★★★　3 题 · 答对 2 题过关</p>
            </div>)}
          </div>
          {after ? <DocumentChatPanel compact file={file} sessionId="" workspaceId={workspaceId} selection={null} onClearSelection={() => {}} onSessionChange={() => {}} onSendToFullChat={(content, preferences) => setSent({ content, preferences })} />
            : <div className="document-chat-compact"><div className="document-chat-compact__form">
              <button aria-label="修改前添加文件" className="document-chat-compact__plus"><Plus size={16} /></button>
              <input aria-label="修改前输入栏" style={{ flex: 1, minWidth: 0, fontSize: 14, outline: 0 }} placeholder="输入消息，或输入 @ 选择模式 / 文件..." />
              <button aria-label="修改前选择模式" className="document-chat-compact__mode" onClick={() => setBeforeClicked(true)}>中<ChevronDown size={14} /></button>
              <span className="document-chat-compact__mic"><Mic size={16} /></span>
            </div></div>}
        </div>
        {!after && beforeClicked && <p role="status" style={{ marginTop: 12, color: "#777" }}>当前响应模式：中</p>}
        {after && sent && <div role="status" style={{ marginTop: 12, padding: 16, border: "1px solid var(--border)", borderRadius: 12 }}>
          <strong>发送参数（本地演示）</strong><p>{sent.content}</p>
          <pre style={{ fontSize: 12, whiteSpace: "pre-wrap", marginTop: 8 }}>{JSON.stringify(sent.preferences, null, 2)}</pre>
        </div>}
      </section>)}
    </div>
  </main>;
}

createRoot(document.getElementById("root")!).render(<QueryClientProvider client={queryClient}><MemoryRouter><Preview /></MemoryRouter></QueryClientProvider>);
