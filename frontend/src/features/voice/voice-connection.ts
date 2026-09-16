/**
 * 语音链路的"已连接"到底是什么（F06/F09）。
 *
 * 审计里的两个复现都来自同一个错误：把"某一步 API 返回了"当成"链路可用了"。
 *
 * - 复现 A：`setRemoteDescription` 返回后控制器立刻把 transport 标成 connected，而
 *   那一刻 PeerConnection 还是 `new`、数据通道还是 `connecting`。answer 落地只说明
 *   协商走完一半：ICE 还没通、导师的管线更没起来。用户看着"已连接"说话，声音哪儿都
 *   没去。
 * - 复现 B：短暂 `disconnected` 之后 ICE 恢复成功、`connected` 回调再次触发，旧回调
 *   只看"是不是 broken"就直接 return，UI 永远停在"正在重连…"。恢复成功是一条必须
 *   显式收尾的转移，否则一次网络抖动就让整场对话卡在重连态。
 *
 * 因此这里把 signaling / transport / data channel / pipeline / playback 拆成互不覆盖
 * 的布尔量：`linkIsCallable` 只由真实事实推导——PeerConnection 真的 connected，并且
 * 数据通道或导师管线至少有一个可用；任何单步（尤其是 setRemoteDescription）的返回值
 * 都不能把它变成 true。掉线按"先 ICE restart、后重新协商"升级，并且从第一次掉线起
 * 共用一份恢复预算（默认 20s），超时后由调用方落到 `noteRecoverFailed` 的 failed 相位
 * 并放开文字输入。
 *
 * 纯模块：不 import React / DOM / 网络。DOM 类型名只出现在契约冻结的参数类型位置，
 * 运行时不触碰任何浏览器 API；`perfNow` 一律由调用方注入，模块自身不读时钟。
 */

export type VoiceLinkPhase =
  | "idle"
  | "signaling"
  | "media"
  | "pipeline"
  | "live"
  | "recovering"
  | "blocked-audio"
  | "failed";

export interface VoiceLinkState {
  phase: VoiceLinkPhase;
  signaling: boolean;
  /** RTCPeerConnection.connectionState === "connected"，只能由 notePeerState 置位。 */
  transport: boolean;
  /** dataChannel.readyState === "open" */
  dataChannel: boolean;
  /** session.ready / runtime_ready */
  pipeline: boolean;
  /** 远端音频确实在播。 */
  playback: boolean;
  reconnectAttempt: number;
  /** performance.now() 开始恢复的时刻；未恢复为 null。 */
  recoverStartedAt: number | null;
  iceRestartUsed: boolean;
  lastError: string | null;
}

export type LinkAction = "none" | "ice-restart" | "reconnect" | "recovered" | "give-up";

/** 一次掉线从开始恢复到放弃的总预算：超过它就降级文字，不能让用户无限等。 */
const RECOVER_BUDGET_MS = 20_000;

/** 可随事实正常前进的相位；其余相位（recovering / blocked-audio / failed）只能由各自的转移离开。 */
const FORWARD_PHASES: readonly VoiceLinkPhase[] = [
  "idle",
  "signaling",
  "media",
  "pipeline",
  "live",
];

function forwardPhase(phase: VoiceLinkPhase): boolean {
  return FORWARD_PHASES.includes(phase);
}

/**
 * 传输已连通时的相位：管线就绪才算 live，否则停在 pipeline（"通道已建立，正在准备
 * 导师"）。live 的承诺必须包含"导师能回答"，光有 ICE 不足以兑现。
 */
function phaseForConnected(state: VoiceLinkState): VoiceLinkPhase {
  return state.pipeline ? "live" : "pipeline";
}

export function emptyLink(): VoiceLinkState {
  return {
    phase: "idle",
    signaling: false,
    transport: false,
    dataChannel: false,
    pipeline: false,
    playback: false,
    reconnectAttempt: 0,
    recoverStartedAt: null,
    iceRestartUsed: false,
    lastError: null,
  };
}

