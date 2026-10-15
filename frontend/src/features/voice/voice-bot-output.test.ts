import { describe, expect, it } from "vitest";

import {
  applyBotOutput,
  captionText,
  keepLitParts,
  lightFirstUnlit,
  markPlaybackStarted,
  revealedParts,
  splitPart,
  type VoiceBotOutputPart,
} from "./voice-bot-output";

/** 造一条官方 `bot-output`（字段名与协议一致，全是 snake_case）。 */
function event(overrides: Partial<Parameters<typeof applyBotOutput>[1]> = {}) {
  return {
    text: "第一句。",
    spokenStatus: "new",
    willBeSpoken: true,
    accumulatedText: "",
    segmentId: 1,
    ...overrides,
  };
}

describe("voice-bot-output · 官方句级语义", () => {
  it("new 先到：整句都在，但一个字都还没读", () => {
    const parts = applyBotOutput([], event());
    expect(parts).toHaveLength(1);
    expect(parts[0].text).toBe("第一句。");
    expect(parts[0].spokenChars).toBe(0);
    expect(parts[0].status).toBe("new");
    expect(splitPart(parts[0], "karaoke")).toEqual({
      spoken: "",
      unspoken: "第一句。",
    });
  });

  it("completed 点亮整句（句级路径的 accumulated_text 就是全文）", () => {
    const lit = applyBotOutput([], event({ spokenStatus: "completed", accumulatedText: "第一句。" }));
    expect(lit[0].spokenChars).toBe(4);
    expect(lit[0].status).toBe("completed");
    expect(splitPart(lit[0], "karaoke")).toEqual({ spoken: "第一句。", unspoken: "" });
  });

  it("completed 没有 accumulated_text 时按整句收尾", () => {
    const lit = applyBotOutput([], event({ spokenStatus: "completed", accumulatedText: "" }));
    expect(lit[0].spokenChars).toBe(4);
    expect(lit[0].status).toBe("completed");
  });

  it("in-progress 只推进游标，不新建段", () => {
    const first = applyBotOutput([], event({ spokenStatus: "new" }));
    const next = applyBotOutput(
      first,
      event({ spokenStatus: "in-progress", accumulatedText: "第一" }),
    );
    expect(next).toHaveLength(1);
    expect(next[0].spokenChars).toBe(2);
    expect(next[0].status).toBe("in-progress");
    expect(splitPart(next[0], "karaoke")).toEqual({ spoken: "第一", unspoken: "句。" });
  });

  it("游标被钳在段文本长度内（服务端做过变换时也不能越界）", () => {
    const announced = applyBotOutput([], event({ spokenStatus: "new" }));
    const parts = applyBotOutput(
      announced,
      event({ spokenStatus: "in-progress", accumulatedText: "第一句。还有多余的后缀" }),
    );
    expect(parts[0].spokenChars).toBe(4);
  });

  it("句级路径下 completed 的 segment_id 与 new 不同 → 回落到第一个未完成段", () => {
    let parts = applyBotOutput([], event({ segmentId: 11 }));
    parts = applyBotOutput(parts, event({ segmentId: 22, text: "第二句。" }));
    parts = applyBotOutput(
      parts,
      event({ segmentId: 999, spokenStatus: "completed", accumulatedText: "第一句。" }),
    );
    expect(parts.map((part) => part.status)).toEqual(["completed", "new"]);
    expect(parts[1].spokenChars).toBe(0);
  });

  it("未朗读的后续句子保持灰字（卡拉OK 的预读）", () => {
    let parts = applyBotOutput([], event({ segmentId: 11 }));
    parts = applyBotOutput(parts, event({ segmentId: 22, text: "第二句。" }));
    expect(parts.map((part) => splitPart(part, "karaoke").unspoken)).toEqual([
      "第一句。",
      "第二句。",
    ]);
  });

  it("重复投递同一条 new 不会让同一句出现两次", () => {
    const first = applyBotOutput([], event({ segmentId: 11 }));
    const again = applyBotOutput(first, event({ segmentId: 11 }));
    expect(again).toBe(first);
    expect(again).toHaveLength(1);
  });

  it("will_be_spoken=false（如代码块）永远算未读", () => {
    const parts = applyBotOutput([], event({ willBeSpoken: false, text: "```code```" }));
    expect(parts[0].willBeSpoken).toBe(false);
    expect(splitPart(parts[0], "karaoke")).toEqual({ spoken: "", unspoken: "```code```" });
    // captions 模式干脆不显示它（官方也是这么处理的）。
    expect(splitPart(parts[0], "captions")).toEqual({ spoken: "", unspoken: "" });
    // instant 模式下它仍然是"未读"那一侧，只是渲染时不给它上灰（与官方 HG 一致）。
    expect(splitPart(parts[0], "instant")).toEqual({ spoken: "", unspoken: "```code```" });
  });

  it("没有可推进的段时：`new` 丢了但 completed 到了，就补一段已点亮的", () => {
    const recovered = applyBotOutput(
      [],
      event({ spokenStatus: "completed", accumulatedText: "其实说过的一句。", segmentId: 5 }),
    );
    expect(recovered).toHaveLength(1);
    expect(recovered[0].status).toBe("completed");
    expect(recovered[0].spokenChars).toBe(recovered[0].text.length);
    // 反过来，`in-progress` 没有"已经播完"的保证：宁可不显示，也不凭它造一句。
    expect(
      applyBotOutput([], event({ spokenStatus: "in-progress", accumulatedText: "未证实" })),
    ).toEqual([]);
  });

  it("收尾只保留已经点亮的段（打断后灰字消失）", () => {
    let parts = applyBotOutput([], event({ segmentId: 11, spokenStatus: "completed", accumulatedText: "第一句。" }));
    parts = applyBotOutput(parts, event({ segmentId: 22, text: "第二句。" }));
    const lit = keepLitParts(parts);
    expect(lit.map((part) => part.text)).toEqual(["第一句。"]);
    expect(captionText(parts)).toBe("第一句。第二句。");
  });
});

