import { QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { apiClient } from "@/api/client";
import { MessageLink } from "@/components/chat/message-link";
import { TooltipProvider } from "@/components/ui/tooltip";
import { toast } from "@/lib/notifications";
import { workspaceQueryKey } from "@/lib/query-keys";
import { deferred } from "@/test/async";
import { createTestQueryClient } from "@/test/render";
import type { Message, MessageListPage, Session } from "@/types/sessions";
import { ChatCanvasPage } from "./chat-pages";

vi.mock("@/features/auth/auth-context-value", () => ({
  useAuth: () => ({ workspaceId: "test-workspace", effectivePermissions: [] }),
}));

const workspaceId = "test-workspace";
function session(id: string): Session {
  return {
    id, workspace_id: workspaceId, title: `Conversation ${id}`, status: "active",
    memory_enabled: true, model_snapshot: {}, context_capsule: {}, graph_id: null,
    current_node_id: null, project_id: null, goal_id: null,
  } as Session;
}
function message(id: string, role: Message["role"], sessionId = "destination", content = id): Message {
  return {
    id, workspace_id: workspaceId, session_id: sessionId, parent_message_id: null,
    role, version: 1, status: "completed", content,
    parts: role === "assistant" ? [{ id: `${id}-text`, type: "text", status: "completed", content }] : [],
    provider_trace: {}, created_at: "2026-11-14T12:00:00Z",
  };
}
function page(items: Message[], hasMore = false, total = items.length): MessageListPage {
  return { items, has_more_before: hasMore, total_count: total, oldest_id: items[0]?.id ?? null, newest_id: items.at(-1)?.id ?? null };
}

// The conditional timeline fills its first 50-message window before it becomes
// successful. A full window ensures olderPage is exercised by the navigation
// effect's loadOlderMessages, not by initial query pagination.
function latestWindow() {
  return [message("latest-user", "user"), message("latest-assistant", "assistant"),
    ...Array.from({ length: 48 }, (_, index) => message(`latest-${index}`, index % 2 ? "assistant" : "user"))];
}

// Keep the real page, timeline cache, lazy mounts, scroll controller and Markdown
// renderer. Only auth and the API boundary are replaced, as in review-route-intent.
function setup({
  search = "", state = null, hash = "", initialSession = "destination",
  initialPage = page([message("target-user", "user"), message("target-assistant", "assistant")]),
  olderPage = async () => page([]),
}: {
  search?: string; state?: Record<string, unknown> | null; hash?: string;
  initialSession?: string; initialPage?: MessageListPage;
  olderPage?: () => Promise<MessageListPage>;
} = {}) {
  const queryClient = createTestQueryClient();
  const sessions = [session("source"), session("destination")];
  const provider = {
    id: "navigation-provider", provider_type: "openai_compatible_chat", display_name: "Test model",
    enabled: true, remote_capability: true, capabilities: { default_model: "navigation-model" },
  };
  const models = {
    provider_id: provider.id, status: "ok",
    models: [{ id: "navigation-model", roles: ["llm"], enabled: true, remote: true, streaming: true }],
  };
  const settings = [
    { key: "chat.suggested_prompts", value: { enabled: false } },
    { key: "chat.default_response_mode", value: { response_mode: "fast" } },
  ];
  queryClient.setQueryData(workspaceQueryKey(workspaceId, "sessions"), sessions);
  queryClient.setQueryData(workspaceQueryKey(workspaceId, "providers"), [provider]);
  queryClient.setQueryData(workspaceQueryKey(workspaceId, "provider-models", provider.id), models);
  queryClient.setQueryData(workspaceQueryKey(workspaceId, "settings"), settings);
  const get = vi.spyOn(apiClient, "get").mockImplementation(async (path, options) => {
    if (path === "/providers") return [provider];
    if (path === `/providers/${provider.id}/models`) return models;
    if (path === "/settings") return settings;
    if (path === "/sandbox/agent/readiness") return { available: true };
    const current = sessions.find(item => path === `/sessions/${item.id}`);
    if (current) return current;
    if (path === "/sessions/source/messages") return page([
      message("source-user", "user", "source", "Find an earlier conversation"),
      message("source-answer", "assistant", "source", "[Open earlier answer](/w/test-workspace/chat/destination?message=target-assistant&keep=yes#answer)"),
    ]);
    if (path === "/sessions/destination/messages") {
      return options?.query?.before_id === "latest-user" ? olderPage() : initialPage;
    }
    return [];
  });
  vi.spyOn(apiClient, "getConditional").mockImplementation((async (path: string, _etag?: string | null, options?: Parameters<typeof apiClient.getConditional>[2]) => ({
    notModified: false, etag: null, data: await apiClient.get(path, options),
  })) as typeof apiClient.getConditional);
  const post = vi.spyOn(apiClient, "post").mockRejectedValue(new Error("Navigation must not write"));
  const postSse = vi.spyOn(apiClient, "postSse");
  const error = vi.spyOn(toast, "error");
  const router = createMemoryRouter([
    { path: "/w/:workspaceId/chat/:sessionId", element: <ChatCanvasPage /> },
  ], { initialEntries: [{ pathname: `/w/${workspaceId}/chat/${initialSession}`, search, state, hash }] });
  const view = render(<StrictMode><QueryClientProvider client={queryClient}>
    <TooltipProvider><RouterProvider router={router} /></TooltipProvider>
  </QueryClientProvider></StrictMode>);
  return {
    ...view, router, get, post, postSse, error, queryClient,
    dispose() { view.unmount(); queryClient.clear(); router.dispose(); },
  };
}

function mountHost(container: HTMLElement, id: string) {
  return container.querySelector<HTMLElement>(`[data-message-mount-id="${id}"]`);
}
async function expectConsumed(view: ReturnType<typeof setup>, expectedState: unknown = null) {
  await waitFor(() => expect(new URLSearchParams(view.router.state.location.search).has("message")).toBe(false));
  expect(view.router.state.location.state).toEqual(expectedState);
  expect(view.router.state.historyAction).toBe("REPLACE");
  expect(view.post).not.toHaveBeenCalled();
  expect(view.postSse).not.toHaveBeenCalled();
}

// jsdom has no layout. Supply geometry rather than replacing the real scrolling
// hook; measuring the mount host and moving the viewport remain observable.
function mockLayout(targetId: string) {
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(4000);
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(100);
  return vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const top = this.dataset.messageMountId === targetId || this.id === `conversation-jump-${targetId}` ? 800 : 0;
    return { top, bottom: top + 100, left: 0, right: 600, width: 600, height: 100, x: 0, y: top, toJSON() {} };
  });
}

