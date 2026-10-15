import { describe, expect, it } from "vitest";

import {
  emptyPlaybackAnchor,
  freezePlayback,
  heardText,
  noteAudible,
  noteEndMarker,
  noteSilent,
  noteStartMarker,
  playedMs,
  segmentReveal,
} from "./voice-playback";
import type { VoicePlaybackAnchor, VoicePlaybackSegment } from "./voice-playback";

/**
 * 契约 §3.3：媒体时钟由"浏览器实际出声的时长累加器"驱动，句边界来自服务端音频游标，
 * 句内线性估算。这里的每条断言都在拒绝一类具体缺陷：字跑在声音前面、静音期间文字继续
 * 前进、打断时把没听到的后半句补进记录。
 */

function segment(
  partial: Partial<VoicePlaybackSegment> & Pick<VoicePlaybackSegment, "sentenceSeq" | "text">,
): VoicePlaybackSegment {
  return {
    segmentId: `turn-1:s${partial.sentenceSeq}`,
    startMs: 0,
    endMs: null,
    queuedAt: 0,
    ...partial,
  };
}

/** 出声 [from, to) 之后停住的一个锚点。 */
function anchorAfterAudible(turnId: string, from: number, to: number): VoicePlaybackAnchor {
  return noteSilent(noteAudible(emptyPlaybackAnchor(turnId), from), to);
}

describe("emptyPlaybackAnchor", () => {
  it("未出声、未观察、无句段", () => {
    const anchor = emptyPlaybackAnchor("turn-1");
    expect(anchor.turnId).toBe("turn-1");
    expect(anchor.generationId).toBeNull();
    expect(anchor.segments).toEqual([]);
    expect(anchor.clock).toEqual({ anchorPerfMs: null, elapsedMs: 0, playing: false });
    expect(anchor.observed).toBe(false);
    expect(playedMs(anchor, 10_000)).toBe(0);
  });

  it("保留传入的 generationId，且每次调用都是独立状态", () => {
    const first = emptyPlaybackAnchor("turn-1", 3);
    expect(first.generationId).toBe(3);
    expect(emptyPlaybackAnchor("turn-1", 0).generationId).toBe(0);
    expect(emptyPlaybackAnchor("turn-1", null).generationId).toBeNull();

    noteAudible(first, 1000);
    expect(emptyPlaybackAnchor("turn-1").clock.playing).toBe(false);
  });
});

describe("媒体时钟 = 出声时长累加器", () => {
  it("首次出声开始累计，静音结算后时间不再前进", () => {
    let anchor = emptyPlaybackAnchor("turn-1");
    anchor = noteAudible(anchor, 1000);
    expect(anchor.observed).toBe(true);
    expect(anchor.clock.playing).toBe(true);

    anchor = noteSilent(anchor, 1500);
    expect(anchor.clock.playing).toBe(false);
    expect(anchor.clock.anchorPerfMs).toBeNull();
    // 静音期间（缓冲/卡顿/句间停顿）无论过多久都不计入媒体位置。
    expect(playedMs(anchor, 1500)).toBe(500);
    expect(playedMs(anchor, 60_000)).toBe(500);
  });

  it("重复的出声心跳不重开区间，避免重复计时", () => {
    let anchor = emptyPlaybackAnchor("turn-1");
    anchor = noteAudible(anchor, 1000);
    anchor = noteAudible(anchor, 1200);
    anchor = noteAudible(anchor, 1400);
    anchor = noteSilent(anchor, 1500);
    expect(playedMs(anchor, 1500)).toBe(500);
  });

  it("多段出声累加，出声中的区间也算进当前位置", () => {
    let anchor = anchorAfterAudible("turn-1", 1000, 1500);
    anchor = noteAudible(anchor, 5000);
    expect(playedMs(anchor, 5600)).toBe(1100);
    anchor = noteSilent(anchor, 6000);
    expect(playedMs(anchor, 6000)).toBe(1500);
  });

  it("从未出声时静音不得凭空计时，observed 保持 false", () => {
    const anchor = noteSilent(emptyPlaybackAnchor("turn-1"), 9999);
    expect(anchor.observed).toBe(false);
    expect(anchor.clock.playing).toBe(false);
    expect(playedMs(anchor, 9999)).toBe(0);
  });

  it("marker 到达不推进媒体时钟（既不是入队时刻，也不是字速估算）", () => {
    let anchor = emptyPlaybackAnchor("turn-1");
    anchor = noteStartMarker(anchor, segment({ sentenceSeq: 1, text: "甲乙丙丁", startMs: 0, endMs: 400 }));
    anchor = noteEndMarker(anchor, 1, 400);
    anchor = noteStartMarker(anchor, segment({ sentenceSeq: 2, text: "戊己庚辛", startMs: 400, endMs: 800 }));

    // 浏览器一声没出：即使最后一句的结束偏移已经知道，也只能是 0 已听到。
    expect(playedMs(anchor, 5000)).toBe(0);
    expect(heardText(anchor, 5000)).toBe("");
    expect(anchor.observed).toBe(false);
  });
});

