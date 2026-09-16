/**
 * 事件作用域判定（F04）。
 *
 * 语音事件来自三条互不同步的通道：RTVI 快通道 marker、持久事件推送、HTTP 补拉。
 * 同一条音频流的快通道事件可能比持久事件早到几秒，而重连/换模型之后，旧会话的
 * 迟到事件仍会排在队列里。若不做作用域判定，这些事件会：把上一轮的句子塞进当前
 * 正在生成的回合、在已经收尾的气泡上再开一个气泡、或让一次 stale 事件把当前
 * live 行整行删除（`:1177-1182` 的老写法）。
 *
 * 所以判定必须发生在 reducer 入口：不属于当前 session/epoch/turn/generation 的
 * 事件只能用来补历史，绝不允许改动当前回合。`unattributed`（没有 turn_id）只在
 * "这个远端会话完全没有任何持久事件"（老后端兜底）时才允许渲染，否则宁可丢弃，
 * 也不要把无法归属的文字混进当前回答。
 *
 * 本模块是纯函数：不 import React / DOM / 网络，状态由调用方持有。
 */

/** 一条事件自带的身份。 */
export interface VoiceScope {
  sessionId: string;
  sessionEpoch: number;
  turnId: string | null;
  generationId: number | null;
}

export type ScopeVerdict =
  | "accept" // 属于当前作用域，可以作用于当前回合
  | "stale" // 属于旧的会话/回合/generation：只能补历史，不得改动当前回合
  | "unattributed"; // 没有 turn_id：只有 legacy（无持久事件）才可渲染

export interface ScopeState extends VoiceScope {
  /** 当前允许被写入的回合；null 表示还没有回合。 */
  activeTurnId: string | null;
  /** 完全没有持久事件（老后端兜底）。 */
  legacy: boolean;
}

/**
 * 初始作用域。
 *
 * `legacy` 默认为 false：没有证据时先不信"当前没有持久事件"，否则老后端的兜底
 * 判断会退化成"任何无主事件都能渲染"。需要兜底时由调用方在确认
 * `durableEventCount === 0` 之后显式打开（契约未提供 setter，通过展开赋值即可）。
 */
export function emptyScope(sessionId = "", sessionEpoch = 0): ScopeState {
  return {
    sessionId,
    sessionEpoch,
    turnId: null,
    generationId: null,
    activeTurnId: null,
    legacy: false,
  };
}

/**
 * 会话/代次变化时重置（返回新状态；epoch 变化会清掉 activeTurnId）。
 *
 * 会话变了，旧的 turn/generation 就都不再属于任何"当前回合"：turn_id 会跨会话
 * 复用编号空间，而 generation 是**每个 bot 实例**从 0 开始数的，若把它留着，
 * 新会话的第一个 generation（比如 1）会被判成 < 旧会话的 5 → 整段新回答都被
 * 当成 stale 丢掉。所以重置时一并清掉 turnId / activeTurnId / generationId。
 *
 * `legacy` 例外：它描述的是"这个远端会话有没有持久事件"这一事实，由调用方按
 * 事件流维护；重置发生在会话 id 首次落定（从 "" 变成真 id）的同一刻，若是连
 * legacy 一起抹掉，调用方刚刚确认的兜底事实会被无声地撤销。
 */
export function noteScopeSession(
  state: ScopeState,
  sessionId: string,
  sessionEpoch: number,
): ScopeState {
  if (state.sessionId === sessionId && state.sessionEpoch === sessionEpoch) return state;
  return { ...emptyScope(sessionId, sessionEpoch), legacy: state.legacy };
}

/** 记下当前活动回合（来自 turn.accepted / user.final / bot-llm-started）。 */
export function noteScopeTurn(state: ScopeState, turnId: string | null): ScopeState {
  if (state.turnId === turnId && state.activeTurnId === turnId) return state;
  return { ...state, turnId, activeTurnId: turnId };
}

/**
 * 记下当前 generation（来自任何带 generation_id 的事件）。
 *
 * 只前进、不回退：generation 是后端闸门的单调递增代次，只有真正的新一代才会
 * 更大。一条迟到的旧事件若把水位拉低，之后**所有**同一低代次的迟到片段都会
 * 变得"不小于当前代次"而被放行——一次乱序就等于永久打开了闸门。
 * 不带 generation_id 的事件（老后端）同样不改动水位。
 */
export function noteScopeGeneration(
  state: ScopeState,
  generationId: number | null,
): ScopeState {
  if (generationId == null) return state;
  if (state.generationId != null && generationId <= state.generationId) return state;
  return { ...state, generationId };
}

/** 判断一个事件是否允许作用于当前回合。 */
export function scopeVerdict(state: ScopeState, incoming: VoiceScope): ScopeVerdict {
  if (incoming.sessionId && incoming.sessionId !== state.sessionId) return "stale";
  if (incoming.sessionEpoch !== state.sessionEpoch) return "stale";
  if (
    incoming.generationId != null &&
    state.generationId != null &&
    incoming.generationId < state.generationId
  ) {
    return "stale";
  }
  if (incoming.turnId == null) return state.legacy ? "accept" : "unattributed";
  if (state.activeTurnId == null) return "accept";
  if (incoming.turnId === state.activeTurnId) return "accept";
  return "stale";
}

/** 该事件是否可以把当前回合"打开"（无回合时由它建立回合）。 */
export function scopeOpensTurn(state: ScopeState, incoming: VoiceScope): boolean {
  return state.activeTurnId == null || incoming.turnId === state.activeTurnId;
}
