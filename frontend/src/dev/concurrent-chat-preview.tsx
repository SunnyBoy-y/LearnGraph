import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { apiClient } from "@/api/client";
import { AuthContext, type AuthContextValue } from "@/features/auth/auth-context-value";
import { ChatCanvasPage } from "@/features/chat/chat-pages";
import { TooltipProvider } from "@/components/ui/tooltip";
import { registerSessionStream } from "@/lib/session-streams";
import { workspaceQueryKey } from "@/lib/query-keys";
import type { Message, Session } from "@/types/sessions";
import "@/index.css";

// Controlled data drives the real chat page; this preview performs no API calls.
const workspaceId = "concurrent-preview";
const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
const sessions = ["A", "B", "C", "D"].map((label) => ({
  id: `preview-${label}`, workspace_id: workspaceId, title: `窗口 ${label}`,
  status: "active", memory_enabled: true, model_snapshot: {}, context_capsule: {},
} as Session));
const row = (label: string, role: "user" | "assistant", content: string): Message => ({
  id: `preview-${label}-${role}`, workspace_id: workspaceId, session_id: `preview-${label}`,
  parent_message_id: role === "assistant" ? `preview-${label}-user` : null,
  role, version: 1, status: role === "user" ? "completed" : "streaming", content,
  parts: role === "assistant" ? [{ id: `answer-${label}`, type: "text", status: "streaming", sequence: 1, content }] : [],
  provider_trace: {}, created_at: new Date().toISOString(),
});
const liveKey = workspaceQueryKey(workspaceId, "live-chat-messages");
const background = ["A", "B", "C"].flatMap((label) => {
  registerSessionStream(`preview-${label}`, new AbortController(), `preview-${label}-assistant`);
  return [row(label, "user", `这是窗口 ${label} 的问题`), row(label, "assistant", `窗口 ${label} 的回答正在继续。`)];
});
client.setQueryData(liveKey, background);
apiClient.get = (async (path: string) => {
  if (path === "/sessions") return sessions;
  if (path === "/providers") return [{
    id: "preview-provider", provider_type: "openai_compatible_chat", display_name: "本地预览模型",
    enabled: true, remote_capability: true, capabilities: { default_model: "preview-model" },
  }];
  if (path === "/providers/preview-provider/models") return {
    provider_id: "preview-provider", status: "ok",
    models: [{ id: "preview-model", roles: ["llm"], enabled: true, remote: true, streaming: true }],
  };
  if (path === "/settings") return [{ key: "chat.default_response_mode", value: { response_mode: "fast" } }];
  if (path.endsWith("/messages")) return { items: [], total_count: 0, has_more_before: false };
  if (path.endsWith("/messages/preview-D-user")) return row("D", "user", "验证 D 窗口只显示自己的内容");
  if (path.endsWith("/messages/preview-D-assistant")) return { ...row("D", "assistant", "这是 D 窗口的独立回复。A、B、C 的内容保留在各自窗口中。"), status: "completed" };
  return [];
}) as typeof apiClient.get;
apiClient.post = (async (path: string) => {
  if (path === "/sessions") return sessions[3];
  throw new Error("此预览只支持发送新会话的首条消息；刷新可重试。");
}) as typeof apiClient.post;
apiClient.patch = (async () => sessions[3]) as typeof apiClient.patch;
apiClient.postSse = (async function* (_path, _payload, options) {
  yield { event: "message.accepted", rawData: "", data: {
    type: "message.accepted", message_id: "preview-D-assistant", payload: { user_message_id: "preview-D-user" },
  } };
  for (const text of ["这是 D 窗口的独立回复。", "A、B、C 的内容保留在各自窗口中。"]) {
    await new Promise((resolve) => setTimeout(resolve, 600));
    if (options?.signal?.aborted) return;
    yield { event: "part.delta", rawData: "", data: {
      type: "part.delta", part: { id: "answer-D", type: "text", status: "streaming", sequence: 1, content_delta: text },
    } };
  }
  // Leave the stream open so switching back to D also exercises the overlay.
  while (!options?.signal?.aborted) await new Promise((resolve) => setTimeout(resolve, 1000));
}) as typeof apiClient.postSse;

setInterval(() => client.setQueryData<Message[]>(liveKey, (current) => current?.map((message) => {
  if (message.role !== "assistant" || message.session_id === "preview-D") return message;
  const label = message.session_id.slice(-1);
  const content = `窗口 ${label} 的回答正在继续。已输出 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}。`;
  return { ...message, content, parts: message.parts.map((part) => ({ ...part, content })) };
})), 1000);

const auth = { workspaceId, username: "预览", workspaceName: "本地模拟", effectivePermissions: [], authenticated: true } as unknown as AuthContextValue;
function Preview() {
  const navigate = useNavigate();
  return <div style={{ display: "flex", flexDirection: "column", height: "100dvh" }}>
    <header style={{ padding: 16, borderBottom: "1px solid #ddd", background: "#fff", zIndex: 5 }}>
      <strong>并发隔离预览 · 真实聊天组件 / 模拟流数据</strong>
      <p>先在 D 输入问题并发送，再切换 A、B、C，确认各自回答继续，D 只显示自己的消息。</p>
      <nav aria-label="预览窗口" style={{ display: "flex", gap: 12 }}>
        {sessions.map((session) => <button key={session.id} style={{ border: "1px solid #bbb", borderRadius: 8, padding: "6px 16px" }}
          onClick={() => navigate(`/w/${workspaceId}/chat/${session.id}`)}>{session.title}</button>)}
      </nav>
    </header>
    <main style={{ flex: 1, minHeight: 0, position: "relative" }}><Routes>
      <Route path="/w/:workspaceId/chat/:sessionId" element={<ChatCanvasPage />} />
    </Routes></main>
  </div>;
}
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={client}><AuthContext.Provider value={auth}><TooltipProvider>
    <MemoryRouter initialEntries={[`/w/${workspaceId}/chat/new`]}><Preview /></MemoryRouter>
  </TooltipProvider></AuthContext.Provider></QueryClientProvider>,
);
