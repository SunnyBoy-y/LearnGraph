import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { apiClient } from "@/api/client";
import { AuthContext, type AuthContextValue } from "@/features/auth/auth-context-value";
import { ChatCanvasPage } from "@/features/chat/chat-pages";
import { TooltipProvider } from "@/components/ui/tooltip";
import { workspaceQueryKey } from "@/lib/query-keys";
import { hasSessionComposerPrefs, setSessionComposerPrefs } from "@/lib/session-composer-prefs";
import type { Message, Session } from "@/types/sessions";
import "@/index.css";

// Real chat page, controlled responses. No backend or model is called.
const workspaceId = "composer-preview";
const activeId = "composer-preview-medium";
const otherId = "composer-preview-fast";
const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
const records: Session[] = [activeId, otherId].map((id) => ({
  id, workspace_id: workspaceId, title: "思考力度保持验证", status: "active", memory_enabled: true,
  model_snapshot: { provider_id: "preview-provider", model_id: "preview-model", thinking_mode: "off", agent_mode: false },
  context_capsule: {},
  goal_id: null, graph_id: null, project_id: null, parent_session_id: null,
  source_message_id: null, pinned: false, closed_at: null, archived_at: null,
  session_kind: "main", writeback_policy: "normal", activity_summary: null,
  created_at: "2026-10-17T00:00:00Z", updated_at: "2026-10-17T00:00:00Z",
}));
if (!hasSessionComposerPrefs(activeId)) setSessionComposerPrefs(activeId, {
  responseMode: "agentic", thinkingMode: "medium", providerId: "preview-provider", modelId: "preview-model",
});
if (!hasSessionComposerPrefs(otherId)) setSessionComposerPrefs(otherId, { responseMode: "fast", thinkingMode: "off" });
const messages: Message[] = [];
let round = 0;
apiClient.get = (async (path: string) => {
  if (path === "/sessions") return records.map((record) => ({ ...record, title: `快照刷新 ${round}` }));
  if (path === "/settings") return [];
  if (path === "/providers") return [{
    id: "preview-provider", provider_type: "openai_compatible_chat", display_name: "预览模型",
    enabled: true, remote_capability: true,
    capabilities: { default_model: "preview-model", reasoning_efforts: ["low", "medium", "high", "xhigh"] },
  }, {
    id: "preview-search", provider_type: "tavily", display_name: "预览搜索", enabled: true, remote_capability: true, capabilities: {},
  }];
  if (path === "/providers/preview-provider/models") return {
    provider_id: "preview-provider", status: "ok",
    models: ["preview-model", "preview-model-alt"].map((id) => ({ id, roles: ["llm"], enabled: true, remote: true, streaming: true })),
  };
  const message = messages.find((item) => path.endsWith(`/messages/${item.id}`));
  if (message) return message;
  if (path.endsWith("/context-usage")) return {
    session_id: activeId, estimated_tokens: 1200, input_budget_tokens: 32000,
    compaction_threshold_tokens: 24000, remaining_tokens: 30800, used_ratio: 0.05,
    context_window_tokens: 32000, compaction_ratio: 0.75, message_count: messages.length,
  };
  if (path.endsWith("/messages")) {
    const items = messages.filter((item) => path.includes(`/${item.session_id}/`));
    return { items, total_count: items.length, has_more_before: false };
  }
  return [];
}) as typeof apiClient.get;
apiClient.post = (async () => records[0]) as typeof apiClient.post;
apiClient.patch = (async () => records[0]) as typeof apiClient.patch;
apiClient.postSse = (async function* (path, payload) {
  const request = payload as { content: string; thinking_mode: string; agent_mode: boolean; provider_id: string; model_id: string };
  const id = path.includes(otherId) ? otherId : activeId;
  round += 1;
  const userId = `preview-user-${round}`;
  const assistantId = `preview-assistant-${round}`;
  const content = `第 ${round} 轮模拟回复：${request.model_id}，thinking_mode=${request.thinking_mode}，agent_mode=${request.agent_mode}。可继续发送，或切换会话后返回。`;
  const base = { workspace_id: workspaceId, session_id: id, version: 1, status: "completed", provider_trace: {}, created_at: new Date().toISOString() };
  messages.push({ ...base, id: userId, role: "user", parent_message_id: null, content: request.content, parts: [] } as Message,
    { ...base, id: assistantId, role: "assistant", parent_message_id: userId, content, parts: [{ id: `text-${round}`, type: "text", status: "completed", content }] } as Message);
  window.dispatchEvent(new CustomEvent("composer-preview-request", { detail: `第 ${round} 轮：${request.model_id} / ${request.thinking_mode} / agent=${request.agent_mode}` }));
  yield { event: "message.accepted", rawData: "", data: { type: "message.accepted", message_id: assistantId, payload: { user_message_id: userId } } };
  yield { event: "part.delta", rawData: "", data: { type: "part.delta", part: { id: `text-${round}`, type: "text", status: "streaming", sequence: 1, content_delta: content } } };
  yield { event: "message.completed", rawData: "", data: { type: "message.completed", message_id: assistantId } };
}) as typeof apiClient.postSse;

export function Preview() {
  const navigate = useNavigate();
  const [lastRequest, setLastRequest] = useState("尚未发送");
  useEffect(() => {
    const receive = (event: Event) => setLastRequest((event as CustomEvent<string>).detail);
    window.addEventListener("composer-preview-request", receive);
    return () => window.removeEventListener("composer-preview-request", receive);
  }, []);
  const buttonStyle = { padding: "4px 10px", border: "1px solid #aaa", borderRadius: 6 };
  return <div style={{ display: "flex", flexDirection: "column", height: "100dvh" }}>
    <header style={{ padding: 12, borderBottom: "1px solid #aaa", fontSize: 13 }}>
      <strong>会话偏好验证 · 真实聊天组件 / 模拟数据</strong>
      <p>旧快照固定为极速。连续发送两轮，刷新快照、切换后返回或刷新页面，所选力度应保持。</p>
      <nav style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBlock: 8 }}>
        <button style={buttonStyle} onClick={() => navigate(`/w/${workspaceId}/chat/${activeId}`)}>返回主会话</button>
        <button style={buttonStyle} onClick={() => navigate(`/w/${workspaceId}/chat/${otherId}`)}>切换极速会话</button>
        <button style={buttonStyle} onClick={() => client.setQueryData(workspaceQueryKey(workspaceId, "sessions"), records.map((record) => ({ ...record, title: `旧快照刷新 ${Date.now()}` })))}>刷新旧快照</button>
      </nav>
      <output data-testid="last-request">{lastRequest}</output>
    </header>
    <main style={{ flex: 1, minHeight: 0, position: "relative" }}><Routes>
      <Route path="/w/:workspaceId/chat/:sessionId" element={<ChatCanvasPage />} />
    </Routes></main>
  </div>;
}
const auth = { workspaceId, username: "预览", workspaceName: "模拟数据", effectivePermissions: [], authenticated: true } as unknown as AuthContextValue;
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={client}><AuthContext.Provider value={auth}><TooltipProvider>
    <MemoryRouter initialEntries={[`/w/${workspaceId}/chat/${activeId}`]}><Preview /></MemoryRouter>
  </TooltipProvider></AuthContext.Provider></QueryClientProvider>,
);
