/**
 * 端到端（jsdom）验证：控制器收到各通道消息后，延迟面板读到的是不是对的。
 *
 * 这条链路以前只能靠真机对着麦克风说话才能看到数字。现在用 `ingestVoiceRtviMessage`
 * 喂**与线上同形**的报文（RTVI 观察者消息 + 包在 `server-message` 里的账本信封），
 * 只把时钟固定住，就能断言每个环节的时间点取自哪条消息、总量算多少。
 *
 * 时钟：所有时间点都是本机收到该信号的时刻（`voiceLatencyNow()` = `performance.now()`），
 * 所以这里用假定时器把 `performance` 也接管，让"860ms"这种断言有意义。
 *
 * 阶段的头号不变量：**旁路信号不得凭空开轮**。远端音频能量、账本输出侧事件在
 * "没有开着的轮次"时都不许建轮 —— 否则服务端收尾与浏览器播完之间的空档里就会冒出
 * 一轮"导师出声 0 ms / 整屏等待"的幻影轮（真机上就是这么出现的）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { VoiceLatencyState } from "./voice-latency";

let ingest: (raw: string) => void;
let store: typeof import("./voice-latency-store");
let latency: typeof import("./voice-latency");

function rtvi(type: string, data: Record<string, unknown> = {}): string {
  return JSON.stringify({ label: "rtvi-ai", id: "m", type, data });
}

function ledger(
  seq: number,
  type: string,
  payload: Record<string, unknown> = {},
  turnId = "turn-1",
): string {
  // 与后端 `_publish` 发出的持久事件信封同形。
  return JSON.stringify({
    label: "rtvi-ai",
    type: "server-message",
    id: `m${seq}`,
    data: {
      type: "voice-event",
      event: {
        event_id: `e${seq}`,
        event_seq: seq,
        type,
        turn_id: turnId,
        audio_cursor_ms: seq * 10,
        payload,
      },
    },
  });
}

const turn = (state: VoiceLatencyState) =>
  latency.voiceLatencyFocusTurn(state);

describe("语音延迟管线（RTVI + 账本 → 面板读数）", () => {
  beforeEach(async () => {
    vi.resetModules();
    vi.useFakeTimers({
      toFake: ["performance", "Date", "setTimeout", "clearTimeout", "setInterval", "clearInterval"],
    });
    const controller = await import("./voice-session-controller");
    store = await import("./voice-latency-store");
    latency = await import("./voice-latency");
    ingest = controller.ingestVoiceRtviMessage;
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("一轮语音：各环节时间点来自哪条通道，总量=说完→出声", () => {
    ingest(ledger(1, "user.started"));
    vi.advanceTimersByTime(1200);
    ingest(rtvi("user-stopped-speaking"));
    vi.advanceTimersByTime(180);
    ingest(ledger(2, "user.final", { text: "今天天气怎么样" }));
    vi.advanceTimersByTime(40);
    ingest(ledger(3, "turn.accepted"));
    vi.advanceTimersByTime(220);
    ingest(rtvi("bot-llm-started"));
    vi.advanceTimersByTime(180);
    ingest(rtvi("bot-llm-text", { text: "今天" }));
    vi.advanceTimersByTime(260);
    ingest(ledger(4, "assistant.sentence.queued", { text: "今天天气很好。" }));
    vi.advanceTimersByTime(420);
    ingest(ledger(5, "assistant.sentence.playback_started", { sentence_seq: 1 }));
    vi.advanceTimersByTime(900);
    ingest(ledger(6, "assistant.sentence.ended", { text: "今天天气很好。" }));
    vi.advanceTimersByTime(100);
    // 整轮收尾走账本（带 turn 语义）。RTVI 的 `bot-stopped-speaking` 在本拓扑下收不到，
    // 也不再消费（见 controller 里的说明）。
    ingest(ledger(7, "assistant.sentence.playback_ended", { turn_final: true }));

    const state = store.getVoiceLatencySnapshot();
    const current = turn(state);
    expect(current?.kind).toBe("voice");
    expect(current?.transcript).toBe("今天天气怎么样");
    expect(current?.reply).toBe("今天天气很好。");
    expect(latency.voiceLatencyTotalMs(current)).toBe(1300);
    expect(latency.voiceLatencySpeechMs(current)).toBe(1200);

    const rows = latency.voiceLatencyRows(current);
    const cum = Object.fromEntries(rows.map((row) => [row.stage, row.cumMs]));
    expect(cum).toEqual({
      userStarted: 0,
      userDone: 1200,
      asrFinal: 1380,
      turnAccepted: 1420,
      llmStart: 1640,
      llmFirst: 1820,
      sentenceQueued: 2080,
      botSpeaking: 2500,
      sentenceEnded: 3400,
      answerDone: 3500,
    });
    const sources = Object.fromEntries(rows.map((row) => [row.stage, row.source]));
    expect(sources).toEqual({
      userStarted: "ledger",
      userDone: "rtvi",
      asrFinal: "ledger",
      turnAccepted: "ledger",
      llmStart: "rtvi",
      llmFirst: "rtvi",
      sentenceQueued: "ledger",
      botSpeaking: "ledger", // 账本 playback_started（带回合语义），不再是远端音频能量
      sentenceEnded: "ledger",
      answerDone: "ledger",
    });
    // 收尾后本轮进入历史，面板转而展示它。
    expect(state.current).toBeNull();
    expect(state.history).toHaveLength(1);
    expect(state.history[0].done).toBe(true);
  });

  it("没有 user-stopped-speaking：总量诚实地显示未测得", () => {
    ingest(ledger(1, "user.started"));
    vi.advanceTimersByTime(1400);
    ingest(ledger(2, "user.final", { text: "帮我算个数" }));
    vi.advanceTimersByTime(800);
    ingest(ledger(3, "turn.accepted"));
    vi.advanceTimersByTime(500);
    ingest(ledger(4, "assistant.sentence.playback_started", { sentence_seq: 1 }));

    const current = turn(store.getVoiceLatencySnapshot());
    expect(current?.marks.userDone).toBeUndefined();
    expect(current?.sources.userDone).toBeUndefined();
    expect(latency.voiceLatencyTotalMs(current)).toBeNull();
  });

  it("远端音频能量不再能凭空空轮（幻影轮闸门）", () => {
    // 服务端收尾之后、浏览器还在播缓冲音频的那一刻：只有音频能量在响。
    ingest(ledger(1, "assistant.sentence.ended", { text: "上一句。" }));
    ingest(ledger(2, "assistant.sentence.playback_ended", { turn_final: true }));
    ingest(rtvi("bot-stopped-speaking"));

    const state = store.getVoiceLatencySnapshot();
    expect(state.current).toBeNull();
    expect(state.history).toEqual([]);
  });

  it("起音后 20ms 跟来的是上一轮的打断：不关新一轮，也不丢（按身份寄存）", () => {
    ingest(ledger(1, "user.started"));
    vi.advanceTimersByTime(20);
    ingest(ledger(2, "turn.interrupted", { reason: "barge_in" }, "turn_prev"));

    const state = store.getVoiceLatencySnapshot();
    expect(state.history).toEqual([]);
    expect(state.current?.marks.userStarted).toBe(0);
    expect(state.current?.marks.answerDone).toBeUndefined();
    // 身份对不上任何一轮：**寄存**而不是丢弃 —— turn_prev 那一轮若随后认领身份，
    // 这条打断会补记到它自己头上（而不是被算进这一轮，也不是凭空消失）。
    expect(latency.voiceLatencyHeldCount(state)).toBe(1);
    expect(state.dropped).toBe(0);

    // 一直没人认领 → 寄存超期，这时才计入"已丢弃"（面板据此自证没算进任何一轮）。
    vi.advanceTimersByTime(latency.VOICE_LATENCY_HOLD_MS + 1000);
    ingest(ledger(3, "user.final")); // 让时间往前走（这条没有身份，落进当前轮）
    const expired = store.getVoiceLatencySnapshot();
    expect(latency.voiceLatencyHeldCount(expired)).toBe(0);
    expect(expired.dropped).toBe(1);
    expect(expired.current?.marks.answerDone).toBeUndefined();
  });

  it("真实序列：上一轮被打断的收尾补记在它自己那一轮上，新一轮不被误关", () => {
    ingest(ledger(1, "user.started")); // 账本这条带的是上一轮的 id，不能当身份用
    vi.advanceTimersByTime(400);
    ingest(ledger(2, "user.final", { text: "第一问" }, "turn_A"));
    ingest(ledger(3, "turn.accepted", {}, "turn_A"));
    vi.advanceTimersByTime(600);
    ingest(ledger(4, "assistant.sentence.playback_started", { sentence_seq: 1 }, "turn_A"));
    const firstTurnId = turn(store.getVoiceLatencySnapshot())?.turnId;
    expect(firstTurnId).toBe("turn_A");

    // 用户抢断：新的起音把第 1 轮收进历史，新一轮开出来（身份等 user.final 认领）。
    vi.advanceTimersByTime(1200);
    ingest(ledger(5, "user.started"));
    ingest(ledger(6, "turn.interrupted", { reason: "barge_in" }, "turn_A"));
    ingest(ledger(7, "assistant.sentence.playback_ended", { sentence_seq: 1, turn_final: true }, "turn_A"));
    ingest(ledger(8, "user.final", { text: "第二问" }, "turn_B"));

    const state = store.getVoiceLatencySnapshot();
    expect(state.history).toHaveLength(1);
    expect(state.history[0].turnId).toBe("turn_A");
    expect(state.history[0].sources.answerDone).toBe("barge-in");
    expect(state.history[0].marks.answerDone).toBe(2200);
    expect(state.current?.id).toBe(2);
    expect(state.current?.turnId).toBe("turn_B");
    expect(state.current?.marks.answerDone).toBeUndefined();
    expect(state.dropped).toBe(0);
  });

  it("打字回合也进面板：起点是发送时刻，走同一条链路", () => {
    store.markVoiceLatency({ stage: "userDone", source: "typed", kind: "text" });
    vi.advanceTimersByTime(300);
    ingest(ledger(3, "turn.accepted"));
    vi.advanceTimersByTime(700);
    ingest(ledger(4, "assistant.sentence.playback_started", { sentence_seq: 1 }));

    const current = turn(store.getVoiceLatencySnapshot());
    expect(current?.kind).toBe("text");
    expect(latency.voiceLatencyTotalMs(current)).toBe(1000);
  });

  it("打断：这一轮就地收尾，总量按已发生的环节给（未出声则未测得）", () => {
    ingest(rtvi("user-stopped-speaking"));
    vi.advanceTimersByTime(400);
    ingest(ledger(3, "turn.accepted"));
    vi.advanceTimersByTime(300);
    ingest(rtvi("bot-llm-started"));
    vi.advanceTimersByTime(200);
    ingest(ledger(9, "turn.interrupted"));

    const state = store.getVoiceLatencySnapshot();
    expect(state.current).toBeNull();
    expect(state.history).toHaveLength(1);
    expect(latency.voiceLatencyTotalMs(state.history[0])).toBeNull();
    expect(state.history[0].marks.turnAccepted).toBe(400);
  });

  it("清空后回到空态", () => {
    ingest(rtvi("user-stopped-speaking"));
    expect(store.getVoiceLatencySnapshot().current).not.toBeNull();
    store.resetVoiceLatency();
    const state = store.getVoiceLatencySnapshot();
    expect(state.current).toBeNull();
    expect(state.history).toEqual([]);
  });
});