describe("noteStartMarker", () => {
  it("按 startMs 升序排列，乱序到达也不打乱音频顺序", () => {
    let anchor = emptyPlaybackAnchor("turn-1");
    anchor = noteStartMarker(anchor, segment({ sentenceSeq: 2, text: "第二句", startMs: 500 }));
    anchor = noteStartMarker(anchor, segment({ sentenceSeq: 1, text: "第一句", startMs: 0 }));
    anchor = noteStartMarker(anchor, segment({ sentenceSeq: 3, text: "第三句", startMs: 900 }));

    expect(anchor.segments.map((item) => item.sentenceSeq)).toEqual([1, 2, 3]);
  });

  it("同一句被两条通道重复投递时合并，而不是重复计入文本", () => {
    let anchor = emptyPlaybackAnchor("turn-1");
    const first = segment({ sentenceSeq: 1, text: "重复句。", startMs: 0, endMs: 1000 });
    anchor = noteStartMarker(anchor, first);
    anchor = noteStartMarker(anchor, { ...first });

    expect(anchor.segments).toHaveLength(1);
    anchor = noteSilent(noteAudible(anchor, 0), 1000);
    expect(heardText(anchor, 1000)).toBe("重复句。");
  });

  it("重投开始 marker 不得抹掉已到的结束偏移", () => {
    let anchor = emptyPlaybackAnchor("turn-1");
    anchor = noteStartMarker(anchor, segment({ sentenceSeq: 1, text: "一句十个字", startMs: 0 }));
    anchor = noteEndMarker(anchor, 1, 1000);
    anchor = noteStartMarker(anchor, segment({ sentenceSeq: 1, text: "一句十个字", startMs: 0 }));

    anchor = noteSilent(noteAudible(anchor, 0), 500);
    // 结束偏移还在，所以句内能插值出前缀；丢了它就只能整句算未听到。
    expect(segmentReveal(anchor.segments[0], playedMs(anchor, 500))).toEqual({
      chars: 2,
      complete: false,
    });
  });
});

describe("noteEndMarker", () => {
  it("只写入命中句序号的句段", () => {
    let anchor = emptyPlaybackAnchor("turn-1");
    anchor = noteStartMarker(anchor, segment({ sentenceSeq: 1, text: "甲", startMs: 0 }));
    anchor = noteStartMarker(anchor, segment({ sentenceSeq: 2, text: "乙", startMs: 500 }));
    anchor = noteEndMarker(anchor, 2, 900);

    expect(anchor.segments.map((item) => item.endMs)).toEqual([null, 900]);
  });

  it("未知句序号原样返回，不为迟到 marker 凭空造句段", () => {
    const anchor = emptyPlaybackAnchor("turn-1");
    expect(noteEndMarker(anchor, 7, 900)).toBe(anchor);
    expect(noteEndMarker(anchor, 7, 900).segments).toEqual([]);
  });
});

describe("segmentReveal", () => {
  const sentence = segment({ sentenceSeq: 1, text: "0123456789", startMs: 100, endMs: 1100 });

  it("还没开始（含恰好等于 startMs）一律 0 字", () => {
    expect(segmentReveal(sentence, 0)).toEqual({ chars: 0, complete: false });
    expect(segmentReveal(sentence, 99)).toEqual({ chars: 0, complete: false });
    expect(segmentReveal(sentence, 100)).toEqual({ chars: 0, complete: false });
  });

  it("到达结束偏移即整句完成", () => {
    expect(segmentReveal(sentence, 1100)).toEqual({ chars: 10, complete: true });
    expect(segmentReveal(sentence, 99_999)).toEqual({ chars: 10, complete: true });
  });

  it("结束偏移前最后一毫秒只有前缀，绝不提前完成", () => {
    expect(segmentReveal(sentence, 1099)).toEqual({ chars: 9, complete: false });
  });

  it("窗口内按比例线性插值并向下取整", () => {
    expect(segmentReveal(sentence, 600)).toEqual({ chars: 5, complete: false });
    expect(segmentReveal(sentence, 110)).toEqual({ chars: 0, complete: false });
    expect(segmentReveal(sentence, 1090)).toEqual({ chars: 9, complete: false });
  });

  it("空文本永远 0 字但可以完成", () => {
    const empty = segment({ sentenceSeq: 2, text: "", startMs: 0, endMs: 10 });
    expect(segmentReveal(empty, 5)).toEqual({ chars: 0, complete: false });
    expect(segmentReveal(empty, 10)).toEqual({ chars: 0, complete: true });
  });

  it("没有结束偏移时默认不显示（句级精度、诚实）", () => {
    const unknown = segment({ sentenceSeq: 3, text: "0123456789", startMs: 100, endMs: null });
    // played 已经远远越过 startMs，但仍然不能假装知道播到了第几个字。
    expect(segmentReveal(unknown, 5000)).toEqual({ chars: 0, complete: false });
    expect(segmentReveal(unknown, 5000, { allowPartialSentence: false })).toEqual({
      chars: 0,
      complete: false,
    });
  });

  it("显式允许句内估算时「已开始则整句显示」，仍未开始则 0 字", () => {
    const unknown = segment({ sentenceSeq: 3, text: "0123456789", startMs: 100, endMs: null });
    expect(segmentReveal(unknown, 5000, { allowPartialSentence: true })).toEqual({
      chars: 10,
      complete: false,
    });
    expect(segmentReveal(unknown, 100, { allowPartialSentence: true })).toEqual({
      chars: 0,
      complete: false,
    });
    expect(segmentReveal(unknown, 100, { allowPartialSentence: true }).complete).toBe(false);
  });
});

