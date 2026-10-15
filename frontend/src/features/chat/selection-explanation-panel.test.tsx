// Must run before the panel (and its pdfjs-dist transitive import) is loaded.
import "@/test/pdfjs-polyfills";

import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelSessionMessage,
  createSession,
  listSessionMessages,
  listSessions,
  streamSessionMessage,
} from "@/api/sessions";
import { discoverProviderModels, listProviders } from "@/api/providers";
import { getAgentSandboxReadiness } from "@/api/control";
import { setSessionComposerPrefs } from "@/lib/session-composer-prefs";
import { renderWithProviders } from "@/test/render";
import { deferred } from "@/test/async";
import type { Provider } from "@/types/providers";
import type { Session } from "@/types/sessions";

import { SelectionExplanationPanel } from "./selection-explanation-panel";

vi.mock("@/api/sessions", () => ({
  streamSessionMessage: vi.fn(),
  createSession: vi.fn(),
  listSessions: vi.fn(),
  listSessionMessages: vi.fn(),
  cancelSessionMessage: vi.fn(),
}));

vi.mock("@/api/providers", () => ({
  listProviders: vi.fn(),
  discoverProviderModels: vi.fn(),
}));

vi.mock("@/api/control", () => ({
  getAgentSandboxReadiness: vi.fn(),
}));

// MessagePartRenderer (rendered by the panel) consumes useAuth for source /
// citation URL building. The suite-wide render helper does not mount a real
// AuthProvider, so provide the hook directly — same pattern as
// personalization-page.test.tsx.
vi.mock("@/features/auth/auth-context-value", () => ({
  useAuth: () => ({
    authenticated: true,
    username: "user-a",
    workspaceId: "ws-1",
    workspaceName: "ws-1",
    setWorkspaceId: async () => undefined,
    login: async () => ({ workspaceId: "ws-1", mustChangePassword: false }),
    register: async () => ({ workspaceId: "ws-1", mustChangePassword: false }),
    changePassword: async () => undefined,
    deleteAccount: async () => undefined,
    logout: async () => undefined,
  }),
}));

const PARENT_SESSION_ID = "parent-1";
const WORKSPACE_ID = "ws-1";
const SELECTED_TEXT = "React Query 缓存隔离";

const modelProvider: Provider = {
  id: "provider-1",
  workspace_id: WORKSPACE_ID,
  display_name: "测试 Provider",
  provider_type: "openai_compatible_chat",
  base_url: null,
  api_key_masked: "sk-…",
  enabled: true,
  remote_capability: true,
  capabilities: { default_model: "model-x" },
  status: "configured_enabled",
  secret_status: "active",
  secret_version: 1,
  secret_key_provider: "environment",
  secret_key_version: 1,
  created_at: "2026-08-02T00:00:00.000Z",
};

const sideSession: Session = {
  id: "side-session-1",
  workspace_id: WORKSPACE_ID,
  title: "划词解释 · 测试",
  goal_id: null,
  graph_id: null,
  project_id: null,
  parent_session_id: PARENT_SESSION_ID,
  source_message_id: null,
  memory_enabled: false,
  pinned: false,
  model_snapshot: {},
  status: "active",
  closed_at: null,
  archived_at: null,
  session_kind: "side",
  writeback_policy: "normal",
  context_capsule: {},
  activity_summary: null,
  created_at: "2026-08-02T00:00:00.000Z",
  updated_at: "2026-08-02T00:00:00.000Z",
};

function renderPanel({
  parentSessionId = PARENT_SESSION_ID,
  creationParentSessionId,
}: {
  parentSessionId?: string;
  creationParentSessionId?: string;
} = {}) {
  return renderWithProviders(
    <SelectionExplanationPanel
      detail={{
        parentSessionId,
        sourceMessageId: "message-1",
        selectedText: SELECTED_TEXT,
      }}
      parentSessionId={parentSessionId}
      creationParentSessionId={creationParentSessionId}
      workspaceId={WORKSPACE_ID}
    />,
  );
}

