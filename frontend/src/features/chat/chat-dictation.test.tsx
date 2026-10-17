import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "@/api/client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { deferred } from "@/test/async";
import { renderWithProviders } from "@/test/render";
import { startDictationOrchestrator, type DictationOrchestratorOptions } from "@/lib/dictation-orchestrator";
import { ChatCanvasPage } from "./chat-pages";

vi.mock("@/features/auth/auth-context-value", () => ({ useAuth: () => ({ workspaceId: "test-workspace", username: "tester", effectivePermissions: [] }) }));
vi.mock("@/lib/dictation-orchestrator", () => ({ dictationOrchestratorSupported: () => true, startDictationOrchestrator: vi.fn() }));

describe("composer ASR", () => {
  beforeEach(() => {
    vi.spyOn(apiClient, "get").mockImplementation(async (path) => {
      if (path === "/providers") return [
        { id: "model", provider_type: "openai_compatible_chat", display_name: "模型", enabled: true, remote_capability: true, capabilities: { default_model: "test-model" } },
        { id: "asr", provider_type: "openai_compatible_transcription", display_name: "ASR", enabled: true, remote_capability: true, capabilities: { default_transcription_model_id: "whisper-1" } },
      ];
      if (path === "/providers/model/models") return { provider_id: "model", status: "ok", models: [{ id: "test-model", roles: ["llm"], enabled: true, remote: true, streaming: true }] };
      if (path === "/settings") return [{ key: "chat.default_response_mode", value: { response_mode: "fast" } }];
      return [];
    });
    vi.spyOn(apiClient, "post").mockRejectedValue(new Error("Unexpected write"));
    vi.spyOn(apiClient, "postSse").mockImplementation(() => (async function* () {})());
    // jsdom does not supply PointerEvent fields consumed by the gesture.
    vi.stubGlobal("PointerEvent", MouseEvent);
  });

  async function open() {
    const view = renderWithProviders(<TooltipProvider><Routes><Route path="/w/:workspaceId/chat/:sessionId" element={<ChatCanvasPage />} /></Routes></TooltipProvider>, { initialEntries: ["/w/test-workspace/chat/new"] });
    await waitFor(() => expect(screen.getByRole("button", { name: "开始语音输入" })).toBeEnabled());
    return { ...view, input: view.container.querySelector("textarea")! };
  }
  async function hold(input: HTMLTextAreaElement) {
    fireEvent.pointerDown(input, { button: 0, clientY: 300, pointerId: 1 });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 550)); });
    await waitFor(() => expect(startDictationOrchestrator).toHaveBeenCalledOnce());
  }

  it("starts configured ASR on hold and waits for the final result after release", async () => {
    let options!: DictationOrchestratorOptions;
    const drain = deferred<void>();
    const upload = vi.spyOn(apiClient, "upload").mockResolvedValue({ text: "真正的 ASR 转写" });
    const stop = vi.fn(async () => { await drain.promise; options.onFinal(await options.transcribeSegment(new Blob(["audio"], { type: "audio/webm" }))); });
    vi.mocked(startDictationOrchestrator).mockImplementation(async (value) => { options = value; return { stop, abort: vi.fn() }; });
    const { input } = await open();
    await hold(input);
    expect(options.transcribeSegment).toBeTypeOf("function");
    fireEvent.pointerUp(input, { clientY: 300, pointerId: 1 });
    expect(stop).toHaveBeenCalledOnce();
    expect(screen.getByRole("status", { name: "正在转写语音" })).toBeInTheDocument();
    expect(apiClient.post).not.toHaveBeenCalled();
    await act(async () => { drain.resolve(); });
    expect(upload).toHaveBeenCalledWith("/sessions/dictation/transcriptions", expect.any(FormData));
    const form = upload.mock.calls[0][1];
    expect(form.get("provider_id")).toBe("asr");
    expect(form.get("model_id")).toBe("whisper-1");
    await waitFor(() => expect(apiClient.post).toHaveBeenCalledWith("/sessions", expect.anything(), expect.anything()));
  });

  it("stops a late ASR startup when released during microphone permission", async () => {
    const startup = deferred<{ stop: () => Promise<void>; abort: () => void }>();
    vi.mocked(startDictationOrchestrator).mockReturnValue(startup.promise);
    const { input } = await open();
    await hold(input);
    fireEvent.pointerUp(input, { clientY: 300 });
    const stop = vi.fn().mockResolvedValue(undefined);
    await act(async () => startup.resolve({ stop, abort: vi.fn() }));
    expect(stop).toHaveBeenCalledOnce();
    expect(screen.queryByRole("status", { name: "正在转写语音" })).not.toBeInTheDocument();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it.each(["swipe", "cancel", "unmount"])("aborts late startup after %s without sending", async (cancel) => {
    const startup = deferred<{ stop: () => Promise<void>; abort: () => void }>();
    vi.mocked(startDictationOrchestrator).mockReturnValue(startup.promise);
    const { input, unmount } = await open();
    await hold(input);
    if (cancel === "unmount") unmount();
    else if (cancel === "cancel") fireEvent.pointerCancel(input);
    else fireEvent.pointerUp(input, { clientY: 150 });
    const abort = vi.fn();
    const stop = vi.fn();
    await act(async () => startup.resolve({ stop, abort }));
    expect(abort).toHaveBeenCalled();
    expect(stop).not.toHaveBeenCalled();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it("shows the dotted bar for click dictation and preserves stop-time text", async () => {
    let options!: DictationOrchestratorOptions;
    vi.mocked(startDictationOrchestrator).mockImplementation(async (value) => {
      options = value;
      return { stop: async () => { options.onFinal("停止后返回的文字"); }, abort: vi.fn() };
    });
    const { input } = await open();
    fireEvent.click(screen.getByRole("button", { name: "开始语音输入" }));
    await screen.findByRole("button", { name: "取消语音输入" });
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "停止语音输入" }));
    await waitFor(() => expect(input.value).toBe("停止后返回的文字"));
    expect(screen.queryByRole("button", { name: "取消语音输入" })).not.toBeInTheDocument();
  });
});