describe("freezePlayback / heardText", () => {
  /** 三句：第一句播完，第二句播到一半，第三句连结束偏移都还没有。 */
  function partialTurn(): VoicePlaybackAnchor {
    let anchor = anchorAfterAudible("turn-1", 0, 2000);
    anchor = noteStartMarker(anchor, segment({ sentenceSeq: 1, text: "先看定义。", startMs: 0, endMs: 1000 }));
    anchor = noteStartMarker(anchor, segment({ sentenceSeq: 2, text: "再看例子。", startMs: 1000, endMs: 3000 }));
    anchor = noteStartMarker(anchor, segment({ sentenceSeq: 3, text: "最后总结。", startMs: 3000, endMs: null }));
    return anchor;
  }

  it("已听到 = 完整句 + 正在播那句的前缀，余量单独返回", () => {
    const frozen = freezePlayback(partialTurn(), 2000);
    expect(frozen.heard).toBe("先看定义。再看");
    expect(frozen.unheard).toBe("例子。最后总结。");
  });

  it("打断时不补齐后半句：没有结束偏移的句子整体进未听到", () => {
    const anchor = partialTurn();
    // 出声累到 5000ms，已越过第三句起点（3000ms），但它没有结束偏移，
    // 不能因此把整句算成已听到，更不能把没播的后半句补进记录。
    const later = noteSilent(noteAudible(anchor, 5000), 8000);
    const frozen = freezePlayback(later, 8000);

    expect(frozen.heard).toBe("先看定义。再看例子。");
    expect(frozen.heard.endsWith("最后总结。")).toBe(false);
    expect(frozen.unheard).toBe("最后总结。");
  });

  it("冻结之后无论传多晚的时间都不再变化（静音不计入）", () => {
    const anchor = partialTurn();
    expect(freezePlayback(anchor, 2000).heard).toBe(freezePlayback(anchor, 90_000).heard);
    expect(playedMs(anchor, 90_000)).toBe(playedMs(anchor, 2000));
  });

  it("句段快照按音频顺序返回，且是独立数组", () => {
    const anchor = partialTurn();
    const frozen = freezePlayback(anchor, 2000);
    expect(frozen.segments.map((item) => item.sentenceSeq)).toEqual([1, 2, 3]);
    expect(frozen.segments).not.toBe(anchor.segments);
    expect(frozen.segments).toEqual(anchor.segments);
  });

  it("全句播完时 heard 为全文、unheard 为空；且与 heardText 完全一致", () => {
    const anchor = noteEndMarker(partialTurn(), 3, 5000);
    const done = noteSilent(noteAudible(anchor, 5000), 8000);
    const frozen = freezePlayback(done, 8000);

    expect(frozen.heard).toBe("先看定义。再看例子。最后总结。");
    expect(frozen.unheard).toBe("");
    expect(heardText(done, 8000)).toBe(frozen.heard);
    expect(heardText(anchor, 2000)).toBe(freezePlayback(anchor, 2000).heard);
  });

  it("只有 marker、没有出声时听到的是空字符串（旧行为「marker 即整句已播报」被拒绝）", () => {
    let anchor = emptyPlaybackAnchor("turn-1");
    anchor = noteStartMarker(anchor, segment({ sentenceSeq: 1, text: "整句文字。", startMs: 0, endMs: 800 }));
    anchor = noteEndMarker(anchor, 1, 800);

    const frozen = freezePlayback(anchor, 100);
    expect(frozen.heard).toBe("");
    expect(frozen.unheard).toBe("整句文字。");
  });
});
