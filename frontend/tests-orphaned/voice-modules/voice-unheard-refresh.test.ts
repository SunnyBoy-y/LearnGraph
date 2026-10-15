import { describe, expect, it } from "vitest";

import {
  emptyTranscriptState,
  orderedEntries,
  reduceVoiceEvent,
  seedFromServerTurns,
  type VoiceEventLike,
} from "./voice-transcript";

/**
 * 刷新后仍然要能区分"说过"和"没播出去"（契约 §4.2 F02）。
 *
 * 打断留下的余量如果只活在内存里，刷新一次它就变成了用户似乎听过的正文——这正是
 * "把没说过的话当成说过了"。所以余量必须走两条路都能重建：持久事件（`turn.finalized`
 * 的 `unheard_text`）与 `/transcript` 记录（`ServerTurnLike.unheard_text`）。
 */
let counter = 0;
function event(overrides: Partial<VoiceEventLike> & { type: string }): VoiceEventLike {
  counter += 1;
  return { event_id: `ve_${counter}`, event_seq: counter, phase: "authoritative", payload: {}, ...overrides };
}

describe("语音余量：刷新后不得变成已播报正文", () => {
  it("turn.finalized 的 unheard_text 只进 unheardText，不进 text", () => {
    const state = reduceVoiceEvent(
      emptyTranscriptState(),
      event({
        type: "turn.finalized",
        turn_id: "t1",
        payload: { text: "第一句。", heard_text: "第一句。", unheard_text: "第二句没播完。", outcome: "interrupted" },
      }),
    );
    const assistant = orderedEntries(state).filter((entry) => entry.role === "assistant");
    expect(assistant).toHaveLength(1);
    expect(assistant[0].text).toBe("第一句。");
    expect(assistant[0].text).not.toContain("第二句");
    expect(assistant[0].unheardText).toBe("第二句没播完。");
  });

  it("没有余量的正常回合不产生空的 fold", () => {
    const state = reduceVoiceEvent(
      emptyTranscriptState(),
      event({
        type: "turn.finalized",
        turn_id: "t1",
        payload: { text: "完整回答。", unheard_text: "", outcome: "finalized" },
      }),
    );
    expect(orderedEntries(state)[0].unheardText).toBeUndefined();
  });

  it("seedFromServerTurns 从服务端记录恢复余量", () => {
    const seeded = seedFromServerTurns(emptyTranscriptState(), [
      {
        turn_id: "t1",
        user_text: "问题",
        assistant_text: "听到的这一段",
        unheard_text: "没播出去的那一段",
        status: "interrupted",
      },
    ]);
    const assistant = orderedEntries(seeded).filter((entry) => entry.role === "assistant");
    expect(assistant).toHaveLength(1);
    expect(assistant[0].text).toBe("听到的这一段");
    expect(assistant[0].unheardText).toBe("没播出去的那一段");
  });

  it("服务端记录没有余量时保持 undefined（老后端兼容）", () => {
    const seeded = seedFromServerTurns(emptyTranscriptState(), [
      { turn_id: "t1", user_text: "问题", assistant_text: "回答", status: "finalized" },
    ]);
    expect(orderedEntries(seeded)[0].unheardText).toBeUndefined();
  });
});
