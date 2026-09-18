/**
 * 全双工语音「各环节耗时」的纯模型。
 *
 * 面板要回答的问题只有一个：**用户说完到导师出声，这几百毫秒花在哪了**。
 * 口径照抄参考实现 `demo-voice2o2/frontend/index.html` 的时序图（第 6 节）：
 * 以「用户说完」为 0 点，按发生顺序给每个环节打一个时间点，行内同时给出
 * 「累计 ms」与「Δ 每段 ms」，顶部给总量；历史轮次以 chip 形式留在面板里。
 *
 * 时钟：**只用本机时钟**（`performance.now()`，见 `voice-latency-store.ts`）。
 * RTVI 报文（`bot-output` / `bot-llm-*` / `user-stopped-speaking`）不带任何时间戳
 * （`handleRtviMessage` 的信封只有 label/type/data），账本事件虽然带服务端
 * `created_at`，但它与本机时钟不同源，混用会让差值失真。所以这里记录的是
 * 「前端收到该信号的时刻」，每个环节行尾标注它来自哪条通道。
 *
 * ## 归轮的三条不变量
 *
 * ① **只有用户侧的人声信号能开轮**（语音 = 起音，打字 = 发送）。任何旁路信号
 *    （远端音频能量、TTS/账本输出侧事件）在没有开着的轮次时都不许开轮 —— 否则
 *    "服务端写完音频"与"浏览器播完最后一段缓冲"之间的几十~几百毫秒空档里，音频
 *    能量就会凭空开出一轮"导师出声 0 ms / 整屏等待"的幻影轮。
 *
 * ② **每个记号按它自己的回合身份归位**（`turnId` = 服务端 `voice_events.turn_id`）。
 *    归轮不再靠"当前轮像不像它该关的那一轮"去猜：
 *    - 账本事件带身份，收尾（`turn.interrupted` / `sentence.playback_ended{turn_final}`）
 *      精确关它自己那一轮；新的一轮起音已经把它收进历史时，这条收尾会被**补记**到
 *      历史行上（所以"被打断在第几毫秒"不会丢，也不会误关新一轮）。
 *    - RTVI 报文没有身份字段（pipecat 的模型里不带 turn_id），只能归到"当前活跃轮"；
 *      它们中间唯一带收尾语义的是音频能量兜底那条，仍保留一条代理判据：本轮还没有任何
 *      输出侧环节时，不认这条收尾（它描述的一定是上一轮）。
 *
 * ③ **身份比信号后到时不丢，按身份寄存**（`pending`）：带身份、但还没有回合认领它的
 *    记号先存着，那一轮一认领身份就按时间顺序补记（`drainPending`）；只有当身份始终
 *    没出现、寄存超期时才计入 `dropped`。这条是被真机数据逼出来的：打字回合的身份
 *    （`user.final` / `turn.accepted`）由控制面写库、只能靠轮询补投，而它的输出侧信号
 *    （LLM/句级账本）走数据通道**即时**到达 —— "丢弃身份对不上的记号"等于把这一轮的
 *    出声时刻整批扔掉（真机回放：4 条打字回合全部"未测得"、永远"进行中"）。
 */

/** 一轮对话里被计时的环节，按正常发生顺序排列。 */
export type VoiceLatencyStage =
  | "userStarted"
  | "userDone"
  | "asrFinal"
  | "turnAccepted"
  | "llmStart"
  | "llmFirst"
  | "sentenceQueued"
  | "botSpeaking"
  | "sentenceEnded"
  | "answerDone";

/** 该环节的时间点是从哪条通道拿到的（面板行尾展示，缺信号时一眼可见）。 */
export type VoiceLatencySource =
  | "rtvi"
  | "ledger"
  | "audio"
  | "barge-in"
  | "typed";

export interface VoiceLatencyStageSpec {
  stage: VoiceLatencyStage;
  icon: string;
  label: string;
  /** 这一段差值代表什么（照 demo 的「seg」列）。 */
  hint: string;
}

