/**
 * 刷新恢复的两处新契约（本地校验用例；本仓 `**\/*.test.*` 被 .gitignore 忽略）。
 *
 * 1) `/transcript` 现在也会交回"正在进行"的回合，前端不能把它当成失败回合：
 *    进行中的回合没有答案，但它还在被回答，不能给用户一个"重试"的暗示。
 * 2) 重载后仍可回到通话：标记带时间戳，且不能活得比服务端会话 TTL 更久。
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  VOICE_RESUME_MAX_AGE_MS,
  clearVoiceSessionResumable,
  markVoiceSessionResumable,
  readVoiceSessionResumable,
} from "./voice-session-markers";
import { emptyTranscriptState, orderedEntries, seedFromServerTurns } from "./voice-transcript";

describe("刷新后仍在进行的语音回合", () => {
  it("只回显用户提问，不带助手文本、也不标成失败", () => {
    const entries = orderedEntries(
      seedFromServerTurns(emptyTranscriptState(), [
        { turn_id: "turn_1", user_text: "水为什么会结冰？", status: "accepted" },
      ]),
    );
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      role: "user",
      text: "水为什么会结冰？",
      final: true,
      turnId: "turn_1",
    });
    expect(entries[0].deliveryStatus).toBeUndefined();
    expect(entries[0].failureReason).toBeUndefined();
  });

  it("已落定但没答案的回合仍然带重试态", () => {
    const entries = orderedEntries(
      seedFromServerTurns(emptyTranscriptState(), [
        { turn_id: "turn_2", user_text: "问", status: "failed", failure_reason: "llm" },
      ]),
    );
    expect(entries[0].deliveryStatus).toBe("failed");
    expect(entries[0].failureReason).toBe("llm");
  });

  it("有答案的回合照常重建助手气泡", () => {
    const entries = orderedEntries(
      seedFromServerTurns(emptyTranscriptState(), [
        { turn_id: "turn_3", user_text: "问", assistant_text: "答", status: "finalized" },
      ]),
    );
    expect(entries.map((entry) => entry.role)).toEqual(["user", "assistant"]);
    expect(entries[1].text).toBe("答");
  });
});

describe("回到通话中的标记", () => {
  beforeEach(() => {
    clearVoiceSessionResumable("ws", "s");
  });

  it("写入后可读到，清掉后读不到", () => {
    expect(readVoiceSessionResumable("ws", "s")).toBe(false);
    markVoiceSessionResumable("ws", "s");
    expect(readVoiceSessionResumable("ws", "s")).toBe(true);
    clearVoiceSessionResumable("ws", "s");
    expect(readVoiceSessionResumable("ws", "s")).toBe(false);
  });

  it("只认本会话，且不会活得比服务端会话 TTL 更久", () => {
    markVoiceSessionResumable("ws", "s");
    expect(readVoiceSessionResumable("ws", "other")).toBe(false);
    expect(VOICE_RESUME_MAX_AGE_MS).toBe(45 * 60 * 1000);
    // 用一个为负的窗口模拟"标记比 TTL 还老"：入口必须自己消失，而不是留一个只会失败的按钮。
    expect(readVoiceSessionResumable("ws", "s", -1)).toBe(false);
  });
});