/**
 * signaling 完成：answer 已落地，PeerConnection 现在存在。
 *
 * F06-A 的复现点就在这里——只能记下"协商完成"，相位最多前进到 media（"正在建立语音
 * 通道…"），**绝不能**把 transport 标成 connected。setRemoteDescription 返回 ≠ ICE
 * 连通；真正的可通话状态由 notePeerState / noteDataChannel / notePipelineReady 各自的
 * 事实决定。也正因为如此，恢复中的重协商 answer 不会把 recovering 相位顶掉。
 */
export function noteSignalingAnswer(state: VoiceLinkState): VoiceLinkState {
  if (!forwardPhase(state.phase)) return { ...state, signaling: true };
  const advanced = state.phase === "idle" || state.phase === "signaling";
  return { ...state, signaling: true, phase: advanced ? "media" : state.phase };
}

/**
 * PeerConnection / ICE 状态变化。返回新的链路状态与调用方该执行的动作。
 *
 * 判定顺序：failed 优先于 disconnected（两个字段可能同时报不同状态，失败更严重）；
 * 第一次掉线只做代价最小的 ICE restart——它保留 PeerConnection、数据通道和持久游标，
 * 只重建网络路径，是网络抖动该有的反应；已经用过 restartIce 仍掉线，说明重启救不回来，
 * 必须重新协商。`recoverStartedAt` 用 `??` 保留第一次掉线的时刻：预算跨重试累计，否则
 * 每次重试都重置计时，超时永远不会到期。
 */
export function notePeerState(
  state: VoiceLinkState,
  pcState: RTCPeerConnectionState | string,
  iceState: RTCIceConnectionState | string,
  perfNow: number,
): { state: VoiceLinkState; action: LinkAction } {
  const failed = pcState === "failed" || iceState === "failed";
  const dropped = pcState === "disconnected" || iceState === "disconnected";
  const connected =
    pcState === "connected" || iceState === "connected" || iceState === "completed";

  if (failed) {
    // ICE failed 意味着这条链路上的 restartIce 没能救回来；继续重启只会把失败拖长。
    // 重新协商（新建 PeerConnection）是唯一还能改变结局的动作。
    return {
      state: {
        ...state,
        phase: "recovering",
        transport: false,
        reconnectAttempt: state.reconnectAttempt + 1,
        recoverStartedAt: state.recoverStartedAt ?? perfNow,
      },
      action: "reconnect",
    };
  }

  if (dropped) {
    if (!state.iceRestartUsed) {
      return {
        state: {
          ...state,
          phase: "recovering",
          transport: false,
          iceRestartUsed: true,
          recoverStartedAt: state.recoverStartedAt ?? perfNow,
        },
        action: "ice-restart",
      };
    }
    return {
      state: {
        ...state,
        phase: "recovering",
        transport: false,
        reconnectAttempt: state.reconnectAttempt + 1,
        recoverStartedAt: state.recoverStartedAt ?? perfNow,
      },
      action: "reconnect",
    };
  }

  if (connected) {
    // F06-B：恢复成功必须显式收尾。相位交还给事实（管线就绪 live，否则 pipeline），
    // 并清掉恢复计时——否则一次网络抖动会把 UI 永久留在"正在重连…"。
    const wasRecovering = state.phase === "recovering" || state.phase === "failed";
    const next: VoiceLinkState = { ...state, transport: true, recoverStartedAt: null };
    if (wasRecovering) {
      return { state: { ...next, phase: phaseForConnected(next) }, action: "recovered" };
    }
    if (!forwardPhase(next.phase)) return { state: next, action: "none" };
    return { state: { ...next, phase: phaseForConnected(next) }, action: "none" };
  }

  // new / connecting / checking / closed：既不是失败也不是连通，保持现状，免得把
  // "还在协商"渲染成别的状态。dataChannel / pipeline 由各自的观察者更新，这里不动。
  return { state, action: "none" };
}

/**
 * 数据通道（RTVI 的唯一通道）是否 open。
 *
 * 打开时把相位推进到 pipeline（"语音通道已建立，正在准备导师"），但前提是 transport
 * 已连通：通道先于 ICE 建好不代表链路可用，这正是 F06-A 要拒绝的那种提前乐观。
 */