export const VOICE_LATENCY_STAGES: readonly VoiceLatencyStageSpec[] = [
  { stage: "userStarted", icon: "🎙️", label: "用户开口", hint: "起音" },
  { stage: "userDone", icon: "🎤", label: "用户说完", hint: "回合判定（EOU）" },
  { stage: "asrFinal", icon: "📝", label: "识别落定", hint: "语音识别" },
  { stage: "turnAccepted", icon: "✅", label: "回合受理", hint: "服务端调度" },
  { stage: "llmStart", icon: "🧠", label: "模型开始", hint: "进入模型" },
  { stage: "llmFirst", icon: "⚡", label: "首字返回", hint: "模型首字 TTFB" },
  { stage: "sentenceQueued", icon: "🧩", label: "首句就绪", hint: "合成排队" },
  { stage: "botSpeaking", icon: "🔊", label: "导师出声", hint: "合成 + 传输" },
  { stage: "sentenceEnded", icon: "📖", label: "首句播完", hint: "首句音频时长" },
  { stage: "answerDone", icon: "🏁", label: "回答结束", hint: "整轮收尾" },
];

export const VOICE_LATENCY_SOURCE_LABELS: Record<VoiceLatencySource, string> = {
  rtvi: "实时通道",
  ledger: "账本事件",
  audio: "音频能量",
  "barge-in": "打断（barge-in）",
  typed: "打字回合",
};

/** 面板顶部那个数字的标题：用户要的就是它。 */
export const VOICE_LATENCY_TOTAL_LABEL = "语音结束 → 语音开始";

export type VoiceLatencyTurnKind = "voice" | "text";

export interface VoiceLatencyTurn {
  id: number;
  /**
   * 服务端回合身份（`voice_events.turn_id`）。
   *
   * 起音时还不知道：账本的 `user.started` 带的是**上一轮**的 turn_id（journal 在开新轮
   * 之前就发出了它），所以身份要等 `user.final` / `turn.accepted` 到达时认领。
   * 打字回合同理（`turn.accepted` 才带身份）。RTVI 报文永远没有身份。
   */
  turnId: string | null;
  kind: VoiceLatencyTurnKind;
  /** 0 点：`userStarted`（说话时长可读）；没有起音信号时退到 `userDone` 或首个标记。 */
  startedAt: number;
  marks: Partial<Record<VoiceLatencyStage, number>>;
  sources: Partial<Record<VoiceLatencyStage, VoiceLatencySource>>;
  /** 这一轮用户说的话（来自权威的 `turn.accepted` 文本），仅用于对照。 */
  transcript: string;
  /** 已经播出来的句子（`assistant.sentence.ended` 逐句累加），仅用于对照。 */
  reply: string;
  done: boolean;
}

export interface VoiceLatencyState {
  current: VoiceLatencyTurn | null;
  history: VoiceLatencyTurn[];
  /**
   * 身份已经报到、但还没有回合认领它的记号（key = 服务端 turn_id）。
   *
   * 线上真实存在的两种迟到：① 打字回合的 `user.final` / `turn.accepted` 由**控制面**
   * （API 进程）写库，不经数据通道下发，只能靠 1.5s 轮询拿到 —— 而它的 LLM/TTS 事件
   * 是 worker 进程**即时**下发的，于是"自己的身份还没到、自己的输出已经到齐"；
   * ② 断线重连后补齐的账本事件同理。这里按身份寄存，等那一轮认领身份时**补记**，
   * 而不是把带身份的信号当成"无主信号"扔掉（扔掉就等于这一轮永远测不出出声时刻）。
   */
  pending: Record<string, VoiceLatencyMark[]>;
  /**
   * 被丢弃的"无主信号"条数：没有开着的轮次、也没有回合身份的记号，加上寄存超期
   * 仍没人认领的记号。
   *
   * 面板把它显示出来，是为了自证：这些记号**没有**被悄悄算进任何一轮。
   */
  dropped: number;
  nextId: number;
}

/** 面板只留最近这么多轮（照 demo 的 12）。 */
export const VOICE_LATENCY_HISTORY_LIMIT = 12;

/**
 * 寄存多久还没人认领就算丢弃。取值要盖住"轮询周期 + 一次请求往返"（1.5s × 2），
 * 又不能让一个早已作废的记号一直等下去 —— 超过这个跨度，它描述的那一轮要么已经
 * 收尾进了历史（那时按身份早就补记上了），要么本身就丢了。
 */
export const VOICE_LATENCY_HOLD_MS = 30_000;

/** 同时寄存的身份数上限（超出丢掉最旧的那个身份）。 */
export const VOICE_LATENCY_PENDING_TURN_LIMIT = 8;

