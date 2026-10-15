/**
 * 真机事件流回放（一次性诊断脚本，gitignore）。
 *
 * 数据来源：线上会话 vs_c03d8b92cd604914ae0d5037 的 `voice_events` 全量导出
 * （frontend/test-artifacts/voicelatency-session.json），按 created_at 的相对时刻
 * 喂回 `ingestVoiceRtviMessage`，最后把面板**真实渲染出来的文字**打出来。
 *
 * 建模口径（与线上的差异都在这里；结论以此为准）：
 *  - 账本事件：worker 进程写的经数据通道**即时**送达；控制面（API 进程）写的
 *    `user.final` / `turn.accepted` **永远不到** —— 数据通道只带 worker 写的事件，
 *    而游标（`lastEventSeq`）会被随后到达的 worker 事件推到它们前面，轮询
 *    （`after_event_seq=`）因此再也取不回它们。本机访问日志的游标序列
 *    `… 27 → 32`（而这一轮的 user.final/turn.accepted 是 29/30）就是证据。
 *  - 因此打字回合的身份只能来自 `/turns/accept` 的**应答**（修复后才会用它）。
 *  - RTVI：`user-stopped-speaking` 用后台日志里的真实时刻（3 次）；
 *    `bot-llm-started/text` 用该回合首个 `assistant.llm.delta` 的时刻近似。
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/voice", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/api/voice")>(),
  getVoiceCapabilities: async () => ({ agent_capabilities: [], voice_provider_ready: true }),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/api/client")>(),
  apiClient: { get: async () => ({ events: [] }), delete: async () => ({}) },
}));

import { VoiceLatencyRail } from "./voice-debug-panel";
import * as staticController from "./voice-session-controller";
import * as staticLatency from "./voice-latency";
import * as staticStore from "./voice-latency-store";

interface Row {
  event_id: string;
  event_seq: number;
  type: string;
  turn_id: string | null;
  request_id: string;
  phase: string;
  audio_cursor_ms: number | null;
  session_epoch: number;
  at: string;
  payload: Record<string, unknown>;
  session_id: string;
}

const RAW: Row[] = JSON.parse(
  readFileSync(
    path.resolve(__dirname, "../../../test-artifacts/voicelatency-session.json"),
    "utf8",
  ),
) as Row[];

/** 后台日志里 `User stopped speaking` 的真实时刻（UTC，本会话内）。 */
const RTVI_USER_STOPPED = [
  "2026-09-18T07:38:35.859",
  "2026-09-18T07:38:48.017",
  "2026-09-18T07:38:49.227",
].map((s) => Date.parse(`${s}Z`));

type Modules = {
  controller: typeof staticController;
  store: typeof staticStore;
  latency: typeof staticLatency;
};

function ms(at: string): number {
  return Date.parse(`${at.replace(" ", "T")}Z`);
}

function ledgerFrame(row: Row): string {
  return JSON.stringify({
    label: "rtvi-ai",
    id: `m${row.event_seq}`,
    type: "server-message",
    data: {
      type: "voice-event",
      event: {
        event_id: row.event_id,
        event_seq: row.event_seq,
        seq: row.event_seq,
        type: row.type,
        turn_id: row.turn_id,
        request_id: row.request_id,
        phase: row.phase,
        audio_cursor_ms: row.audio_cursor_ms,
        session_epoch: row.session_epoch,
        session_id: row.session_id,
        timestamp: `${row.at.replace(" ", "T")}Z`,
        payload: row.payload,
      },
    },
  });
}

function rtvi(type: string, data: Record<string, unknown> = {}): string {
  return JSON.stringify({ label: "rtvi-ai", id: `r${type}`, type, data });
}

interface Plan {
  /** 模拟"修复前的控制器"：不拿 /turns/accept 应答里的身份。 */
  useAcceptResponse: boolean;
}

interface Delivery {
  at: number;
  run: () => void;
}

async function freshModules(): Promise<Modules> {
  vi.resetModules();
  return {
    controller: await import("./voice-session-controller"),
    store: await import("./voice-latency-store"),
    latency: await import("./voice-latency"),
  };
}

