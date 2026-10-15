import { describe, expect, it } from "vitest";

import {
  shouldCommitVoiceRender,
  voiceRenderStartsSession,
} from "./voice-render-policy";

describe("voice render commit policy", () => {
  it("lets interim user speech reach the conversation live", () => {
    expect(
      shouldCommitVoiceRender({
        final: false,
        role: "user",
        text: "我想问一下这个猪",
      }),
    ).toBe(true);
  });

  it("lets a sentence that is being spoken reach the conversation", () => {
    expect(
      shouldCommitVoiceRender({
        final: false,
        role: "assistant",
        text: "猪肉部位大致可以分为三类。",
      }),
    ).toBe(true);
  });

  it("rejects an empty assistant update before playback authorizes text", () => {
    expect(
      shouldCommitVoiceRender({
        final: false,
        role: "assistant",
        text: "",
      }),
    ).toBe(false);
  });

  it("commits one authoritative voice turn", () => {
    expect(
      shouldCommitVoiceRender({
        final: true,
        role: "user",
        text: "我想问一下这个猪肉部位",
      }),
    ).toBe(true);
  });

  it("rejects an update that would render an empty bubble", () => {
    expect(
      shouldCommitVoiceRender({ final: true, role: "assistant", text: "   " }),
    ).toBe(false);
  });

  it("accepts a removal-only update (settling a turn's segments)", () => {
    expect(
      shouldCommitVoiceRender({
        final: true,
        role: "user",
        text: "",
        removes: ["user-turn-abc-seg0", "user-turn-abc-seg1"],
      }),
    ).toBe(true);
  });

  it("accepts a turn-scoped retirement with no remembered row ids", () => {
    expect(
      shouldCommitVoiceRender({
        final: true,
        role: "user",
        text: "",
        retireTurnSegments: "turn-abc",
      }),
    ).toBe(true);
  });

  it("keeps non-conversational rows out of the canvas", () => {
    expect(
      shouldCommitVoiceRender({
        final: true,
        role: "background",
        text: "后台任务已完成",
      } as unknown as Parameters<typeof shouldCommitVoiceRender>[0]),
    ).toBe(false);
  });

});

/**
 * 全双工语音会话与文本会话同等地位：一条用户话落定，这个会话就不再是"空草稿"。
 *
 * 少了这条判据，侧栏会继续把语音会话当空草稿藏着，点「新会话」还会被复用回同一个
 * 会话——用户看到的就是"没离开上一通电话"。
 */
describe("voice session standing", () => {
  it("一条落定的用户回合让会话开始（等同于文本首条消息）", () => {
    expect(
      voiceRenderStartsSession({ final: true, role: "user" }),
    ).toBe(true);
  });

  it("导师的回答不算会话开始", () => {
    expect(
      voiceRenderStartsSession({ final: true, role: "assistant" }),
    ).toBe(false);
  });

  it("还在说的 ASR 分段（未落定）不算会话开始", () => {
    expect(
      voiceRenderStartsSession({ final: false, role: "user" }),
    ).toBe(false);
  });

  it("一个回合内部的 ASR 分段行不算会话开始", () => {
    // 分段只是这一轮里的一段话；它落定不代表这一轮进了会话。
    expect(
      voiceRenderStartsSession({
        final: true,
        role: "user",
        voiceSegment: true,
      }),
    ).toBe(false);
  });
});