/** 寄存记号总数上限（每个身份按到达顺序保留最近的若干条）。 */
export const VOICE_LATENCY_PENDING_MARK_LIMIT = 96;

/** 寄存中的记号条数（面板据此说明"有信号在等它的回合"）。 */
export function voiceLatencyHeldCount(state: VoiceLatencyState): number {
  let total = 0;
  for (const marks of Object.values(state.pending)) total += marks.length;
  return total;
}

/**
 * 输出侧环节：只有它们出现过，才说明这一轮已经进入"导师在回答"。
 *
 * 只用于**没有回合身份**的收尾信号（音频能量兜底）：它描述的是"某一轮已经在回答时被
 * 打断或播完"，所以当当前轮一个输出侧环节都还没有时，这条收尾一定属于上一轮。带身份的
 * 账本收尾不需要这条判据（身份已经精确）。
 */
const VOICE_LATENCY_OUTPUT_STAGES: readonly VoiceLatencyStage[] = [
  "llmStart",
  "llmFirst",
  "sentenceQueued",
  "botSpeaking",
  "sentenceEnded",
];

/** 用户侧信号：它们代表"用户说了话"，因此允许开一轮（旁路信号不许）。 */
const VOICE_LATENCY_USER_SIDE_STAGES: readonly VoiceLatencyStage[] = [
  "userStarted",
  "userDone",
  "asrFinal",
  "turnAccepted",
];

function hasOutputStage(turn: VoiceLatencyTurn): boolean {
  return VOICE_LATENCY_OUTPUT_STAGES.some((stage) => turn.marks[stage] !== undefined);
}

export function emptyVoiceLatencyState(): VoiceLatencyState {
  return { current: null, history: [], pending: {}, dropped: 0, nextId: 1 };
}

export interface VoiceLatencyMark {
  stage: VoiceLatencyStage;
  /** 本机时钟（`voiceLatencyNow()`）。 */
  at: number;
  source: VoiceLatencySource;
  /** 该环节的文本（ASR/受理文本、已播句文本），只用于面板里的「内容」对照。 */
  detail?: string;
  /** 只在需要开口轮次时生效：打字回合是 `text`。 */
  kind?: VoiceLatencyTurnKind;
  /** 账本事件自带的回合身份；RTVI 报文没有这个字段（留空 = 归到当前活跃轮）。 */
  turnId?: string | null;
}

function cloneTurn(turn: VoiceLatencyTurn): VoiceLatencyTurn {
  return { ...turn, marks: { ...turn.marks }, sources: { ...turn.sources } };
}

function startTurn(
  state: VoiceLatencyState,
  kind: VoiceLatencyTurnKind,
  at: number,
  turnId: string | null,
): VoiceLatencyState {
  const turn: VoiceLatencyTurn = {
    id: state.nextId,
    turnId,
    kind,
    startedAt: at,
    marks: {},
    sources: {},
    transcript: "",
    reply: "",
    done: false,
  };
  return { ...state, current: turn, nextId: state.nextId + 1 };
}

function closeTurn(state: VoiceLatencyState): VoiceLatencyState {
  const turn = state.current;
  if (!turn) return state;
  const closed: VoiceLatencyTurn = { ...cloneTurn(turn), done: true };
  return {
    ...state,
    current: null,
    history: [...state.history, closed].slice(-VOICE_LATENCY_HISTORY_LIMIT),
  };
}

function dropMark(state: VoiceLatencyState): VoiceLatencyState {
  return { ...state, dropped: state.dropped + 1 };
}

/**
 * 一条带身份的记号，身份对不上任何一轮时的去处：**寄存**，不是丢弃。
 *
 * 之所以不能丢：身份（`turn_id`）是服务端给的，它的到达顺序与"这条信号属于哪一轮"
 * 无关。打字回合就是活例子 —— 它的 `user.final` / `turn.accepted` 由控制面写库、
 * 只能靠 1.5s 轮询拿到，而 LLM/句级账本是 worker 即时下发的：丢弃等于把这一轮
 * 的"出声时刻"整批扔掉（真机事件流回放：一条打字回合只剩 userDone + 两条 RTVI 行，
 * 总量永远"未测得"）。寄存在身份下则只等那一刻：那一轮一认领身份，立刻补记。
 *
 * 有界：单条身份按到达顺序保留最近若干条，身份数也有上限，超出丢最旧的（计入 dropped）。
 */