/** 把真机事件流按时间轴喂进去，返回最终状态。 */
async function drive(plan: Plan, modules: Modules) {
  const { store, latency, controller } = modules;
  localStorage.setItem("learngraph.voice.remote-sessions.v1", JSON.stringify({ "test:latency-replay": RAW[0].session_id }));
  controller.voiceSessionController.open("test", "latency-replay");
  vi.useFakeTimers({
    toFake: ["performance", "Date", "setTimeout", "clearTimeout", "setInterval", "clearInterval"],
  });

  const base = ms(RAW[0].at);
  const deliveries: Delivery[] = [];
  const typedTurns: { at: number; turnId: string }[] = [];
  const firstDelta = new Map<string, number>();

  for (const row of RAW) {
    const at = ms(row.at) - base;
    if (row.type === "assistant.llm.delta" && row.turn_id && !firstDelta.has(row.turn_id)) {
      firstDelta.set(row.turn_id, at);
    }
    const controlPlane =
      row.payload.client_message_id != null &&
      (row.type === "user.final" || row.type === "turn.accepted");
    if (row.type === "user.final" && row.payload.client_message_id != null) {
      // 打字回合：发送那一刻前端自己开轮；身份由 /turns/accept 应答给出（修复后）。
      deliveries.push({
        at: at - 200,
        run: () => store.markVoiceLatency({ stage: "userDone", source: "typed", kind: "text" }),
      });
      typedTurns.push({ at: at - 150, turnId: String(row.turn_id ?? "") });
    }
    if (controlPlane) continue; // 控制面事件：数据通道不带，游标又已越过 → 永远不到
    deliveries.push({ at, run: () => controller.ingestVoiceRtviMessage(ledgerFrame(row)) });
  }

  for (const typed of typedTurns) {
    if (!plan.useAcceptResponse) continue;
    deliveries.push({
      at: typed.at,
      run: () =>
        store.markVoiceLatency({
          stage: "turnAccepted",
          source: "typed",
          turnId: typed.turnId,
        }),
    });
  }

  for (const stopAt of RTVI_USER_STOPPED) {
    deliveries.push({
      at: stopAt - base,
      run: () => controller.ingestVoiceRtviMessage(rtvi("user-stopped-speaking")),
    });
  }
  for (const [, at] of firstDelta) {
    deliveries.push({
      at: at - 40,
      run: () => controller.ingestVoiceRtviMessage(rtvi("bot-llm-started")),
    });
    deliveries.push({
      at: at - 5,
      run: () => controller.ingestVoiceRtviMessage(rtvi("bot-llm-text", { text: "…" })),
    });
  }

  deliveries.sort((a, b) => a.at - b.at);
  let now = 0;
  for (const delivery of deliveries) {
    if (delivery.at > now) {
      vi.advanceTimersByTime(delivery.at - now);
      now = delivery.at;
    }
    delivery.run();
  }
  vi.advanceTimersByTime(60_000); // 让寄存超期的记号落账（面板显示"已丢弃"）

  return { state: store.getVoiceLatencySnapshot(), latency, store };
}

function report(state: ReturnType<typeof staticStore.getVoiceLatencySnapshot>, latency: Modules["latency"]) {
  const fmt = (turn: import("./voice-latency").VoiceLatencyTurn | null) => {
    if (!turn) return "—";
    const rows = latency
      .voiceLatencyRows(turn)
      .filter((row) => row.cumMs !== null)
      .map((row) => `${row.stage}=${row.cumMs}(${row.source})`)
      .join(" ");
    return [
      `#${turn.id} kind=${turn.kind} id=${turn.turnId ?? "null"} done=${turn.done}`,
      `  total=${latency.voiceLatencyTotalMs(turn)} speech=${latency.voiceLatencySpeechMs(turn)}`,
      `  rows: ${rows || "(无)"}`,
    ].join("\n");
  };
  return [
    `dropped=${state.dropped} held=${latency.voiceLatencyHeldCount(state)}`,
    `current:\n${fmt(state.current)}`,
    "history:",
    ...state.history.map((turn) => fmt(turn)),
  ].join("\n");
}

describe("真机事件流回放", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("面板最终显示成什么样（照抄界面文字）", async () => {
    // 用静态模块实例渲染（渲染器与组件必须是同一份 React），所以这条排在最前面。
    const { state, latency } = await drive(
      { useAcceptResponse: true },
      { controller: staticController, store: staticStore, latency: staticLatency },
    );
    const view = render(<VoiceLatencyRail />);
    const text = (view.container.textContent ?? "").replace(/\s+/g, " ").trim();
    console.log(
      `========== 面板文字（渲染态）==========\n${text}\n========== 状态 ==========\n${report(
        state,
        latency,
      )}`,
    );
    expect(text).toContain("语音延迟");
  });

  it("修复前（身份拿不到）：打字回合整轮空转", async () => {
    const { state, latency } = await drive({ useAcceptResponse: false }, await freshModules());
    console.log(`========== 修复前 ==========\n${report(state, latency)}`);
    expect(state.current?.marks.botSpeaking).toBeUndefined();
  });

  it("修复后（应答里的身份 + 按身份寄存）：每一轮打字回合都读得出数字", async () => {
    const { state, latency } = await drive({ useAcceptResponse: true }, await freshModules());
    console.log(`========== 修复后 ==========\n${report(state, latency)}`);
    const totals = state.history
      .map((turn) => latency.voiceLatencyTotalMs(turn))
      .filter((value): value is number => value !== null);
    expect(state.dropped).toBe(0);
    expect(state.current).toBeNull();
    expect(totals).toEqual([1498, 1103, 1091, 1405, 1475]);
  });
});