describe("voice-bot-output · 点亮只认因果信号", () => {
  it("lightFirstUnlit 按文本命中那一段（同文句子也不会点错）", () => {
    let parts = applyBotOutput([], event({ segmentId: 11, text: "嗯。" }));
    parts = applyBotOutput(parts, event({ segmentId: 22, text: "嗯。" }));
    const lit = lightFirstUnlit(parts, "嗯。");
    expect(lit[0].status).toBe("completed");
    // 第二段应当仍然未读 —— 一次事件只点亮一段。
    expect(lit[1].status).toBe("new");
  });

  it("没有匹配文本时点亮最靠前的一段；全都点亮后原样返回", () => {
    let parts = applyBotOutput([], event({ segmentId: 11, text: "第一句。" }));
    parts = applyBotOutput(parts, event({ segmentId: 22, text: "第二句。" }));
    const once = lightFirstUnlit(parts);
    expect(once.map((part) => part.status)).toEqual(["completed", "new"]);
    const twice = lightFirstUnlit(once, "第二句。");
    expect(twice.map((part) => part.status)).toEqual(["completed", "completed"]);
    expect(lightFirstUnlit(twice)).toBe(twice);
  });
});

describe("voice-bot-output · 三种模式的切分", () => {  const part: VoiceBotOutputPart = {
    segmentId: 1,
    text: "已读未读",
    spokenChars: 2,
    status: "in-progress",
    willBeSpoken: true,
  };

  it("karaoke：已读正常色、未读灰", () => {
    expect(splitPart(part, "karaoke")).toEqual({ spoken: "已读", unspoken: "未读" });
  });

  it("captions：只显示已读", () => {
    expect(splitPart(part, "captions")).toEqual({ spoken: "已读", unspoken: "" });
  });

  it("instant：全文不分色", () => {
    expect(splitPart(part, "instant")).toEqual({ spoken: "已读未读", unspoken: "" });
  });
});