function holdMark(
  state: VoiceLatencyState,
  turnId: string,
  mark: VoiceLatencyMark,
): VoiceLatencyState {
  const held = [...(state.pending[turnId] ?? []), mark].slice(
    -VOICE_LATENCY_PENDING_MARK_LIMIT,
  );
  const pending: Record<string, VoiceLatencyMark[]> = { ...state.pending, [turnId]: held };
  let dropped = state.dropped;
  // 身份数上限：`Object.keys` 的顺序是插入顺序，第一个就是最旧的身份。
  while (Object.keys(pending).length > VOICE_LATENCY_PENDING_TURN_LIMIT) {
    const oldest = Object.keys(pending)[0];
    dropped += pending[oldest].length; // 这个身份一直没出现，寄存的记号也算丢弃
    delete pending[oldest];
  }
  return { ...state, pending, dropped };
}

/** 寄存超期（或寄到一半被新的时限扫过）的记号：算丢弃，从寄存里清掉。 */
function prunePending(state: VoiceLatencyState, now: number): VoiceLatencyState {
  let dropped = state.dropped;
  let changed = false;
  const pending: Record<string, VoiceLatencyMark[]> = {};
  for (const [turnId, marks] of Object.entries(state.pending)) {
    const kept = marks.filter((mark) => now - mark.at <= VOICE_LATENCY_HOLD_MS);
    dropped += marks.length - kept.length;
    if (kept.length !== marks.length) changed = true;
    if (kept.length > 0) pending[turnId] = kept;
  }
  return changed || Object.keys(pending).length !== Object.keys(state.pending).length
    ? { ...state, pending, dropped }
    : state;
}

/**
 * 某一轮认领了身份（或身份已知）：把寄存在这个身份下的记号按时间顺序**补记**进去。
 *
 * 用 `applyMark` / `patchClosedTurn` 走同一条归位路径，所以寄存的收尾记号一样能
 * 把那一轮关掉（哪怕它在寄存期间已经因为新一轮起音被收进了历史）。
 */
function drainPending(state: VoiceLatencyState, turnId: string): VoiceLatencyState {
  const held = state.pending[turnId];
  if (!held || held.length === 0) return state;
  const pending = { ...state.pending };
  delete pending[turnId];
  let next: VoiceLatencyState = { ...state, pending };
  for (const mark of [...held].sort((a, b) => a.at - b.at)) {
    if (next.current?.turnId === turnId) {
      next = applyMark(next, mark, true);
      continue;
    }
    next = patchClosedTurn(next, turnId, mark) ?? next;
  }
  return next;
}

/** 把一条记号写进**某一轮**（每环节只记第一次；内容对照与时间点解耦）。 */
function markTurn(turn: VoiceLatencyTurn, mark: VoiceLatencyMark): VoiceLatencyTurn {
  const detail = (mark.detail ?? "").trim();
  let next = turn;
  // 内容对照（与时间点解耦：同一环节可能被多条消息重复触发，只累加没见过的文本）。
  // `user.final` 的 `payload.text` 已经是**合并后的整轮文本**（服务端保证），所以这里是
  // 覆盖而不是追加 —— 追加会把前面每一段重复一遍。
  if (mark.stage === "asrFinal" && detail) next = { ...next, transcript: detail };
  if (mark.stage === "sentenceEnded" && detail && !next.reply.includes(detail)) {
    next = { ...next, reply: next.reply ? `${next.reply}${detail}` : detail };
  }
  if (next.marks[mark.stage] !== undefined) return next;
  return {
    ...next,
    marks: { ...next.marks, [mark.stage]: mark.at },
    sources: { ...next.sources, [mark.stage]: mark.source },
  };
}

/**
 * 收尾信号能不能落进**没有身份**的当前轮。
 *
 * 带身份的收尾由调用方保证归属，不需要这条判据；没有身份（音频能量兜底）时，
 * 本轮还没有任何输出侧环节就说明这条收尾描述的不是这一轮。
 */
function acceptsUnidentifiedAnswerDone(turn: VoiceLatencyTurn): boolean {
  return hasOutputStage(turn);
}