describe("conversation message link navigation", { timeout: 15_000 }, () => {
  it.each(["user", "assistant"] as const)("loads older history for a direct query %s target and highlights its mount host", async role => {
    const targetId = `target-${role}`;
    const rect = mockLayout(targetId);
    const older = deferred<MessageListPage>();
    const loadOlder = vi.fn(() => older.promise);
    const view = setup({
      search: `?message=${targetId}&keep=one&keep=two`, hash: "#answer", state: { fromSearch: true },
      initialPage: page(latestWindow(), true, 52),
      olderPage: loadOlder,
    });
    try {
      await waitFor(() => expect(loadOlder).toHaveBeenCalledTimes(1));
      expect(mountHost(view.container, targetId)).toBeNull();
      expect(new URLSearchParams(view.router.state.location.search).get("message")).toBe(targetId);
      expect(view.get).toHaveBeenCalledWith("/sessions/destination/messages", expect.objectContaining({
        query: expect.objectContaining({ before_id: "latest-user", compact: "false", limit: 40 }),
      }));
      await act(async () => { older.resolve(page([message("target-user", "user"), message("target-assistant", "assistant")], false, 52)); });
      await waitFor(() => expect(mountHost(view.container, targetId)).toHaveClass("ring-2", "ring-primary", "ring-offset-2"));
      await expectConsumed(view, { fromSearch: true });
      expect(new URLSearchParams(view.router.state.location.search).getAll("keep")).toEqual(["one", "two"]);
      expect(view.router.state.location.hash).toBe("#answer");
      expect(rect.mock.contexts.some(element => (element as HTMLElement).dataset.messageMountId === targetId || (element as HTMLElement).id === `conversation-jump-${targetId}`)).toBe(true);
      expect(view.container.querySelector('[data-conversation-scroll="true"]')?.scrollTop).toBeGreaterThan(0);
      expect(screen.getByRole("log")).toHaveAttribute("data-scroll-mode", "MANUAL_READING");
      expect(loadOlder).toHaveBeenCalledTimes(1);
      expect(view.error).not.toHaveBeenCalled();
      await waitFor(() => expect(mountHost(view.container, targetId)).not.toHaveClass("ring-2"), { timeout: 3000 });
    } finally { view.dispose(); older.resolve(page([])); }
  });

  it("reveals a query target outside the collapsed long-history tail", async () => {
    const messages = Array.from({ length: 122 }, (_, index) => message(`history-${index}`, index % 2 ? "assistant" : "user"));
    const view = setup({ search: "?message=history-1", initialPage: page(messages) });
    try {
      await waitFor(() => expect(mountHost(view.container, "history-1")).toHaveClass("ring-primary"));
      expect(mountHost(view.container, "history-121")).toBeInTheDocument();
      await expectConsumed(view);
      expect(view.error).not.toHaveBeenCalled();
    } finally { view.dispose(); }
  });

  it("keeps state.targetMessageId working and consumes only that state field", async () => {
    const view = setup({ search: "?keep=yes", hash: "#saved", state: { targetMessageId: "target-assistant", fromSearch: { term: "earlier" } } });
    try {
      await waitFor(() => expect(mountHost(view.container, "target-assistant")).toHaveClass("ring-primary"));
      await expectConsumed(view, { fromSearch: { term: "earlier" } });
      expect(view.router.state.location.search).toBe("?keep=yes");
      expect(view.router.state.location.hash).toBe("#saved");
      expect(view.error).not.toHaveBeenCalled();
    } finally { view.dispose(); }
  });

  it("reports a missing target safely, removes the query and does not retry after refetch", async () => {
    const view = setup({ search: "?message=missing&keep=yes", hash: "#saved", state: { fromSearch: true } });
    try {
      await waitFor(() => expect(view.error).toHaveBeenCalledWith("未能定位到该消息，请重新搜索"), { timeout: 4000 });
      await expectConsumed(view, { fromSearch: true });
      expect(view.router.state.location.search).toBe("?keep=yes");
      expect(view.router.state.location.hash).toBe("#saved");
      expect(mountHost(view.container, "target-user")).toBeInTheDocument();
      expect(view.container.querySelector(".ring-primary")).toBeNull();
      await act(async () => { await view.queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "messages", "destination") }); });
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 1600)); });
      expect(view.error).toHaveBeenCalledTimes(1);
    } finally { view.dispose(); }
  });

  it("cleans up the target when loading an older page fails", async () => {
    const loadOlder = vi.fn().mockRejectedValue(new Error("History unavailable"));
    const view = setup({ search: "?message=missing&keep=yes", hash: "#saved", initialPage: page(latestWindow(), true, 52), olderPage: loadOlder });
    try {
      await waitFor(() => expect(loadOlder).toHaveBeenCalledTimes(1), { timeout: 4000 });
      await waitFor(() => expect(view.error).toHaveBeenCalledWith("未能定位到该消息，请重新搜索"), { timeout: 4000 });
      await expectConsumed(view);
      expect(view.router.state.location.search).toBe("?keep=yes");
      expect(view.router.state.location.hash).toBe("#saved");
      expect(loadOlder).toHaveBeenCalledTimes(1);
      expect(mountHost(view.container, "latest-user")).toBeInTheDocument();
    } finally { view.dispose(); }
  });

  it("navigates from a rendered MessageLink to another session only when clicked", async () => {
    const view = setup({ initialSession: "source" });
    try {
      const link = await screen.findByRole("link", { name: "Open earlier answer" });
      expect(link).toHaveAttribute("href", "/w/test-workspace/chat/destination?message=target-assistant&keep=yes#answer");
      expect(link).not.toHaveAttribute("target", "_blank");
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 180)); });
      expect(view.router.state.location.pathname).toBe("/w/test-workspace/chat/source");
      expect(view.get.mock.calls.some(([path]) => path === "/sessions/destination/messages")).toBe(false);
      fireEvent.click(link);
      await waitFor(() => expect(view.router.state.location.pathname).toBe("/w/test-workspace/chat/destination"));
      await waitFor(() => expect(mountHost(view.container, "target-assistant")).toHaveClass("ring-primary"));
      await expectConsumed(view);
      expect(view.router.state.location.search).toBe("?keep=yes");
      expect(view.router.state.location.hash).toBe("#answer");
      expect(mountHost(view.container, "source-answer")).toBeNull();
    } finally { view.dispose(); }
  });
});

