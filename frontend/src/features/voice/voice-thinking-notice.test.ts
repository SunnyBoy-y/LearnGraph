import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/voice", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/api/voice")>(),
  getVoiceCapabilities: async () => ({ agent_capabilities: [], voice_provider_ready: true }),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/api/client")>(),
  apiClient: { get: async () => ({ events: [] }), delete: async () => ({}) },
}));

import {
  ingestVoiceRtviMessage,
  voiceSessionController,
} from "./voice-session-controller";

/**
 * 前台实时语音回合恒为「关闭思考」的界面一侧。
 *
 * 服务端在拼不出"关闭思考"字段时（自定义网关、能力快照缺失等）**照常服务**，并按
 * 厂商默认继续思考——那份代价（每轮首字明显变慢）必须由界面说出来，否则用户只会
 * 觉得服务坏了。它走 durable `processor.notice`（code=thinking_off_unavailable），
 * 不是 `processor.error`：通话不降级、不放开文字输入、麦克风不静音。
 *
 * 这里钉住三件事：白名单外的 notice 不上界面、提示不是降级、新通话/换模型能清掉它。
 */

const RTVI_LABEL = "rtvi-ai";
let seq = 0;

function pushDurableEvent(type: string, payload: Record<string, unknown>): void {
  seq += 1;
  ingestVoiceRtviMessage(
    JSON.stringify({
      label: RTVI_LABEL,
      type: "server-message",
      data: {
        event: {
          event_id: `evt-${type}-${seq}`,
          event_seq: seq,
          session_id: "vs_notice",
          session_epoch: 1,
          type,
          phase: type,
          payload,
        },
      },
    }),
  );
}

beforeEach(() => {
  // Incoming durable events must belong to the currently opened remote.
  const session = `notice-${seq + 1}`;
  localStorage.setItem("learngraph.voice.remote-sessions.v1", JSON.stringify({ [`test:${session}`]: "vs_notice" }));
  voiceSessionController.open("test", session);
  pushDurableEvent("session.created", {});
});

describe("语音面板 · 无法关闭思考的提醒", () => {
  it("白名单内的 notice 提到界面上，且不把通话降级", () => {
    pushDurableEvent("processor.notice", {
      stage: "llm",
      code: "thinking_off_unavailable",
      message: "检测到模型 deepseek-flash 无法关闭思考，回复延迟会明显增加",
    });
    const snapshot = voiceSessionController.getSnapshot();
    expect(snapshot.pipelineNotice).toBe(
      "检测到模型 deepseek-flash 无法关闭思考，回复延迟会明显增加",
    );
    // 不是降级：没有 error，也不强制文字输入。
    expect(snapshot.error).toBeNull();
    expect(snapshot.textFallback).toBe(false);
    expect(snapshot.degradedNotice).toBeNull();
  });

  it("排障类 notice 不占界面", () => {
    pushDurableEvent("processor.notice", {
      stage: "asr",
      code: "cloud_vad_no_final",
      message: "补静音后 2.5s 内没有收到 final",
    });
    expect(voiceSessionController.getSnapshot().pipelineNotice).toBeNull();
  });

  it("空文案的 notice 不产生一条空提示", () => {
    pushDurableEvent("processor.notice", {
      stage: "llm",
      code: "thinking_off_unavailable",
      message: "   ",
    });
    expect(voiceSessionController.getSnapshot().pipelineNotice).toBeNull();
  });

  it("新通话与挂断都会清掉旧提示", () => {
    pushDurableEvent("processor.notice", {
      stage: "llm",
      code: "thinking_off_unavailable",
      message: "检测到模型 X 无法关闭思考，回复延迟会明显增加",
    });
    expect(voiceSessionController.getSnapshot().pipelineNotice).not.toBeNull();

    pushDurableEvent("session.created", {});
    expect(voiceSessionController.getSnapshot().pipelineNotice).toBeNull();

    pushDurableEvent("processor.notice", {
      stage: "llm",
      code: "thinking_off_unavailable",
      message: "检测到模型 X 无法关闭思考，回复延迟会明显增加",
    });
    pushDurableEvent("session.closed", {});
    expect(voiceSessionController.getSnapshot().pipelineNotice).toBeNull();
  });
});
