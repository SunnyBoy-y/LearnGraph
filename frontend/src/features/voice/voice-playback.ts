/**
 * 语音回答的媒体时钟（F01/F02 的核心）。
 *
 * 屏幕上"已经听到的文字"必须由**浏览器实际出声的时长**推出来，而不是由字速估算或
 * 服务端入队时刻推出：
 *
 *  - 服务端 `voice-sentence-start` 是"这一句的首帧进入输出队列"的时刻，合成远快于
 *    播放，队列里可以堆好几句，所以入队时刻和真正出声的时刻可以差几百毫秒到几秒；
 *  - 字速估算（每个字固定 ms）在标点停顿、语速变化、句间停顿上必然漂移，而且它把
 *    "合成快"当成"播得快"，于是暂停/卡顿期间文字照样往前跑——那正是"字跑在声音前面"
 *    的根。
 *
 * 只有音量检测看到的"正在出声"是事实：出声的时间累加，静音（缓冲、卡顿、句间停顿）
 * 的时间一分不计。所以媒体位置 = 出声时长累加器，句边界来自服务端的音频游标
 * （`startMs`/`endMs`），句内位置是线性估算。
 *
 * 精度声明（必须同步到 UI 文案里）：
 *  - 句边界精度受音量检测周期（70–100ms）限制，约 ±150ms；
 *  - 句内位置为线性估算，不是逐字对齐（契约 §6：不申请、不解析供应商词级时间戳）；
 *  - `segmentReveal` 默认 `allowPartialSentence: false`，即句级精度、诚实显示：没有
 *    结束偏移时宁可显示 0 字，也不假装知道播到了第几个字。真实前端传 `true` 时属于
 *    "句内估算"，必须在 UI 上标注估算精度。
 *
 * 本模块是纯函数集合：不 import React / DOM / 网络，时间一律由调用方传入
 * `performance.now()`，因此可以在测试里精确控制。
 */

export interface VoicePlaybackSegment {
  sentenceSeq: number;
  segmentId: string;
  text: string;
  /** 服务端媒体时间线上的起始偏移（ms），来自 voice-sentence-start。 */
  startMs: number;
  /** 该句结束偏移（ms），来自 voice-sentence-end；未知为 null。 */
  endMs: number | null;
  /** marker 到达时的 performance.now()，仅用于诊断与回落。 */
  queuedAt: number;
}

export interface VoicePlaybackClock {
  /** 当前连续可播放区间的起点（performance.now()）。null 表示未在出声。 */
  anchorPerfMs: number | null;
  /** 之前已累计的出声时长（ms）。 */
  elapsedMs: number;
  /** 是否正在出声。 */
  playing: boolean;
}

export interface VoicePlaybackAnchor {
  turnId: string;
  generationId: number | null;
  segments: VoicePlaybackSegment[];
  clock: VoicePlaybackClock;
  /** 是否至少观察到过一次出声。 */
  observed: boolean;
}

export function emptyPlaybackAnchor(turnId: string, generationId?: number | null): VoicePlaybackAnchor {
  return {
    turnId,
    generationId: generationId ?? null,
    segments: [],
    clock: { anchorPerfMs: null, elapsedMs: 0, playing: false },
    observed: false,
  };
}

/** 句段身份：以 segmentId 为准，老后端没有 segmentId 时退回到句序号。 */
function segmentKey(segment: VoicePlaybackSegment): string {
  return segment.segmentId || `s${segment.sentenceSeq}`;
}

/**
 * 记下一句的开始。句段按 `startMs` 升序排列，因为拼接"已听到"文本必须按音频顺序，
 * 而 RTVI 快通道与持久通道可能乱序到达。
 *
 * 同一句的 marker 可能被两条通道重复投递（快通道 + 持久事件补齐）：重复投递只做合并，
 * 且**不得**把已知的 `endMs` 抹回 null——否则重投一次开始 marker 就会丢掉该句的插值
 * 窗口，句内进度会突然退回 0。
 */
export function noteStartMarker(
  anchor: VoicePlaybackAnchor,
  segment: VoicePlaybackSegment,
): VoicePlaybackAnchor {
  const key = segmentKey(segment);
  const index = anchor.segments.findIndex((item) => segmentKey(item) === key);
  if (index < 0) {
    const segments = [...anchor.segments];
    const at = segments.findIndex((item) => item.startMs > segment.startMs);
    segments.splice(at < 0 ? segments.length : at, 0, segment);
    return { ...anchor, segments };
  }
  const segments = [...anchor.segments];
  segments[index] = {
    ...segments[index],
    ...segment,
    endMs: segment.endMs ?? segments[index].endMs,
  };
  return { ...anchor, segments };
}