describe("MessageLink native fallbacks", () => {
  it("retains a native chat anchor outside a router", () => {
    render(<MessageLink href="/w/test-workspace/chat/destination?message=target-user">Conversation</MessageLink>);
    expect(screen.getByRole("link", { name: "Conversation" })).toHaveAttribute("href", "/w/test-workspace/chat/destination?message=target-user");
  });

  it("leaves external links and non-chat internal links native inside a router", () => {
    const router = createMemoryRouter([{ path: "/", element: <>
      <MessageLink href="https://example.com/reference">External reference</MessageLink>
      <MessageLink href="/w/test-workspace/graph/example">Graph</MessageLink>
    </> }]);
    const view = render(<RouterProvider router={router} />);
    try {
      const external = screen.getByRole("link", { name: "External reference" });
      expect(external).toHaveAttribute("href", "https://example.com/reference");
      expect(external).toHaveAttribute("target", "_blank");
      expect(external).toHaveAttribute("rel", "noopener noreferrer");
      expect(screen.getByRole("button", { name: "预览链接：External reference" })).toBeInTheDocument();
      const graph = screen.getByRole("link", { name: "Graph" });
      // A React Router Link prevents the default click; a native anchor does not.
      let defaultPreventedByLink: boolean | undefined;
      const interceptNativeNavigation = (event: MouseEvent) => {
        defaultPreventedByLink = event.defaultPrevented;
        event.preventDefault(); // Stop jsdom's unsupported full-document navigation.
      };
      document.addEventListener("click", interceptNativeNavigation, { once: true });
      try { fireEvent.click(graph); }
      finally { document.removeEventListener("click", interceptNativeNavigation); }
      expect(defaultPreventedByLink).toBe(false);
      expect(router.state.location.pathname).toBe("/");
    } finally { view.unmount(); router.dispose(); }
  });
});
