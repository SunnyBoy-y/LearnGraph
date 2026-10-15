import { describe, expect, it } from "vitest";

import {
  availabilitySummary,
  composerAllowed,
  deriveAvailability,
  voiceNotices,
  type VoiceAvailability,
} from "./voice-availability";

const HEALTHY: VoiceAvailability = { input: true, output: true, model: true, playback: true };

describe("voice availability derivation", () => {
  it("keeps every capability available when nothing degraded", () => {
    expect(deriveAvailability([])).toEqual(HEALTHY);
    expect(deriveAvailability([], { playbackBlocked: false })).toEqual(HEALTHY);
  });

  it("loses only input when ASR degraded", () => {
    const availability = deriveAvailability(["asr"]);
    expect(availability.input).toBe(false);
    expect(availability.output).toBe(true);
    expect(availability.model).toBe(true);
    expect(availability.playback).toBe(true);
  });

  it("loses only the model when the LLM degraded", () => {
    const availability = deriveAvailability(["llm"]);
    expect(availability.model).toBe(false);
    expect(availability.input).toBe(true);
    expect(availability.output).toBe(true);
    expect(availability.playback).toBe(true);
  });

  it("loses output but keeps playback when TTS degraded", () => {
    // 拒绝"TTS 坏 ⇒ 播放能力也坏"的合并：播放能力描述的是浏览器是否允许
    // audio.play()，TTS 降级与它无关，混在一起会让恢复声音按钮凭空出现。
    const availability = deriveAvailability(["tts"]);
    expect(availability.output).toBe(false);
    expect(availability.playback).toBe(true);
    expect(availability.input).toBe(true);
    expect(availability.model).toBe(true);
  });

  it("loses output and playback when the browser blocked audio", () => {
    const availability = deriveAvailability([], { playbackBlocked: true });
    expect(availability.playback).toBe(false);
    expect(availability.output).toBe(false);
    expect(availability.input).toBe(true);
    expect(availability.model).toBe(true);
  });

  it("keeps output unavailable when TTS and playback are both broken", () => {
    const availability = deriveAvailability(["tts"], { playbackBlocked: true });
    expect(availability.output).toBe(false);
    expect(availability.playback).toBe(false);
  });

  it("leaves all four capabilities untouched by a network degradation", () => {
    // network 不是四项能力之一：它只产生提示，不得顺手关掉模型或输入。
    expect(deriveAvailability(["network"])).toEqual(HEALTHY);
  });

  it("degrades every capability independently at once", () => {
    expect(deriveAvailability(["asr", "llm", "tts", "network"], { playbackBlocked: true })).toEqual({
      input: false,
      output: false,
      model: false,
      playback: false,
    });
  });

  it("does not mutate the degraded stage list it is given", () => {
    const stages = ["asr", "tts"];
    deriveAvailability(stages);
    expect(stages).toEqual(["asr", "tts"]);
  });
});

describe("voice composer gating", () => {
  it("unlocks the composer after a transport error", () => {
    expect(composerAllowed(HEALTHY, "error")).toBe(true);
  });

  it("unlocks the composer when the user can no longer be understood", () => {
    expect(composerAllowed(deriveAvailability(["asr"]), "connected")).toBe(true);
  });

  it("unlocks the composer when the model cannot answer", () => {
    expect(composerAllowed(deriveAvailability(["llm"]), "connected")).toBe(true);
  });

  it("keeps voice mode owning the composer while input and model work", () => {
    expect(composerAllowed(HEALTHY, "connected")).toBe(false);
    expect(composerAllowed(HEALTHY, "connecting")).toBe(false);
    expect(composerAllowed(HEALTHY, "idle")).toBe(false);
  });

  it("rejects opening the composer just because the answer cannot be heard", () => {
    // 拒绝"任何降级都放开文字输入"：TTS 或播放坏掉时用户仍然应该正常说话。
    expect(composerAllowed(deriveAvailability(["tts"]), "connected")).toBe(false);
    expect(composerAllowed(deriveAvailability(["network"]), "connected")).toBe(false);
    expect(composerAllowed(deriveAvailability([], { playbackBlocked: true }), "connected")).toBe(false);
  });
});