describe("voice-bot-output · 上屏裁剪（说一句显一句）", () => {
  /** 官方通道一次送来三句（预读），一句都还没点亮。 */
  function threeSentences() {
    let parts = applyBotOutput([], event({ segmentId: 11, text: "第一句。" }));
    parts = applyBotOutput(parts, event({ segmentId: 22, text: "第二句。" }));
    return applyBotOutput(parts, event({ segmentId: 33, text: "第三句。" }));
  }

  it("只放已点亮的 + 已经开始播放的当前句，预读句先不上屏", () => {
    let parts = threeSentences();
    // 三句都只是预读：音频还没开始，屏幕上不提前显示。
    expect(revealedParts(parts)).toHaveLength(0);
    parts = markPlaybackStarted(parts, "第一句。");
    expect(revealedParts(parts).map((part) => part.text)).toEqual(["第一句。"]);
    parts = lightFirstUnlit(parts, "第一句。");
    expect(revealedParts(parts).map((part) => part.text)).toEqual(["第一句。"]);
    parts = markPlaybackStarted(parts, "第二句。");
    expect(revealedParts(parts).map((part) => part.text)).toEqual(["第一句。", "第二句。"]);
    parts = lightFirstUnlit(parts, "第二句。");
    expect(revealedParts(parts).map((part) => part.text)).toEqual(["第一句。", "第二句。"]);
    parts = markPlaybackStarted(parts, "第三句。");
    expect(revealedParts(parts).map((part) => part.text)).toEqual(["第一句。", "第二句。", "第三句。"]);
    parts = lightFirstUnlit(parts, "第三句。");
    expect(revealedParts(parts)).toHaveLength(3);
  });

  it("上屏的段落里最多只有一句是灰的", () => {
    let parts = threeSentences();
    expect(revealedParts(parts).filter((part) => part.status !== "completed")).toHaveLength(0);
    parts = markPlaybackStarted(parts, "第一句。");
    parts = lightFirstUnlit(parts, "第一句。");
    parts = markPlaybackStarted(parts, "第二句。");
    expect(revealedParts(parts).filter((part) => part.status !== "completed")).toHaveLength(1);
  });

  it("不上屏的段落原样留在表里（点亮要用它）", () => {
    const parts = threeSentences();
    expect(parts).toHaveLength(3);
    expect(revealedParts(parts)).toHaveLength(0);
  });

  it("代码块（will_be_spoken=false）到点即显示，不占预告位", () => {
    let parts = applyBotOutput(
      [],
      event({ segmentId: 11, text: "```code```", willBeSpoken: false }),
    );
    parts = applyBotOutput(parts, event({ segmentId: 22, text: "第一句。" }));
    // Non-spoken code is immediate; the spoken sentence still waits for its
    // causal audio-start marker.
    expect(revealedParts(parts).map((part) => part.text)).toEqual([
      "```code```",
    ]);
    parts = markPlaybackStarted(parts, "第一句。");
    expect(revealedParts(parts).map((part) => part.text)).toEqual(["```code```", "第一句。"]);
  });
});

describe("voice-bot-output · 同一句被两条通道各宣布一次", () => {
  it("账本兜底先开的段，官方 `new` 再送一次不会开第二段", () => {
    const ledger: VoiceBotOutputPart[] = [
      {
        segmentId: null,
        text: "第一句。",
        spokenChars: 0,
        status: "in-progress",
        willBeSpoken: true,
        source: "ledger",
      },
    ];
    expect(applyBotOutput(ledger, event({ segmentId: 11, text: "第一句。" }))).toBe(ledger);
  });

  it("重复的同文句子（前一句还没点亮）仍然各开一段", () => {
    let parts = applyBotOutput([], event({ segmentId: 11, text: "嗯。" }));
    parts = applyBotOutput(parts, event({ segmentId: 22, text: "嗯。" }));
    expect(parts.map((part) => part.text)).toEqual(["嗯。", "嗯。"]);
  });

  it("前一句已点亮时，同文的下一句照样开新段", () => {
    let parts = applyBotOutput([], event({ segmentId: 11, text: "嗯。" }));
    parts = lightFirstUnlit(parts, "嗯。");
    parts = applyBotOutput(parts, event({ segmentId: 22, text: "嗯。" }));
    expect(parts.map((part) => part.status)).toEqual(["completed", "new"]);
  });
});
