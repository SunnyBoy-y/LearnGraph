/**
 * 助手回合的完成判定与气泡归属（F03）。
 *
 * 全双工语音里"声音停了"不等于"导师说完了"：用户插话、TTS 缓冲抖动、句间停顿
 * 都会让远端音频出现静音段。历史缺陷正是把 RMS 静音当完成信号——句间停顿被误判
 * 为回合结束，下一句没有可以复用的活动回合，只能开新气泡，于是同一个回答在转录里
 * 被拆成好几块，句间距越自然越明显。
 *
 * 所以这里把"完成"锚定在服务端事实链上：LLM 输出结束 + 输出队列排空。函数签名里
 * 没有任何能量/音量参数，从类型上封死"用静音判完成"的写法；`turnTimeoutExceeded`
 * 是唯一的例外，且它必须显式给出"已经过了多久"，是一条可解释的恢复策略而不是猜测。
 * 气泡归属同理：同一个 turn_id 的后续 marker 复用同一回合，只有 turn_id 变化或
 * 上一回合确实已完成才开新回合。
 *
 * 纯模块：不 import React / DOM / 网络，可直接单测。
 */

export interface VoiceTurnProgress {
  /** LLM 已结束输出（bot-llm-stopped）。 */
  llmClosed: boolean;
  /** TTS 已结束合成（最后一个 audio context 已收尾）。 */
  ttsEnded: boolean;
  /** 输出队列已排空（playback_ended / BotStoppedSpeaking）。 */
  outputDrained: boolean;
  /** 浏览器确实播完（可选，来自播放确认）。 */
  playbackEnded: boolean;
  /** 已播报的字符数（仅诊断/展示，不参与完成判定）。 */
  spokenChars: number;
}

/** llmClosed 之后的兜底预算：超过它仍未排空就按 timeout 收尾，避免回合永久悬挂。 */
const TURN_TIMEOUT_BUDGET_MS = 8000;

export function emptyTurnProgress(): VoiceTurnProgress {
  return {
    llmClosed: false,
    ttsEnded: false,
    outputDrained: false,
    playbackEnded: false,
    spokenChars: 0,
  };
}

/**
 * 回合是否可以结束。
 *
 * 当且仅当 `llmClosed && outputDrained`：`ttsEnded` 只说明合成收尾，音频可能还在
 * 队列里播；`playbackEnded` 是浏览器侧的体验事实，缺了它也不能把还没排空的队列
 * 当成说完了。RMS/静音永远不是完成信号，因此本函数不接受任何能量参数。
 */
export function turnMayComplete(progress: VoiceTurnProgress): boolean {
  return progress.llmClosed && progress.outputDrained;
}

/** 回合结束的原因；只有这四种。 */
export type VoiceTurnEndReason = "tts-drained" | "interrupted" | "timeout" | "session-end";

/**
 * 超时兜底：llmClosed 后经过 budgetMs 仍未排空，按 timeout 收尾。
 *
 * 计时只在 LLM 关闭之后才有意义——此前的时间属于"生成中"，不是故障；已经可以正常
 * 收尾时也永远不超时，否则兜底会在回合结束之后再把状态翻回 timeout。
 * `sinceLlmClosedMs` 不是有限数（例如时钟缺失）时判为未超时：宁可多等，也不能凭
 * 一个坏数字把回合提前砍掉。
 */
export function turnTimeoutExceeded(
  progress: VoiceTurnProgress,
  sinceLlmClosedMs: number,
  budgetMs = TURN_TIMEOUT_BUDGET_MS,
): boolean {
  if (turnMayComplete(progress)) return false;
  if (!progress.llmClosed) return false;
  return sinceLlmClosedMs >= budgetMs;
}

/**
 * 决定一条助手句段应该落到哪个回合气泡。
 *
 * 这是 F03 的修复点：句间停顿（同一 turn_id 的后续 marker）必须复用同一个气泡，
 * 只有 turn_id 变化或上一回合已完成时才开新回合。判定顺序按契约给定：
 * 上一回合未完成且进来的 marker 没有新 turn_id（为空或同一个 turn_id）就复用；
 * 一旦 marker 带了不同的 turn_id，就用服务端那个 id 建新回合——这样新气泡的身份
 * 与持久事件一致，后续 finalize 能直接对上，不必再靠本地记忆去猜。
 */
export function beginTurnFor(
  previous: { turnId: string; progress: VoiceTurnProgress } | null,
  incomingTurnId: string | null,
  newTurnKey: () => string,
): { turnId: string; beginsNewTurn: boolean } {
  if (!previous) return { turnId: newTurnKey(), beginsNewTurn: true };
  if (
    !turnMayComplete(previous.progress) &&
    (incomingTurnId == null || incomingTurnId === previous.turnId)
  ) {
    return { turnId: previous.turnId, beginsNewTurn: false };
  }
  if (incomingTurnId != null && incomingTurnId !== previous.turnId) {
    return { turnId: incomingTurnId, beginsNewTurn: true };
  }
  return { turnId: newTurnKey(), beginsNewTurn: true };
}

/**
 * 文本诚实度：有句段结束偏移且观察到播放 → `"sentence"`；否则也降级为 `"sentence"`
 * （绝不返回"逐字可信"的承诺）；只有缺少结束偏移时才标记 `"estimated"`，供 UI 提示
 * 用户"这是估算"。
 *
 * 句边界的唯一事实来源是服务端 journal 的结束偏移，而音量观察只能证明"确实出过声"，
 * 无法把句级精度提升到逐字，所以 `hasPlaybackObservation` 不参与结果判定：没有结束
 * 偏移时，即便听到过声音也只能给 `"estimated"`。
 */
export function revealPrecisionFor(opts: {
  hasEndOffsets: boolean;
  hasPlaybackObservation: boolean;
}): "sentence" | "estimated" {
  return opts.hasEndOffsets ? "sentence" : "estimated";
}
