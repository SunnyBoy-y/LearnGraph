/**
 * 语音能力可用性的正交分解（F07 / F09）。
 *
 * 为什么要把「能不能说 / 能不能听 / 能不能答 / 能不能播」拆成四个互不推导的布尔量：
 * 以前整通电话只用一个 `textFallback` 标志表示降级，而它只由 ASR 降级推导
 * （`requiresTextFallback`），渲染处又写成 `textFallback && degradedNotice`。于是
 * TTS 一旦降级，回答不再发声、界面却一个字都不说——用户只能自己发现导师"哑了"。
 * 四种能力本来是独立的：播放被浏览器拦下不影响语音识别与模型生成，TTS 坏也不影响
 * 用户继续说话。拆开之后每种降级各自产生提示、各自给出用户可执行的动作，
 * 同时 `input`/`model` 还负责回答"输入区该不该放开给文字"。
 *
 * 本模块是纯函数：不 import React / DOM / 网络，也不 import
 * `voice-session-controller`，因此可以逐条对应契约规则做单元测试，且不受
 * 组件渲染路径影响。
 */

export type VoiceCapability = "asr" | "tts" | "llm" | "network";

export interface VoiceAvailability {
  /** 用户说话能否被理解（false ⇒ 必须开放文字输入）。 */
  input: boolean;
  /** 导师的声音能否被听到。 */
  output: boolean;
  /** 导师能否给出回答。 */
  model: boolean;
  /** audio.play() 是否被浏览器允许。 */
  playback: boolean;
}

export interface VoiceNotice {
  id: "asr" | "tts" | "llm" | "network" | "playback";
  text: string;
  /** 用户可执行的动作；没有则为 null。 */
  action: "type" | "retry" | "resume-audio" | null;
}

/**
 * 提示的固定顺序，按契约 id 联合类型的声明顺序冻结。
 *
 * 为什么不按"发现顺序"或 Map 遍历顺序输出：提示由事件序列驱动，同一组降级在
 * 不同到达顺序下必须渲染成同一个样子，否则用户会看到提示来回跳动。
 */
const NOTICE_ORDER: readonly VoiceNotice["id"][] = ["asr", "tts", "llm", "network", "playback"];

/**
 * 每种降级说什么、能做什么。
 *
 * TTS 的文案刻意点明"回答会以文字显示"，而不是只说"语音播报不可用"：用户需要知道
 * 接着去哪里看回答。TTS 与 ASR 的 action 都是 `type`——一个要求改用文字提问，
 * 一个要求去文字区看回答。
 */
const NOTICE_TEXT: Record<VoiceNotice["id"], string> = {
  asr: "语音识别已不可用，请改用文字输入；本次通话的记录与任务都会保留。",
  tts: "语音播报已不可用，回答会以文字显示；你仍然可以继续说话。",
  llm: "导师暂时无法给出回答，请稍后重试；你的问题与记录都会保留。",
  network: "语音处理暂时不可用，请重试；本次通话的记录与任务都会保留。",
  playback: "浏览器阻止了声音播放，请点击「恢复声音」后继续。",
};

const NOTICE_ACTION: Record<VoiceNotice["id"], VoiceNotice["action"]> = {
  asr: "type",
  tts: "type",
  llm: "retry",
  network: "retry",
  playback: "resume-audio",
};

/**
 * 由降级阶段推导四项能力。
 *
 * `output` 同时依赖 tts 与 playbackBlocked，因为"听不到导师"这两个成因对用户是
 * 同一件事；但 `playback` 只反映浏览器是否允许播放，`tts` 降级并不代表播放能力
 * 坏了——两者混在一起会让"恢复声音"按钮在 TTS 坏掉时错误地出现。
 */
export function deriveAvailability(
  degradedStages: readonly string[],
  opts?: { playbackBlocked?: boolean },
): VoiceAvailability {
  const playbackBlocked = opts?.playbackBlocked === true;
  return {
    input: !degradedStages.includes("asr"),
    output: !degradedStages.includes("tts") && !playbackBlocked,
    model: !degradedStages.includes("llm"),
    playback: !playbackBlocked,
  };
}

/**
 * 输入区是否必须允许文字输入。
 *
 * 三条都必须放开：连接本身失败时语音模式已经无法承载提问；识别坏了用户说不清；
 * 模型坏了说了也白说（且必须留出重试的入口）。除这几种情况之外语音模式仍然独占
 * 输入区，否则会同时出现"按住说话"与文字输入两套入口。
 */
export function composerAllowed(availability: VoiceAvailability, transport: string): boolean {
  if (transport === "error") return true;
  if (!availability.input) return true;
  if (!availability.model) return true;
  return false;
}

/**
 * 逐个能力的提示（TTS 降级以前被隐藏，必须出来）。
 *
 * tts 提示以 `degradedStages` 而不是 `!availability.output` 为判据：`output` 为
 * false 也可能只是播放被浏览器拦下，用 `!output` 会把两件事压成一条，用户就再也
 * 看不到"回答不会出声"这个事实。播放与 TTS 同时坏时两条提示都要给。
 */
export function voiceNotices(
  availability: VoiceAvailability,
  degradedStages: readonly string[],
): VoiceNotice[] {
  const degraded: Record<VoiceNotice["id"], boolean> = {
    asr: !availability.input,
    tts: degradedStages.includes("tts"),
    llm: !availability.model,
    network: degradedStages.includes("network"),
    playback: !availability.playback,
  };
  const notices: VoiceNotice[] = [];
  for (const id of NOTICE_ORDER) {
    if (degraded[id]) notices.push({ id, text: NOTICE_TEXT[id], action: NOTICE_ACTION[id] });
  }
  return notices;
}

/**
 * 连接状态那一句（与 `voice-status.ts` 的 `voiceStatusText` 同一套文案）。
 *
 * 为什么在这里复写而不是 import：`voice-status.ts` 依赖 `voice-session-controller`
 * 的类型，而这个模块必须保持可独立测试、可被任何宿主调用；两处文案一旦要改，
 * 应该一起改（它们是同一条状态栏的两种用法）。
 */
function connectionSummary(transport: string, state: string, error?: string | null): string {
  if (transport === "connecting") return "正在接通语音导师…";
  if (transport === "reconnecting") return "语音连接中断，正在重连…";
  if (transport === "error") return error || "语音连接失败";
  if (state === "speaking") return "导师正在回答，你随时可以插话";
  if (state === "thinking") return "导师正在思考，你随时可以插话";
  if (state === "listening") return "正在聆听，请直接说话";
  if (transport === "connected") return "语音导师已连接，请直接说话";
  return "语音导师未连接";
}

/**
 * 降级摘要，接在连接状态后面。
 *
 * 连接文案说"正在聆听"，但识别已经坏了——只播一句会把用户送进对着麦克风空说的
 * 状态，所以降级事实必须与连接事实合并在同一句里，不能各自占一行让用户二选一。
 */
function degradationSummary(availability: VoiceAvailability): string {
  const parts: string[] = [];
  if (!availability.input) parts.push("语音识别不可用，请改用文字输入");
  if (!availability.model) parts.push("导师暂时无法回答");
  if (!availability.output) parts.push("回答不会发声，将以文字显示");
  return parts.length > 0 ? `（${parts.join("；")}）` : "";
}

/** 状态栏那一句短文案（与语音连接状态合并）。 */
export function availabilitySummary(
  availability: VoiceAvailability,
  transport: string,
  state: string,
  error?: string | null,
): string {
  return `${connectionSummary(transport, state, error)}${degradationSummary(availability)}`;
}