describe("voice notices", () => {
  it("says nothing when every capability works", () => {
    expect(voiceNotices(HEALTHY, [])).toEqual([]);
  });

  it("asks the user to type when speech recognition is gone", () => {
    const notices = voiceNotices(deriveAvailability(["asr"]), ["asr"]);
    expect(notices).toHaveLength(1);
    expect(notices[0]?.id).toBe("asr");
    expect(notices[0]?.action).toBe("type");
    expect(notices[0]?.text).toContain("文字输入");
  });

  it("surfaces the TTS degradation that used to be hidden", () => {
    // F07 回归点：TTS 降级以前只挂在 ASR 的 textFallback 门后，永不出现。
    const notices = voiceNotices(deriveAvailability(["tts"]), ["tts"]);
    expect(notices).toHaveLength(1);
    expect(notices[0]?.id).toBe("tts");
    expect(notices[0]?.action).toBe("type");
    expect(notices[0]?.text).toContain("文字显示");
  });

  it("rejects reporting a TTS degradation as a recognition failure", () => {
    const notices = voiceNotices(deriveAvailability(["tts"]), ["tts"]);
    expect(notices.map((notice) => notice.id)).not.toContain("asr");
  });

  it("offers to resume audio when the browser blocked playback", () => {
    const notices = voiceNotices(deriveAvailability([], { playbackBlocked: true }), []);
    expect(notices).toHaveLength(1);
    expect(notices[0]?.id).toBe("playback");
    expect(notices[0]?.action).toBe("resume-audio");
  });

  it("gives both notices when playback is blocked and TTS is degraded", () => {
    const notices = voiceNotices(deriveAvailability(["tts"], { playbackBlocked: true }), ["tts"]);
    expect(notices.map((notice) => notice.id)).toEqual(["tts", "playback"]);
    expect(notices.map((notice) => notice.action)).toEqual(["type", "resume-audio"]);
  });

  it("asks for a retry on model and network degradations", () => {
    expect(voiceNotices(deriveAvailability(["llm"]), ["llm"]).map((notice) => notice.action)).toEqual([
      "retry",
    ]);
    expect(voiceNotices(deriveAvailability(["network"]), ["network"]).map((notice) => notice.action)).toEqual([
      "retry",
    ]);
  });

  it("emits every applicable notice in the frozen declaration order", () => {
    const availability = deriveAvailability(["network", "llm", "asr", "tts"], { playbackBlocked: true });
    const notices = voiceNotices(availability, ["network", "llm", "asr", "tts"]);
    expect(notices.map((notice) => notice.id)).toEqual(["asr", "tts", "llm", "network", "playback"]);
    expect(notices.every((notice) => typeof notice.text === "string" && notice.text.length > 0)).toBe(true);
    expect(notices.every((notice) => notice.action !== null)).toBe(true);
  });

  it("refuses to announce a degradation from half the facts", () => {
    // llm 以能力标志为判据、tts 以降级阶段为判据，两者必须由同一次
    // processor.error 同源推导。任一半边单独喂进来时不产生提示，避免界面
    // 只凭半个事实就宣布"语音播报已不可用"。
    expect(voiceNotices(HEALTHY, ["llm"])).toEqual([]);
    expect(voiceNotices({ ...HEALTHY, output: false }, [])).toEqual([]);
  });
});

describe("voice availability summary", () => {
  it("falls back to the unconnected sentence", () => {
    expect(availabilitySummary(HEALTHY, "idle", "closed")).toBe("语音导师未连接");
  });

  it("merges the connection sentence with the live state", () => {
    expect(availabilitySummary(HEALTHY, "connected", "listening")).toBe("正在聆听，请直接说话");
    expect(availabilitySummary(HEALTHY, "connected", "thinking")).toBe("导师正在思考，你随时可以插话");
    expect(availabilitySummary(HEALTHY, "connecting", "closed")).toBe("正在接通语音导师…");
    expect(availabilitySummary(HEALTHY, "reconnecting", "closed")).toBe("语音连接中断，正在重连…");
    expect(availabilitySummary(HEALTHY, "connected", "closed")).toBe("语音导师已连接，请直接说话");
  });

  it("reports the transport failure with or without a reason", () => {
    expect(availabilitySummary(HEALTHY, "error", "error")).toBe("语音连接失败");
    expect(availabilitySummary(HEALTHY, "error", "error", "麦克风被拒绝")).toBe("麦克风被拒绝");
  });

  it("puts the TTS degradation into the status line, not only into the notices", () => {
    const availability = deriveAvailability(["tts"]);
    const summary = availabilitySummary(availability, "connected", "speaking");
    expect(summary).toContain("回答不会发声");
    expect(summary).toContain("导师正在回答，你随时可以插话");
  });

  it("says the recognition is gone even while the link claims to listen", () => {
    const summary = availabilitySummary(deriveAvailability(["asr"]), "connected", "listening");
    expect(summary).toContain("语音识别不可用");
    expect(summary).toContain("正在聆听，请直接说话");
  });

  it("names every degraded capability, in one parenthetical", () => {
    const availability = deriveAvailability(["asr", "llm", "tts"], { playbackBlocked: true });
    const summary = availabilitySummary(availability, "connected", "closed");
    expect(summary.match(/（/g)).toHaveLength(1);
    expect(summary).toContain("语音识别不可用");
    expect(summary).toContain("导师暂时无法回答");
    expect(summary).toContain("回答不会发声");
  });

  it("stays identical to the connection sentence when nothing is degraded", () => {
    expect(availabilitySummary(HEALTHY, "connected", "speaking")).not.toContain("（");
  });
});
