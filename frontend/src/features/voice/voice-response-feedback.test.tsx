import { beforeEach, expect, it, vi } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { VoiceActivityIndicator } from "./voice-activity-indicator";

vi.mock("@/api/voice", async (original) => ({
  ...await original<typeof import("@/api/voice")>(),
  getVoiceCapabilities: async () => ({ agent_capabilities: [], voice_provider_ready: true }),
}));
vi.mock("@/api/client", async (original) => ({
  ...await original<typeof import("@/api/client")>(), apiClient: { get: async () => ({ events: [] }), delete: async () => ({}) },
}));
import { ingestVoiceRtviMessage, voiceSessionController as controller } from "./voice-session-controller";
let seq = 0;
function event(type: string, payload = {}, turn = "turn-current") {
  ingestVoiceRtviMessage(JSON.stringify({ label: "rtvi-ai", type: "server-message", data: { event: {
    event_id: `feedback-${++seq}`, event_seq: seq, session_id: "vs-feedback", session_epoch: 1, turn_id: turn, type, payload,
  } } }));
}
beforeEach(() => {
  cleanup();
  const session = `feedback-${seq}`;
  localStorage.setItem("learngraph.voice.remote-sessions.v1", JSON.stringify({ [`test:${session}`]: "vs-feedback" }));
  controller.open("test", session);
  event("turn.accepted");
});

it("shows actual inference and delegation, then clears the activity", () => {
  event("assistant.activity", { state: "thinking" });
  expect(controller.getSnapshot()).toMatchObject({ state: "thinking", foregroundActivity: "thinking" });
  event("assistant.activity", { state: "delegating" });
  expect(controller.getSnapshot().foregroundActivity).toBe("delegating");
  event("assistant.activity", { state: "idle" });
  expect(controller.getSnapshot()).toMatchObject({ state: "listening", foregroundActivity: "idle" });
});

it("does not revive interrupted or completed thinking from a late event", () => {
  event("assistant.activity", { state: "thinking" });
  event("turn.interrupted");
  event("assistant.activity", { state: "delegating" });
  expect(controller.getSnapshot().foregroundActivity).toBe("idle");
  event("turn.accepted", {}, "next-turn");
  event("assistant.activity", { state: "thinking" }, "next-turn");
  event("turn.finalized", { role: "assistant" }, "next-turn");
  event("assistant.activity", { state: "thinking" }, "next-turn");
  expect(controller.getSnapshot().foregroundActivity).toBe("idle");
});

it("a finished older turn cannot clear a newer turn's indicator", () => {
  event("assistant.activity", { state: "thinking" }, "new-turn");
  event("turn.finalized", { role: "assistant" }, "old-turn");
  expect(controller.getSnapshot().foregroundActivity).toBe("thinking");
  controller.disconnect();
  expect(controller.getSnapshot().foregroundActivity).toBe("idle");
});

it("renders a clear indicator only while work is active", () => {
  const view = render(<VoiceActivityIndicator activity="thinking" />);
  expect(view.getByText("思考中")).toBeVisible();
  view.rerender(<VoiceActivityIndicator activity="delegating" />);
  expect(view.getByText("正在安排后台任务")).toBeVisible();
  view.rerender(<VoiceActivityIndicator activity="idle" />);
  expect(view.container).toBeEmptyDOMElement();
});
