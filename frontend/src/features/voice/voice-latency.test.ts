/**
 * 语音延迟模型（纯函数）单测。
 *
 * 面板上那几个数字全部由这里的归轮规则算出来，而这条规则里有五条容易错的地方：
 * ① **只有用户侧的人声信号能开轮**（语音 = 起音，打字 = 发送）：远端音频能量、TTS 与
 *    账本的输出侧事件在没有开着的轮次时一律丢弃 —— 这就是"凭空开轮"的闸门；
 * ② **每个记号按自己的回合身份（turnId）归位**：带身份的收尾精确关它自己那一轮，
 *    已经收尾的轮会被**补记**（"被打断在第几毫秒"不会丢）；
 * ③ **身份比信号后到时寄存而不是丢弃**：身份还没出现的记号按身份存着，那一轮一认领
 *    就按时间顺序补记（真机上打字回合就是这样：身份走控制面/轮询，输出走数据通道）；
 * ④ RTVI 报文没有身份，只能归到当前活跃轮，其收尾仍靠"本轮有没有输出侧环节"这条代理判据；
 * ⑤ 同一环节可能被两条通道各报一次，只能记第一次。
 */

import { describe, expect, it } from "vitest";

import {
  VOICE_LATENCY_HISTORY_LIMIT,
  VOICE_LATENCY_HOLD_MS,
  emptyVoiceLatencyState,
  reduceVoiceLatency,
  voiceLatencyFocusTurn,
  voiceLatencyHeldCount,
  voiceLatencyRows,
  voiceLatencySpeechMs,
  voiceLatencyTotalMs,
  type VoiceLatencyMark,
  type VoiceLatencySource,
  type VoiceLatencyStage,
  type VoiceLatencyState,
} from "./voice-latency";

const mark = (
  state: VoiceLatencyState,
  stage: VoiceLatencyStage,
  at: number,
  source: VoiceLatencySource = "ledger",
  extra: { detail?: string; kind?: "voice" | "text"; turnId?: string } = {},
): VoiceLatencyState =>
  reduceVoiceLatency(state, { stage, at, source, ...extra } as VoiceLatencyMark);

/** 一轮完整对话：开口 → 说完 → 识别 → 受理 → 模型 → 合成 → 出声 → 播完 → 收尾。 */
function fullTurn(start = 1000): VoiceLatencyState {
  let state = emptyVoiceLatencyState();
  state = mark(state, "userStarted", start);
  state = mark(state, "userDone", start + 1200, "rtvi");
  state = mark(state, "asrFinal", start + 1380, "ledger", { detail: "今天天气怎么样" });
  state = mark(state, "turnAccepted", start + 1420);
  state = mark(state, "llmStart", start + 1640, "rtvi");
  state = mark(state, "llmFirst", start + 1820, "rtvi");
  state = mark(state, "sentenceQueued", start + 2080);
  state = mark(state, "botSpeaking", start + 2500, "ledger");
  state = mark(state, "sentenceEnded", start + 3400, "ledger", { detail: "今天天气很好。" });
  state = mark(state, "answerDone", start + 3500, "rtvi");
  return state;
}

