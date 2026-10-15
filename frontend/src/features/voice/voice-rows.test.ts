import { describe, expect, it } from "vitest";

import type { Message } from "@/types/sessions";

import { mergeVoiceTrace, retireVoiceRows, voiceTurnParentRowId, voiceRowRetired } from "./voice-rows";

/**
 * The canvas-level contract behind "全双工语音链路为什么会出现重复":
 * one user turn = at most one authoritative bubble, and its per-segment
 * fragments are retired by *turn*, not by a list of ids somebody remembered.
 */

function row(
  partial: Partial<Message> & Pick<Message, "id">,
): Message {
  return {
    workspace_id: "ws",
    session_id: "s",
    parent_message_id: null,
    role: "user",
    version: 1,
    status: "completed",
    content: "text",
    parts: [],
    provider_trace: {},
    created_at: "2026-01-01T00:00:00Z",
    ...partial,
  } as Message;
}

const segment = (id: string, turnId: string) =>
  row({
    id,
    provider_trace: { voice_turn: true, voice_segment: true, turn_id: turnId },
  });

const authoritative = (id: string, turnId: string, clientMessageId?: string) =>
  row({
    id,
    provider_trace: {
      voice_turn: true,
      voice_segment: false,
      turn_id: turnId,
      client_message_id: clientMessageId,
    },
  });

describe("retireVoiceRows", () => {
  it("retires a fragment created after a flush, by turn, without a remembered id", () => {
    // The `turn.accepted` update only knows the segment row ids its accumulator
    // saw. A `user.final` re-delivered afterwards allocates a row key it cannot
    // know about; the turn-scoped form is what removes it.
    const rows = [
      segment("temp-voice-user-turn-key-a-seg0", "turn-1"),
      authoritative("temp-voice-user-typed-cmid-1", "turn-1", "cmid-1"),
      segment("temp-voice-user-turn-key-b-seg0", "turn-1"),
    ];

    const kept = retireVoiceRows(rows, { retireTurnSegments: "turn-1" });

    expect(kept.map((item) => item.id)).toEqual(["temp-voice-user-typed-cmid-1"]);
  });

  it("keeps another turn's fragments and the authoritative rows", () => {
    const rows = [
      segment("temp-voice-user-turn-key-a-seg0", "turn-1"),
      segment("temp-voice-user-turn-key-c-seg0", "turn-2"),
      authoritative("temp-voice-user-turn-turn-2", "turn-2"),
    ];

    const kept = retireVoiceRows(rows, { retireTurnSegments: "turn-1" });

    expect(kept.map((item) => item.id)).toEqual([
      "temp-voice-user-turn-key-c-seg0",
      "temp-voice-user-turn-turn-2",
    ]);
  });

  it("still honours the explicit row-id list", () => {
    const rows = [
      segment("temp-voice-user-turn-key-a-seg1", "turn-1"),
      authoritative("temp-voice-user-turn-turn-1", "turn-1"),
    ];

    const kept = retireVoiceRows(rows, {
      removes: ["user-turn-key-a-seg1"],
    });

    expect(kept.map((item) => item.id)).toEqual(["temp-voice-user-turn-turn-1"]);
  });

  it("is a no-op without any retirement, so ordinary renders never drop rows", () => {
    const rows = [segment("temp-voice-user-turn-key-a-seg0", "turn-1")];
    expect(retireVoiceRows(rows, {})).toHaveLength(1);
    expect(retireVoiceRows(rows, { removes: [], retireTurnSegments: "" })).toHaveLength(1);
  });

  it("never retires a non-fragment row through the turn rule", () => {
    const settled = authoritative("temp-voice-user-turn-turn-1", "turn-1");
    expect(voiceRowRetired(settled, { retireTurnSegments: "turn-1" })).toBe(false);
  });
});

describe("voiceTurnParentRowId · 导师行挂到同一回合的用户行", () => {
  it("打字回合按幂等键找父行", () => {
    const rows = [
      row({ id: "temp-voice-user-typed-cmid-1", provider_trace: { voice_turn: true, client_message_id: "cmid-1" } }),
      row({ id: "temp-voice-assistant-final-turn-9", role: "assistant", provider_trace: { voice_turn: true, turn_id: "turn-9" } }),
    ];

    expect(
      voiceTurnParentRowId(rows, { turnId: "turn-9", clientMessageId: "cmid-1" }),
    ).toBe("temp-voice-user-typed-cmid-1");
  });

  it("客户端没拿到服务端回合 id 时，靠同一把客户端回合键找到父行", () => {
    // 身份事件（user.final / turn.accepted）丢过时，客户端用的回合键是自己生成的
    // 那把；用户行与导师行共用它，父链因此仍然成立 —— 这正是"同一条回答两份"的
    // 那条漏网路径。
    const clientKey = "2f0c4c1e-client-key";
    const rows = [
      row({ id: `temp-voice-user-turn-${clientKey}`, provider_trace: { voice_turn: true, turn_id: clientKey } }),
      row({ id: `temp-voice-assistant-final-${clientKey}`, role: "assistant", provider_trace: { voice_turn: true, turn_id: clientKey } }),
    ];

    expect(voiceTurnParentRowId(rows, { turnId: clientKey })).toBe(
      `temp-voice-user-turn-${clientKey}`,
    );
  });

  it("一个回合有多个用户行时，选权威行而不是逐段临时行", () => {
    const rows = [
      segment("temp-voice-user-turn-key-a-seg0", "turn-1"),
      authoritative("temp-voice-user-turn-turn-1", "turn-1"),
    ];

    expect(voiceTurnParentRowId(rows, { turnId: "turn-1" })).toBe(
      "temp-voice-user-turn-turn-1",
    );
  });

  it("找不到父行时返回 null，绝不猜一个", () => {
    expect(voiceTurnParentRowId([], { turnId: "turn-1" })).toBeNull();
    expect(voiceTurnParentRowId([], {})).toBeNull();
  });
});

describe("mergeVoiceTrace", () => {
  it("keeps an existing client_message_id when the update omits it", () => {
    // The merged `user.final` / `turn.finalized` re-render carries no
    // client_message_id; erasing the key here is what made the persisted twin
    // paint a second bubble after a refetch.
    const current = { voice_turn: true, turn_id: "turn-1", client_message_id: "cmid-1" };
    const merged = mergeVoiceTrace(current, {
      voice_turn: true,
      turn_id: "turn-1",
      client_message_id: undefined,
      deliveryStatus: "accepted",
    });

    expect(merged.client_message_id).toBe("cmid-1");
    expect(merged.deliveryStatus).toBe("accepted");
  });

  it("overwrites with defined values and tolerates an empty current trace", () => {
    expect(mergeVoiceTrace(null, { a: 1, b: undefined })).toEqual({ a: 1 });
    expect(mergeVoiceTrace({ a: 1 }, { a: 2 })).toEqual({ a: 2 });
  });
});