function applyMark(
  state: VoiceLatencyState,
  mark: VoiceLatencyMark,
  identified: boolean,
): VoiceLatencyState {
  const turn = state.current;
  if (!turn) return state;
  if (mark.stage === "answerDone" && !identified && !acceptsUnidentifiedAnswerDone(turn)) {
    // 这条收尾描述的不是这一轮（本轮还没有任何输出侧环节）：丢弃并计数，别去关错轮。
    return dropMark(state);
  }
  const next = markTurn(turn, mark);
  if (next === turn) return state; // 该环节已经记过（两条通道各报一次时只留第一次）
  const opened: VoiceLatencyState = { ...state, current: next };
  return mark.stage === "answerDone" ? closeTurn(opened) : opened;
}

/**
 * 身份对应的轮已经收尾（在历史里）：把迟到的记号**补记**上去，而不是去关当前轮。
 *
 * 返回 `null` = 历史里没有这一轮（调用方按"身份未知"继续处理）；返回同一个 state
 * 引用 = 找到了但该环节已经记过（幂等，不该算成"无主信号"）。
 */
function patchClosedTurn(
  state: VoiceLatencyState,
  turnId: string,
  mark: VoiceLatencyMark,
): VoiceLatencyState | null {
  const index = state.history.findIndex((turn) => turn.turnId === turnId);
  if (index < 0) return null;
  const turn = state.history[index];
  const next = markTurn(turn, mark);
  if (next === turn) return state;
  const history = [...state.history];
  history[index] = next;
  return { ...state, history };
}

/**
 * 一条记号该落进哪一轮：当前轮身份相符 → 落进去；历史里身份相符 → 补记。
 *
 * 返回 `null` = 面板里还没有这个身份的回合（调用方决定是寄存还是开局）；
 * 返回同一个 state 引用 = 找到了但该环节已经记过（幂等）。
 */
function applyToKnownTurn(
  state: VoiceLatencyState,
  turnId: string,
  mark: VoiceLatencyMark,
): VoiceLatencyState | null {
  if (state.current?.turnId === turnId) return applyMark(state, mark, true);
  return patchClosedTurn(state, turnId, mark);
}

/**
 * 记录一个环节时间点。
 *
 * 归轮规则（刻意写成显式分支，面板读数依赖它）：
 * - `userStarted`：起音**永远**是新一轮的开始，旧轮若还开着就收尾。它不能按身份归位：
 *   账本这条带的是上一轮的 turn_id（journal 先发事件、后开新轮），照身份归位会把这次
 *   开口吞掉。身份等 `user.final` / `turn.accepted` 来认领。
 * - 打字回合：以发送时刻开一轮（不带身份），身份同样等账本认领。
 * - 带身份的记号：当前轮身份相符就落进去；历史里身份相符就补记；身份还没到（面板里
 *   还没有这一轮）就**按身份寄存**（`pending`），等那一轮认领时补记 —— 丢弃是错的：
 *   见 `holdMark` 的说明。只有用户侧信号能在身份不明时另开一轮（起音丢了）。
 * - 没有身份的记号（RTVI / 音频能量）：归当前活跃轮；没有活跃轮时只有「说完」能开局，
 *   其它一律丢弃 —— 这就是"凭空开轮"的闸门。
 */
export function reduceVoiceLatency(
  state: VoiceLatencyState,
  mark: VoiceLatencyMark,
): VoiceLatencyState {
  if (!Number.isFinite(mark.at)) return state;
  const base = prunePending(state, mark.at);
  const turnId = (mark.turnId ?? "").trim();

  if (mark.stage === "userStarted" && mark.kind !== "text") {
    const previous = base.current ? closeTurn(base) : base;
    return applyMark(startTurn(previous, "voice", mark.at, null), mark, false);
  }

  if (mark.kind === "text" && mark.stage === "userDone") {
    const previous = base.current ? closeTurn(base) : base;
    const opened = startTurn(previous, "text", mark.at, turnId || null);
    const applied = applyMark(opened, mark, Boolean(turnId));
    // 打字回合自己选的 id 就是那一轮的身份：寄存在它名下的记号（它的 LLM/TTS 事件
    // 常常先于控制面的 `turn.accepted` 到达）在这里一次补上。
    return turnId ? drainPending(applied, turnId) : applied;
  }

  if (turnId) {
    const known = applyToKnownTurn(base, turnId, mark);
    if (known) return drainPending(known, turnId);
    if (!VOICE_LATENCY_USER_SIDE_STAGES.includes(mark.stage)) {
      return holdMark(base, turnId, mark);
    }
    if (base.current && base.current.turnId === null) {
      // 起音时还不知道身份：账本的 user.final / turn.accepted 到了就认领上。
      const claimed = applyMark(
        { ...base, current: { ...base.current, turnId } },
        mark,
        true,
      );
      return drainPending(claimed, turnId);
    }
    const previous = base.current ? closeTurn(base) : base;
    const opened = startTurn(previous, mark.kind ?? "voice", mark.at, turnId);
    return drainPending(applyMark(opened, mark, true), turnId);
  }

  if (!base.current) {
    if (mark.stage === "userDone") {
      return applyMark(startTurn(base, "voice", mark.at, null), mark, false);
    }
    return dropMark(base);
  }
  return applyMark(base, mark, false);
}

