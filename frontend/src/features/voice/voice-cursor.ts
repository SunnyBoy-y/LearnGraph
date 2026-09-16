/**
 * 持久事件游标（F05）。
 *
 * 实时推送和 HTTP 补拉是两条独立的通道：推送会丢、会乱序、会重连后整段重放，
 * 补拉则可能夹杂 RTVI 之外的多种事件。以前前端只用一个 `lastEventSeq`（观察到的
 * 最大值）当水位，于是只要看见 seq=3，就再也不会去拉 seq=2 —— 那个被跳过的序号
 * 永久丢失，用户看到的是"导师少说了半句"。
 *
 * 修复的要点是把水位拆成两件事：
 *  - `confirmed`：服务端保证"连续无缺口"覆盖到的序号，补拉只能从它之后开始；
 *  - `observed`：观察到的最大序号（含缺口），只用于诊断与界面提示。
 *
 * 但"连续"不能只靠本地推断：`replay_events(..., types=...)` 会按类型过滤，返回
 * 数组的最大值不代表前面没有空洞，所以服务端另给 `contiguous_through`（会话级
 * 水位），由 `confirmThrough` 采纳。游标本身绑定远端会话与 epoch，换会话必须重置。
 *
 * 本模块是纯函数：不 import React / DOM / 网络，状态由调用方持有。
 */

export interface CursorState {
  /** 已确认"连续无缺口"覆盖到的序号；轮询只能从它之后开始。 */
  confirmed: number;
  /** 观察到的最大序号（含缺口）。 */
  observed: number;
  /** 远端会话绑定：换会话必须重置。 */
  sessionId: string;
  sessionEpoch: number;
  /** 已知缺口（升序去重）。 */
  gaps: number[];
}

/**
 * 缺口表上限。
 *
 * 缺口列表只用于诊断与"立刻补拉"的提示，真正的补拉始终从 `confirmed` 开始，
 * 所以丢掉的缺口不会造成数据丢失。超限时保留**序号最小**的那些：它们离
 * `confirmed` 最近，正是下一次补拉最先能确认到的部分，丢掉它们才会让水位停在
 * 原地；而远端序号更大的缺口会被下一次补拉的结果（及其水位）一并覆盖。
 */
export const MAX_TRACKED_GAPS: number = 64;

export function emptyCursor(sessionId = "", sessionEpoch = 0): CursorState {
  return { confirmed: 0, observed: 0, sessionId, sessionEpoch, gaps: [] };
}

function sortGaps(seqs: readonly number[]): number[] {
  return [...new Set(seqs)].sort((left, right) => left - right);
}

/** 从 `confirmed` 起吸收连续可接上的缺口，返回新水位与剩余缺口。 */
function absorb(confirmed: number, gaps: readonly number[]): {
  confirmed: number;
  gaps: number[];
} {
  const sorted = sortGaps(gaps.filter((seq) => seq > confirmed));
  let next = confirmed;
  let index = 0;
  while (index < sorted.length && sorted[index] === next + 1) {
    next += 1;
    index += 1;
  }
  return { confirmed: next, gaps: sorted.slice(index) };
}

/**
 * 观察到一个实时事件的序号。返回新状态与是否需要立刻补拉。
 *
 * 会话/代次不同时整段重建：旧会话的缺口数字在新会话里没有意义（序号空间已换），
 * 而 `fetchAfter` 回到 0 会让下一次补拉从新会话的开头整段取回，所以这里是"重建"
 * 而不是"缺口"，`gap` 仍为 false；换会话的调用方本来就要重新拉一次。
 */
export function observeSeq(
  state: CursorState,
  seq: number,
  sessionId?: string,
  sessionEpoch?: number,
): { state: CursorState; gap: boolean } {
  if (sessionId !== undefined && sessionId !== state.sessionId) {
    return { state: emptyCursor(sessionId, sessionEpoch ?? state.sessionEpoch), gap: false };
  }
  if (sessionEpoch !== undefined && sessionEpoch !== state.sessionEpoch) {
    return { state: emptyCursor(state.sessionId, sessionEpoch), gap: false };
  }
  // 非有限数只可能来自坏载荷；放进去会污染 gaps 与水位（NaN 的比较永远为 false）。
  if (!Number.isFinite(seq)) return { state, gap: false };

  const observed = Math.max(state.observed, seq);
  if (seq <= state.confirmed) {
    return {
      state: observed === state.observed ? state : { ...state, observed },
      gap: false,
    };
  }
  if (seq === state.confirmed + 1) {
    const next = absorb(seq, state.gaps);
    return { state: { ...state, observed, confirmed: next.confirmed, gaps: next.gaps }, gap: false };
  }
  const gaps = sortGaps([...state.gaps, seq]);
  return {
    state: {
      ...state,
      observed,
      gaps: gaps.length > MAX_TRACKED_GAPS ? gaps.slice(0, MAX_TRACKED_GAPS) : gaps,
    },
    gap: true,
  };
}

/**
 * 服务端返回水位（contiguous_through）：<= watermark 的序号保证已投递。
 *
 * 水位同时抬升 `observed`：既然该序号以内的都已投递，观测到的最大值就不该比它小。
 */
export function confirmThrough(state: CursorState, watermark: number): CursorState {
  if (!Number.isFinite(watermark)) return state;
  const observed = Math.max(state.observed, watermark);
  const confirmed = Math.max(state.confirmed, watermark);
  const next = absorb(confirmed, state.gaps);
  // absorb 只会移除缺口，所以长度相等即内容相等。
  if (
    next.confirmed === state.confirmed &&
    observed === state.observed &&
    next.gaps.length === state.gaps.length
  ) {
    return state;
  }
  return { ...state, observed, confirmed: next.confirmed, gaps: next.gaps };
}

/**
 * 下一次 HTTP 补偿请求应该使用的 after_event_seq。
 *
 * 必须是 `confirmed` 而不是 `observed`：用观察到的最大值会让被跳过的序号永远
 * 补不回来（以前 seq=2 丢失的根因）。
 */
export function fetchAfter(state: CursorState): number {
  return state.confirmed;
}

/** 缺口列表（去重升序，长度上限 MAX_TRACKED_GAPS）。 */
export function pendingGaps(state: CursorState): number[] {
  return [...state.gaps];
}