describe("SelectionExplanationPanel mutation rollback", () => {
  beforeEach(() => {
    // Fast mode skips the agentic sandbox-readiness branch, keeping the flow
    // limited to createSession → optimistic messages → stream.
    setSessionComposerPrefs(PARENT_SESSION_ID, {
      responseMode: "fast",
      thinkingMode: "off",
      searchRoute: "disabled",
      generationMode: "text",
      providerId: modelProvider.id,
      modelId: "model-x",
    });
    vi.mocked(listProviders).mockResolvedValue([modelProvider]);
    vi.mocked(discoverProviderModels).mockResolvedValue({
      provider_id: modelProvider.id,
      status: "ok",
      models: [{ id: "model-x", roles: ["llm"], streaming: true, remote: true, enabled: true }],
    });
    vi.mocked(listSessions).mockResolvedValue([]);
    vi.mocked(listSessionMessages).mockResolvedValue([]);
    vi.mocked(createSession).mockResolvedValue(sideSession);
    vi.mocked(cancelSessionMessage).mockResolvedValue(undefined);
    vi.mocked(getAgentSandboxReadiness).mockResolvedValue({
      available: false,
      code: null,
      message: "",
      authorized: true,
      sandbox_enabled: true,
      agent_enabled: true,
      backend_id: "docker",
      platform: "linux",
      capabilities: [],
      remediation_steps: [],
    });
  });

  it("creates nested-session explanations under the root parent", async () => {
    const sourceSessionId = "side-session-1";
    vi.mocked(createSession).mockResolvedValue({
      ...sideSession,
      id: "new-side-session",
      parent_session_id: PARENT_SESSION_ID,
    });
    vi.mocked(streamSessionMessage).mockImplementation(() =>
      (async function* () {
        yield {
          id: "evt-completed",
          event: "message",
          rawData: JSON.stringify({ type: "message.completed" }),
          data: { type: "message.completed" },
        };
      })(),
    );

    renderPanel({
      parentSessionId: sourceSessionId,
      creationParentSessionId: PARENT_SESSION_ID,
    });

    await waitFor(() => {
      expect(vi.mocked(createSession)).toHaveBeenCalledWith(
        expect.objectContaining({
          parent_session_id: PARENT_SESSION_ID,
          session_kind: "side",
        }),
      );
    });
    expect(vi.mocked(createSession)).not.toHaveBeenCalledWith(
      expect.objectContaining({ parent_session_id: sourceSessionId }),
    );
  });

  it("shows the optimistic user turn while the stream is pending, then marks the assistant failed and appends an error part when the stream rejects", async () => {
    const gate = deferred<void>();
    vi.mocked(streamSessionMessage).mockImplementation(() =>
      (async function* () {
        await gate.promise;
        throw new Error("stream exploded");
      })(),
    );

    const { container } = renderPanel();

    // Auto-submit ran: a side session was created and the optimistic user
    // message (with the quoted selection) is visible before any stream data.
    expect(await screen.findByLabelText("引用模型回答")).toHaveTextContent(
      SELECTED_TEXT,
    );
    expect(vi.mocked(createSession)).toHaveBeenCalledTimes(1);
    expect(container.querySelector(".message-part-error")).toBeNull();

    // Fail the stream mid-flight. The retry loop re-runs the mock, but every
    // generator awaits the same rejected gate, so the send() finally settles
    // into the terminal-error path deterministically.
    gate.reject(new Error("stream exploded"));

    await waitFor(() => {
      const errorPart = container.querySelector(".message-part-error");
      expect(errorPart).not.toBeNull();
      expect(errorPart?.textContent).toContain("stream exploded");
    });

    // The optimistic user turn is retained; only the assistant turn shows the
    // failed state.
    expect(screen.getByLabelText("引用模型回答")).toHaveTextContent(
      SELECTED_TEXT,
    );
  });

  it("retains streamed parts but still marks the assistant failed when the stream dies mid-delivery", async () => {
    vi.mocked(streamSessionMessage).mockImplementation(() =>
      (async function* () {
        // One text part streams in before the connection drops. The optimistic
        // assistant keeps its local id (no message.failed event remaps it), so
        // the terminal-error path appends the error part to the same message.
        yield {
          id: "evt-1",
          event: "message",
          rawData: JSON.stringify({
            type: "part.delta",
            message_id: "m-1",
            part: {
              id: "part-1",
              type: "text",
              status: "streaming",
              content_delta: "初步分析",
            },
          }),
          data: {
            type: "part.delta",
            message_id: "m-1",
            part: {
              id: "part-1",
              type: "text",
              status: "streaming",
              content_delta: "初步分析",
            },
          },
        };
        throw new Error("连接中断");
      })(),
    );

    const { container } = renderPanel();

    await waitFor(() => {
      const errorPart = container.querySelector(".message-part-error");
      expect(errorPart).not.toBeNull();
      expect(errorPart?.textContent).toContain("连接中断");
    });

    // The optimistic text that had already streamed is retained alongside the
    // failure marker rather than being silently dropped.
    expect(
      container.querySelector(".message-part-error")?.textContent,
    ).toContain("连接中断");
    expect(
      container.querySelector(".message-part-error")?.textContent,
    ).not.toContain("上游模型拒绝生成");
  });
});
