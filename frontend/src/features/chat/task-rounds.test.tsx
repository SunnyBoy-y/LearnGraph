import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { apiClient } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { renderWithProviders } from "@/test/render";
import type { Message, Session } from "@/types/sessions";
import { ChatCanvasPage } from "./chat-pages";

vi.mock("@/features/auth/auth-context-value", () => ({
  useAuth: () => ({ workspaceId: "test-workspace", username: "tester", workspaceName: "Test workspace", effectivePermissions: [] }),
}));

it("renders a durable voice artifact inside its answer before the next question", async () => {
  const session = { id: "parent", workspace_id: "test-workspace", title: "轮次归属", status: "active", model_snapshot: {}, context_capsule: {} } as Session;
  const messages = ([
    ["u1", "user", "第一轮提问", "turn-1"], ["a1", "assistant", "第一轮回答", "turn-1"],
    ["u2", "user", "第二轮提问", "turn-2"], ["a2", "assistant", "第二轮回答", "turn-2"],
  ] as const).map(([id, role, content, turn], index) => ({
    id, role, content, session_id: session.id, workspace_id: session.workspace_id,
    status: "completed", version: 1, parent_message_id: null,
    parts: [{ id: `${id}-text`, type: "text", status: "completed", content, sequence: 0 }],
    provider_trace: { voice_turn_id: turn }, created_at: `2026-10-21T01:00:0${index}Z`,
  } as Message));
  vi.spyOn(apiClient, "getConditional").mockResolvedValue({ notModified: false, etag: null,
    data: { items: messages, total_count: 4, has_more_before: false } });
  vi.spyOn(apiClient, "get").mockImplementation(async path => {
    if (path === "/sessions") return [session];
    if (path.endsWith("/messages")) return { items: messages, total_count: 4, has_more_before: false };
    if (path.startsWith("/sandbox/agent-tasks?")) return { tasks: [{
      task_id: "round-artifact", title: "第一轮产物", status: "SUCCEEDED", created_at: "2026-10-21T01:00:00Z",
      spec_json: { association: { turn_id: "turn-1" } },
    }] };
    return [];
  });
  const view = renderWithProviders(<TooltipProvider><Routes>
    <Route path="/w/:workspaceId/chat/:sessionId" element={<ChatCanvasPage />} />
  </Routes></TooltipProvider>, { initialEntries: ["/w/test-workspace/chat/parent"] });
  const artifact = await screen.findByRole("heading", { name: "第一轮产物" });
  const firstAnswer = await screen.findByText("第一轮回答");
  const nextQuestion = view.container.querySelector('[data-message-id="u2"]')!;
  expect(nextQuestion).toHaveTextContent("第二轮提问");
  expect(firstAnswer.compareDocumentPosition(artifact) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(artifact.compareDocumentPosition(nextQuestion) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(artifact.closest('[aria-label="本轮产物"]')).toHaveAttribute("data-task-round", "turn-1");
  expect(view.container.querySelectorAll('[aria-label="本轮产物"]')).toHaveLength(1);
  view.unmount();
});