describe("voice-latency · 归轮与读数", () => {
  it("完整一轮：各环节累计/差值正确，总量=说完→出声", () => {
    const state = fullTurn();
    expect(state.current).toBeNull();
    expect(state.history).toHaveLength(1);

    const turn = state.history[0];
    expect(turn.done).toBe(true);
    expect(turn.kind).toBe("voice");
    expect(turn.transcript).toBe("今天天气怎么样");
    expect(turn.reply).toBe("今天天气很好。");

    expect(voiceLatencyTotalMs(turn)).toBe(1300);
    expect(voiceLatencySpeechMs(turn)).toBe(1200);

    const rows = voiceLatencyRows(turn);
    expect(rows.map((row) => row.stage)).toEqual([
      "userStarted",
      "userDone",
      "asrFinal",
      "turnAccepted",
      "llmStart",
      "llmFirst",
      "sentenceQueued",
      "botSpeaking",
      "sentenceEnded",
      "answerDone",
    ]);
    expect(rows.map((row) => row.cumMs)).toEqual([
      0, 1200, 1380, 1420, 1640, 1820, 2080, 2500, 3400, 3500,
    ]);
    expect(rows.map((row) => row.deltaMs)).toEqual([
      0, 1200, 180, 40, 220, 180, 260, 420, 900, 100,
    ]);
    expect(rows.find((row) => row.stage === "botSpeaking")?.source).toBe("ledger");
    expect(rows.find((row) => row.stage === "asrFinal")?.source).toBe("ledger");
  });

  it("没有开着的轮次时，旁路信号一律不开轮（凭空开轮的闸门）", () => {
    const bypass: ReadonlyArray<[VoiceLatencyStage, VoiceLatencySource]> = [
      ["botSpeaking", "audio"],
      ["sentenceQueued", "ledger"],
      ["sentenceEnded", "ledger"],
      ["asrFinal", "ledger"],
      ["turnAccepted", "ledger"],
      ["llmFirst", "ledger"],
      ["answerDone", "ledger"],
    ];
    let state = emptyVoiceLatencyState();
    for (const [stage, source] of bypass) {
      state = mark(state, stage, 1000, source);
    }
    expect(state.current).toBeNull();
    expect(state.history).toEqual([]);
    // 反过来：只要有用户侧信号（这里是最小的一轮），同样的信号就照常被记录。
    state = mark(state, "userStarted", 1000);
    state = mark(state, "botSpeaking", 2000, "ledger");
    expect(state.current?.marks.botSpeaking).toBe(2000);
  });

  it("缺「用户说完」或「导师出声」时不编数字：总量为未测得", () => {
    let state = mark(emptyVoiceLatencyState(), "userStarted", 1000);
    state = mark(state, "asrFinal", 2000, "ledger");
    state = mark(state, "botSpeaking", 3000, "ledger");
    expect(voiceLatencyTotalMs(state.current)).toBeNull();
    expect(voiceLatencySpeechMs(state.current)).toBeNull();
  });

  it("「用户说完」迟到（两条通道重排）时回填到同一轮，而不是开新轮", () => {
    let state = mark(emptyVoiceLatencyState(), "userStarted", 1000);
    state = mark(state, "asrFinal", 1380, "ledger");
    state = mark(state, "userDone", 1400, "rtvi");
    expect(state.history).toHaveLength(0);
    expect(state.current?.marks.userDone).toBe(1400);
    expect(state.current?.marks.asrFinal).toBe(1380);
    expect(state.current?.startedAt).toBe(1000);
  });

  it("说话超过 3 秒也归同一轮（不再按时间窗口拆轮）", () => {
    let state = mark(emptyVoiceLatencyState(), "userStarted", 1000);
    state = mark(state, "userDone", 9000, "rtvi");
    expect(state.history).toHaveLength(0);
    expect(state.current?.marks.userDone).toBe(9000);
    expect(voiceLatencySpeechMs(state.current)).toBe(8000);
  });

  it("打断信号属于上一轮：本轮还没有输出环节时被丢弃（并计数）", () => {
    let state = mark(emptyVoiceLatencyState(), "userStarted", 1000);
    // 账本的 turn.interrupted 挂在被打断的那一轮上，却在新一轮起音后几毫秒到达；
    // 没有身份可比时（RTVI 场景）用"本轮有没有输出环节"这条代理判据挡住。
    state = mark(state, "answerDone", 1020, "ledger");
    expect(state.current).not.toBeNull();
    expect(state.current?.marks.answerDone).toBeUndefined();
    expect(state.current?.marks.userStarted).toBe(1000);
    expect(state.history).toHaveLength(0);
    expect(state.dropped).toBe(1);
  });

  it("带身份的收尾补记到它自己那一轮：新一轮不被误关，打断时刻也不丢", () => {
    // 第 1 轮：起音（还不知道身份）→ 账本 user.final / turn.accepted 认领身份 A。
    let state = mark(emptyVoiceLatencyState(), "userStarted", 1000);
    state = mark(state, "asrFinal", 1400, "ledger", { turnId: "turn_A" });
    expect(state.current?.turnId).toBe("turn_A");
    state = mark(state, "turnAccepted", 1420, "ledger", { turnId: "turn_A" });
    state = mark(state, "llmFirst", 1600, "ledger", { turnId: "turn_A" });
    // 用户抢断：新的起音把第 1 轮就地收进历史，新一轮（身份待认领）开出来。
    state = mark(state, "userStarted", 2000);
    expect(state.history).toHaveLength(1);
    expect(state.current?.id).toBe(2);
    state = mark(state, "turnAccepted", 2100, "ledger", { turnId: "turn_B" });
    expect(state.current?.turnId).toBe("turn_B");
    // 第 1 轮的打断收尾现在才到：补记到历史行上，而不是关掉第 2 轮。
    state = mark(state, "answerDone", 2200, "barge-in", { turnId: "turn_A" });
    expect(state.current?.id).toBe(2);
    expect(state.current?.marks.answerDone).toBeUndefined();
    expect(state.history[0].marks.answerDone).toBe(2200);
    expect(state.history[0].sources.answerDone).toBe("barge-in");
    expect(state.dropped).toBe(0);
  });

  it("没有回合认领的身份：记号按身份寄存，认领时补记（不是丢弃）", () => {
    let state = mark(emptyVoiceLatencyState(), "sentenceQueued", 1000, "ledger", {
      turnId: "turn_zzz",
    });
    // 还没有任何一轮认领 turn_zzz：不进任何一轮，也不算丢弃 —— 只是寄存。
    expect(state.current).toBeNull();
    expect(state.history).toEqual([]);
    expect(state.dropped).toBe(0);
    expect(voiceLatencyHeldCount(state)).toBe(1);

    // 起音丢了、但账本给出了这一轮的 user.final：以识别落定为 0 点开一轮（诚实），
    // 并把刚才寄存的那条补记进去 —— 而不是把数字算到别人头上，也不是扔掉。
    state = mark(state, "asrFinal", 1200, "ledger", { detail: "你好", turnId: "turn_zzz" });
    expect(state.current?.turnId).toBe("turn_zzz");
    expect(state.current?.startedAt).toBe(1200);
    expect(state.current?.transcript).toBe("你好");
    expect(state.current?.marks.sentenceQueued).toBe(1000);
    expect(state.current?.sources.sentenceQueued).toBe("ledger");
    expect(voiceLatencyHeldCount(state)).toBe(0);
    expect(state.dropped).toBe(0);
  });

  it("身份迟到（打字回合：控制面的身份只能靠轮询）时，先到的 llm/出声信号不被扔掉", () => {
    // 发送那一刻开一轮（还没有服务端身份），随后 worker 的账本信号先到。
    let state = mark(emptyVoiceLatencyState(), "userDone", 1000, "typed", { kind: "text" });
    state = mark(state, "llmFirst", 1400, "ledger", { turnId: "turn_T" });
    state = mark(state, "sentenceQueued", 1500, "ledger", { turnId: "turn_T" });
    state = mark(state, "botSpeaking", 1600, "ledger", { turnId: "turn_T" });
    expect(state.current?.marks.botSpeaking).toBeUndefined();
    expect(voiceLatencyHeldCount(state)).toBe(3);
    expect(voiceLatencyTotalMs(state.current)).toBeNull();

    // 身份到了（真机上来自 /turns/accept 应答或轮询补投）：三轮信号一次补上。
    state = mark(state, "turnAccepted", 1700, "typed", { turnId: "turn_T" });
    expect(state.current?.turnId).toBe("turn_T");
    expect(state.current?.marks.llmFirst).toBe(1400);
    expect(state.current?.marks.sentenceQueued).toBe(1500);
    expect(state.current?.marks.botSpeaking).toBe(1600);
    expect(state.current?.marks.turnAccepted).toBe(1700);
    expect(voiceLatencyTotalMs(state.current)).toBe(600);
    expect(voiceLatencyHeldCount(state)).toBe(0);
    expect(state.dropped).toBe(0);
  });

  it("寄存超期仍没人认领：算丢弃（面板据此自证没算进任何一轮）", () => {
    let state = mark(emptyVoiceLatencyState(), "botSpeaking", 1000, "ledger", {
      turnId: "turn_ghost",
    });
    expect(voiceLatencyHeldCount(state)).toBe(1);
    // 之后有别的信号流过（时间往前推过寄存窗口）。
    state = mark(state, "userStarted", 1000 + VOICE_LATENCY_HOLD_MS + 1_000);
    expect(voiceLatencyHeldCount(state)).toBe(0);
    expect(state.dropped).toBe(1);
  });

  it("本轮已有输出环节时，收尾信号照常收尾", () => {
    let state = mark(emptyVoiceLatencyState(), "userStarted", 1000);
    state = mark(state, "llmFirst", 1500, "ledger");
    state = mark(state, "answerDone", 1800, "ledger");
    expect(state.current).toBeNull();
    expect(state.history).toHaveLength(1);
    expect(state.history[0].marks.answerDone).toBe(1800);
  });

  it("总量以「用户说完」为起点，到导师出声为止", () => {
    let state = mark(emptyVoiceLatencyState(), "userStarted", 1000);
    state = mark(state, "userDone", 2600, "rtvi");
    state = mark(state, "botSpeaking", 3000, "ledger");
    expect(state.current?.marks.userDone).toBe(2600);
    // 说话时长（1000→2600）不计入总量：起点是「说完」那一刻。
    expect(voiceLatencyTotalMs(state.current)).toBe(400);
  });

  it("同一环节被两条通道各报一次时只记第一次", () => {
    let state = mark(emptyVoiceLatencyState(), "userDone", 1000, "ledger");
    state = mark(state, "asrFinal", 1100, "ledger", { detail: "第一段" });
    state = mark(state, "asrFinal", 1200, "rtvi", { detail: "第一段第二段" });
    expect(state.current?.marks.asrFinal).toBe(1100);
    expect(state.current?.transcript).toBe("第一段第二段"); // 文本取最新（服务端合并后的整轮文本）
  });

  it("乱序到达的环节不会产生负耗时", () => {
    let state = mark(emptyVoiceLatencyState(), "userDone", 1000, "rtvi");
    state = mark(state, "asrFinal", 900, "rtvi");
    const rows = voiceLatencyRows(state.current);
    expect(rows.find((row) => row.stage === "asrFinal")?.cumMs).toBe(0);
    expect(rows.find((row) => row.stage === "asrFinal")?.deltaMs).toBe(0);
  });

  it("新的起音开新轮：上一轮就地收尾", () => {
    let state = fullTurn();
    expect(state.history).toHaveLength(1);
    state = mark(state, "userStarted", 9000);
    expect(state.history).toHaveLength(1); // 上一轮已经收尾过，不重复入库
    expect(state.current?.id).toBe(2);
    expect(state.current?.marks.userStarted).toBe(9000);
  });

  it("每次起音都开新轮：重复投递由事件 id 去重，模型不再猜", () => {
    let state = mark(emptyVoiceLatencyState(), "userStarted", 1000);
    state = mark(state, "userStarted", 1200);
    expect(state.history).toHaveLength(1);
    expect(state.history[0].marks.userStarted).toBe(1000);
    expect(state.current?.marks.userStarted).toBe(1200);
  });

  it("打字回合单独成轮，起点是发送时刻", () => {
    let state = fullTurn();
    state = mark(state, "userDone", 5000, "typed", { kind: "text" });
    expect(state.current?.kind).toBe("text");
    expect(state.current?.marks.userDone).toBe(5000);
    state = mark(state, "botSpeaking", 6100, "ledger");
    expect(voiceLatencyTotalMs(state.current)).toBe(1100);
  });

  it("历史只留最近 12 轮，面板看最新一轮", () => {
    let state = emptyVoiceLatencyState();
    for (let index = 0; index < VOICE_LATENCY_HISTORY_LIMIT + 3; index += 1) {
      const start = (index + 1) * 10_000;
      state = mark(state, "userDone", start, "rtvi");
      state = mark(state, "botSpeaking", start + 900, "ledger");
      state = mark(state, "answerDone", start + 2000, "ledger");
    }
    expect(state.history).toHaveLength(VOICE_LATENCY_HISTORY_LIMIT);
    expect(state.history[state.history.length - 1].id).toBe(
      VOICE_LATENCY_HISTORY_LIMIT + 3,
    );
    expect(voiceLatencyFocusTurn(state)?.id).toBe(VOICE_LATENCY_HISTORY_LIMIT + 3);
  });

  it("进行中的一轮优先于历史", () => {
    let state = fullTurn();
    state = mark(state, "userDone", 9000, "rtvi");
    expect(voiceLatencyFocusTurn(state)?.id).toBe(state.current?.id);
  });

  it("没有数据时每一行都是等待态（面板空态的数据面）", () => {
    const rows = voiceLatencyRows(null);
    expect(rows).toHaveLength(10);
    expect(rows.every((row) => row.cumMs === null && row.deltaMs === null)).toBe(true);
    expect(voiceLatencyTotalMs(null)).toBeNull();
  });

  it("空闲状态下的收尾信号不产生垃圾轮次", () => {
    let state = mark(emptyVoiceLatencyState(), "answerDone", 500, "ledger");
    expect(state.current).toBeNull();
    expect(state.history).toEqual([]);
    // 反过来：轮次真的开着、且已经有输出环节时，收尾信号照常生效。
    state = mark(state, "userDone", 1000, "rtvi");
    state = mark(state, "sentenceQueued", 1200, "ledger");
    state = mark(state, "answerDone", 1500, "ledger");
    expect(state.history).toHaveLength(1);
  });

  it("同一轮的句文本按播完顺序累加", () => {
    let state = mark(emptyVoiceLatencyState(), "userDone", 1000, "rtvi");
    state = mark(state, "sentenceEnded", 2000, "ledger", { detail: "第一句。" });
    state = mark(state, "sentenceEnded", 3000, "ledger", { detail: "第二句。" });
    state = mark(state, "answerDone", 3100, "rtvi");
    expect(state.history[0].reply).toBe("第一句。第二句。");
  });
});