export interface VoiceLatencyRow extends VoiceLatencyStageSpec {
  /** 距本轮 0 点的累计毫秒；该环节还没到 = null。 */
  cumMs: number | null;
  /** 与上一个已知环节的差值（乱序到达时按 0 计，避免出现负耗时）。 */
  deltaMs: number | null;
  source: VoiceLatencySource | null;
}

export function voiceLatencyRows(turn: VoiceLatencyTurn | null): VoiceLatencyRow[] {
  let prev: number | null = null;
  return VOICE_LATENCY_STAGES.map((spec) => {
    const at = turn?.marks[spec.stage];
    if (at === undefined || at === null || !turn) {
      return { ...spec, cumMs: null, deltaMs: null, source: null };
    }
    const cumMs = Math.max(0, Math.round(at - turn.startedAt));
    const deltaMs = prev === null ? cumMs : Math.max(0, Math.round(at - prev));
    prev = at;
    return { ...spec, cumMs, deltaMs, source: turn.sources[spec.stage] ?? null };
  });
}

/** 「语音结束 → 语音开始」：用户说完到导师真正出声。 */
export function voiceLatencyTotalMs(turn: VoiceLatencyTurn | null): number | null {
  const done = turn?.marks.userDone;
  const speaking = turn?.marks.botSpeaking;
  if (done === undefined || speaking === undefined || speaking < done) return null;
  return Math.round(speaking - done);
}

/**
 * 「识别落定 → 导师出声」：总量量不出来时的**补充读数**，不是替代品。
 *
 * 为什么单独给一个：总量必须有「用户说完」锚点（实时通道 `user-stopped-speaking`，
 * Smart Turn 判定的 EOU）。这个锚点在本拓扑里会整轮不发 —— 它是
 * `LLMUserAggregator` 回合控制器的产物，真机日志（07:38 那通电话）里 4 段人声只发了
 * 3 次停止，还有一次落在隔壁轮头上。缺锚点时总量诚实地显示「未测得」，但读者至少
 * 该知道"系统认清这句话之后还花了多久"：那就是识别落定（账本 `user.final`，带回合身份、
 * 每轮必到）到出声。
 *
 * 它**不含** ASR 收尾那一段，所以一定比真实感知延迟小 —— 名字里写清起点是「识别落定」，
 * 不与总量混用同一个数字位。
 */
export function voiceLatencyRecognizedToSpeechMs(turn: VoiceLatencyTurn | null): number | null {
  const recognized = turn?.marks.asrFinal;
  const speaking = turn?.marks.botSpeaking;
  if (recognized === undefined || speaking === undefined || speaking < recognized) return null;
  return Math.round(speaking - recognized);
}

/** 说话时长：用户开口到回合判定，用于解释为什么总量里有多少是"还在说话"。 */
export function voiceLatencySpeechMs(turn: VoiceLatencyTurn | null): number | null {
  const started = turn?.marks.userStarted;
  const done = turn?.marks.userDone;
  if (started === undefined || done === undefined || done < started) return null;
  return Math.round(done - started);
}

/** 面板当前展示哪一轮：进行中的优先，否则最近收尾的一轮（照 demo）。 */
export function voiceLatencyFocusTurn(state: VoiceLatencyState): VoiceLatencyTurn | null {
  return state.current ?? state.history[state.history.length - 1] ?? null;
}

/** 服务端回合 id 在面板上的短形式（`turn_ab12cd34…` → `ab12cd34`）。 */
export function shortTurnId(turnId: string | null): string | null {
  if (!turnId) return null;
  const bare = turnId.replace(/^turn_/, "");
  return bare.slice(0, 8);
}