export function noteDataChannel(state: VoiceLinkState, open: boolean): VoiceLinkState {
  const next = { ...state, dataChannel: open };
  if (!open || !next.transport || !forwardPhase(next.phase)) return next;
  return { ...next, phase: phaseForConnected(next) };
}

/**
 * 导师管线（session.ready / runtime_ready）是否就绪：只有它才把相位推到 live。
 * 管线掉线时从 live 退回 pipeline——"通道在、导师不在"要说出来，而不是继续显示
 * "请直接说话"。
 */
export function notePipelineReady(state: VoiceLinkState, ready: boolean): VoiceLinkState {
  const next = { ...state, pipeline: ready };
  if (!next.transport || !forwardPhase(next.phase)) return next;
  return { ...next, phase: phaseForConnected(next) };
}

/**
 * 远端音频是否真的在出声。
 *
 * `playing === false` 只在链路已经连通、且不在恢复中/已失败时才算"浏览器阻止了声音"：
 * 正在接通或正在重连时没有声音是正常的，不该弹"点击恢复声音"。出声后从 blocked-audio
 * 回到事实相位，这就是"恢复声音"之后 UI 的复位路径。
 */
export function notePlayback(state: VoiceLinkState, playing: boolean): VoiceLinkState {
  if (playing) {
    const next: VoiceLinkState = { ...state, playback: true };
    if (next.phase !== "blocked-audio") return next;
    return { ...next, phase: next.transport ? phaseForConnected(next) : "media" };
  }
  const next: VoiceLinkState = { ...state, playback: false };
  if (!next.transport || next.phase === "recovering" || next.phase === "failed") return next;
  return { ...next, phase: "blocked-audio" };
}

/**
 * 恢复以失败告终（重试预算耗尽或重连抛错）：降级为文字模式。
 *
 * 进入这里就说明链路已经没有可用的传输事实，因此把 transport / dataChannel /
 * pipeline / playback 一起清掉——`linkIsCallable` 必须为 false，输入区据此解除封锁
 * （契约 F07 的"transport error 必须开放文字输入"）。恢复计时也清空，否则已降级的
 * 状态会一直被判成"恢复超时"。
 */
export function noteRecoverFailed(state: VoiceLinkState, error: string): VoiceLinkState {
  return {
    ...state,
    phase: "failed",
    transport: false,
    dataChannel: false,
    pipeline: false,
    playback: false,
    recoverStartedAt: null,
    lastError: error,
  };
}

/** 真正可以通话：transport 必须是真的 connected，且有可用数据通道或管线就绪。 */
export function linkIsCallable(state: VoiceLinkState): boolean {
  return state.transport && (state.dataChannel || state.pipeline);
}

const LINK_LABELS: Record<VoiceLinkPhase, string> = {
  idle: "语音导师未连接",
  signaling: "正在接通语音导师…",
  media: "正在建立语音通道…",
  pipeline: "语音通道已建立，正在准备导师…",
  live: "语音导师已连接，请直接说话",
  recovering: "语音连接中断，正在重连…",
  "blocked-audio": "浏览器阻止了声音播放，请点击恢复声音",
  failed: "语音连接无法恢复，已降级为文字模式",
};

/** 状态栏文案（冻结文案，逐相位一一对应）。 */
export function linkLabel(state: VoiceLinkState): string {
  return LINK_LABELS[state.phase];
}

/**
 * 恢复预算是否耗尽（默认 20s）。
 *
 * 计时从第一次进入恢复的那一刻算起，跨多次 ice-restart / reconnect 累计：每次重试都
 * 重置计时会让"预算"永远不到期，正是契约要避免的"单次状态变化后缺少必达的恢复超时"。
 * 未处于恢复中（`recoverStartedAt === null`）时永不超时。
 */
export function recoverBudgetExceeded(
  state: VoiceLinkState,
  perfNow: number,
  budgetMs = RECOVER_BUDGET_MS,
): boolean {
  if (state.recoverStartedAt === null) return false;
  return perfNow - state.recoverStartedAt >= budgetMs;
}