/**
 * 记下一句的结束偏移。结束偏移是服务端媒体时间线上的事实，也是句内线性插值的唯一
 * 依据；没有它的句子（还在合成中的最后一句）只能按"未播完"处理。找不到句序号时原样
 * 返回，避免为迟到 marker 凭空造一个句段。
 */
export function noteEndMarker(
  anchor: VoicePlaybackAnchor,
  sentenceSeq: number,
  endMs: number,
): VoicePlaybackAnchor {
  const index = anchor.segments.findIndex((item) => item.sentenceSeq === sentenceSeq);
  if (index < 0) return anchor;
  const segments = [...anchor.segments];
  segments[index] = { ...segments[index], endMs };
  return { ...anchor, segments };
}

/** 音量检测：出声中。 */
export function noteAudible(anchor: VoicePlaybackAnchor, perfNow: number): VoicePlaybackAnchor {
  if (anchor.clock.playing) return anchor;
  return {
    ...anchor,
    observed: true,
    clock: { anchorPerfMs: perfNow, elapsedMs: anchor.clock.elapsedMs, playing: true },
  };
}

/** 音量检测：静音（可能只是句间停顿，也可能是卡顿）。 */
export function noteSilent(anchor: VoicePlaybackAnchor, perfNow: number): VoicePlaybackAnchor {
  if (!anchor.clock.playing || anchor.clock.anchorPerfMs == null) return anchor;
  return {
    ...anchor,
    clock: {
      anchorPerfMs: null,
      elapsedMs: anchor.clock.elapsedMs + Math.max(0, perfNow - anchor.clock.anchorPerfMs),
      playing: false,
    },
  };
}

/** 当前已播报的媒体位置（ms）。 */
export function playedMs(anchor: VoicePlaybackAnchor, perfNow: number): number {
  const { anchorPerfMs, elapsedMs, playing } = anchor.clock;
  if (!playing || anchorPerfMs == null) return Math.max(0, elapsedMs);
  return elapsedMs + Math.max(0, perfNow - anchorPerfMs);
}

/** 单个句段在该播放位置下应显示多少字符。 */
export function segmentReveal(
  segment: VoicePlaybackSegment,
  played: number,
  opts?: { allowPartialSentence?: boolean },
): { chars: number; complete: boolean } {
  const length = segment.text.length;
  if (played <= segment.startMs) return { chars: 0, complete: false };
  const endMs = segment.endMs;
  if (endMs != null && played >= endMs) return { chars: length, complete: true };
  if (endMs != null) {
    // 走到这里必然 startMs < played < endMs，分母为正。
    const chars = Math.floor((length * (played - segment.startMs)) / (endMs - segment.startMs));
    return { chars: Math.min(Math.max(chars, 0), length), complete: false };
  }
  // 没有结束偏移：默认按"未播完"处理，一个字都不显示（句级精度，诚实）。
  if (opts?.allowPartialSentence === true) return { chars: length, complete: false };
  return { chars: 0, complete: false };
}

/**
 * 打断/收尾时冻结：把已听到的前缀与未播完的余量分开。
 *
 * 用默认的 `segmentReveal` 语义（句级）：正在播但没有结束偏移的句子整体算"没听到"，
 * 绝不把后半句补全——录音到此为止，写进记录的文字也必须到此为止。余量单独返回，
 * UI 只能在折叠区展示，不得混进"已播报"文本。
 */
export function freezePlayback(
  anchor: VoicePlaybackAnchor,
  perfNow: number,
): { heard: string; unheard: string; segments: VoicePlaybackSegment[] } {
  const played = playedMs(anchor, perfNow);
  let heard = "";
  let unheard = "";
  for (const segment of anchor.segments) {
    const { chars } = segmentReveal(segment, played);
    heard += segment.text.slice(0, chars);
    unheard += segment.text.slice(chars);
  }
  return { heard, unheard, segments: [...anchor.segments] };
}

/** 拼接已完全播报的句段文本（逐字前缀 + 完整句）。 */
export function heardText(anchor: VoicePlaybackAnchor, perfNow: number): string {
  // 与冻结用同一套口径：可见的"已听到"文本必须等于打断时会固化下来的内容，
  // 否则打断瞬间屏幕上的字会突然变多或变少。
  return freezePlayback(anchor, perfNow).heard;
}
