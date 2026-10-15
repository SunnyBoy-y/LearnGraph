/**
 * 端到端（jsdom）验证：官方 `bot-output` + 因果账本事件 如何驱动画布上的字幕。
 *
 * 这条路径以前只能靠真机麦克风验证（`handleRtviMessage` 是模块私有的）。现在用
 * `ingestVoiceRtviMessage` 喂**与线上逐字段相同**的报文，断言：
 *
 *  1. 没有账本的 provider 由 `bot-output{new}` 即时展示文字；
 *  2. `assistant.sentence.queued` → 该句音频开始后才进入画布并显示灰字；
 *  3. `bot-output{completed}` **不点亮** —— 它是 urgent 帧，实测比音频早到十几秒，
 *     而且它的 `accumulated_text` 本身就是整句，所以状态与游标两样都得丢掉；
 *  4. 账本 `assistant.sentence.ended` → 那一句点亮（因果信号），且一次只点亮一句；
 *  5. 账本 `turn.interrupted` → 未开读的预读句撤下，最终正文只含已开始播放的文本。
 *
 * 控制器的字幕状态是**模块级**的（同一页只跑一条语音会话），所以每个用例都
 * `vi.resetModules()` 后重新 import，避免用例之间互相污染。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type RenderUpdate = {
  id?: string;
  removes?: string[];
  role?: string;
  text: string;
  final: boolean;
  interrupted?: boolean;
  captionParts?: {
    text: string;
    spokenChars: number;
    status: string;
    willBeSpoken: boolean;
  }[];
};

let ingest: (raw: string) => void;
let getSnapshot: () => {
  tasks: Array<{ taskId: string; status: string; activities?: Array<{ text: string }> }>;
  realtimeDelivery: {
    received: number;
    accepted: number;
    replayed: number;
    duplicates: number;
    dropped: number;
    queued: number;
    ended: number;
  };
};
let updates: RenderUpdate[] = [];

const capture = (event: Event) => {
  updates.push((event as CustomEvent<RenderUpdate>).detail);
};

function botOutput(text: string, status: string, accumulated = ""): string {
  return JSON.stringify({
    label: "rtvi-ai",
    type: "bot-output",
    id: "m1",
    data: {
      text,
      aggregated_by: "sentence",
      will_be_spoken: true,
      spoken_status: status,
      segment_id: 1,
      spoken_progress: { accumulated_text: accumulated, remaining_text: text },
    },
  });
}

function sentenceEnded(seq: number, text: string): string {
  // 与后端 `_publish` 发出的持久事件信封同形（voice-event 包一层 server-message）。
  return JSON.stringify({
    label: "rtvi-ai",
    type: "server-message",
    id: "m2",
    data: {
      type: "voice-event",
      event: {
        event_id: `e${seq}`,
        event_seq: seq,
        type: "assistant.sentence.ended",
        turn_id: "turn-1",
        audio_cursor_ms: 0,
        payload: { sentence_seq: seq, text, audio_end_cursor_ms: 1000 },
      },
    },
  });
}

/**
 * 账本「这一句开始播放」的事件（字段取自线上 `voice_events` 的导出行）。
 *
 * 它是这一句的**第二个**文本来源：官方 `bot-output` 没送到时，屏幕上这句话由它补上。
 */
function sentenceQueued(seq: number, text: string): string {
  return JSON.stringify({
    label: "rtvi-ai",
    type: "server-message",
    id: "m4",
    data: {
      type: "voice-event",
      event: {
        event_id: `q${seq}`,
        event_seq: seq,
        type: "assistant.sentence.queued",
        turn_id: "turn-1",
        audio_cursor_ms: 0,
        payload: { sentence_seq: seq, text, segment_id: `turn-1:s${seq}` },
      },
    },
  });
}

function liveSentence(type: "assistant.sentence.queued" | "assistant.sentence.ended", seq: number, text: string): string {
  const event = {
    delivery: "live",
    type,
    turn_id: "live-turn-1",
    payload: {
      live_event_id: `live-${type}-${seq}`,
      sentence_seq: seq,
      text,
      ...(type === "assistant.sentence.ended" ? { audio_end_cursor_ms: 1000 } : {}),
    },
  };
  // Exercise the nested/string form emitted by some RTVI server-message
  // adapters, while the ordinary helpers above cover durable replay envelopes.
  return JSON.stringify({
    label: "rtvi-ai",
    type: "server-message",
    data: {
      type: "server-message",
      data: JSON.stringify({ type: "voice-event", event }),
    },
  });
}

function assistantFinal(seq: number, turnId: string, text: string): string {
  return JSON.stringify({
    label: "rtvi-ai",
    type: "server-message",
    data: {
      type: "voice-event",
      event: {
        event_id: `final-${seq}`,
        event_seq: seq,
        type: "turn.finalized",
        turn_id: turnId,
        payload: { role: "assistant", text },
      },
    },
  });
}

/** 账本的打断收尾（带 turn 语义）。RTVI 的 `bot-interrupted` 在本拓扑下收不到。 */
function turnInterrupted(seq: number, heardText: string): string {
  return JSON.stringify({
    label: "rtvi-ai",
    type: "server-message",
    id: "m3",
    data: {
      type: "voice-event",
      event: {
        event_id: `e${seq}`,
        event_seq: seq,
        type: "turn.interrupted",
        turn_id: "turn-1",
        audio_cursor_ms: 0,
        payload: { reason: "barge_in", heard_text: heardText },
      },
    },
  });
}

const lastLive = () => [...updates].reverse().find((u) => !u.final);

describe("语音字幕管线（bot-output + 账本点亮）", () => {
  beforeEach(async () => {
    vi.resetModules();
    const mod = await import("./voice-session-controller");
    ingest = mod.ingestVoiceRtviMessage;
    getSnapshot = mod.voiceSessionController.getSnapshot;
    updates = [];
    window.addEventListener("learngraph:voice-render", capture);
  });

  afterEach(() => {
    window.removeEventListener("learngraph:voice-render", capture);
  });

  it("有服务端回合身份的兜底文字被账本替换，迟到的进度不会重建旧气泡", () => {
    const raw = JSON.parse(botOutput("第一句。第二句。", "new"));
    raw.data.turn_id = "turn-1";
    ingest(JSON.stringify(raw));
    expect(lastLive()?.id).toBe("assistant-live-turn-1");

    ingest(sentenceQueued(1, "第一句。"));
    expect(lastLive()?.removes).toContain("assistant-live-turn-1");
    expect(lastLive()?.text).toBe("第一句。");
    const updateCount = updates.length;
    raw.data.spoken_status = "completed";
    ingest(JSON.stringify(raw));
    expect(updates).toHaveLength(updateCount);
    expect(lastLive()?.captionParts?.[0].spokenChars).toBe(0);
  });

  it("官方文字先兜底，账本到达后只展示当前播放句，ended 再点亮", () => {
    ingest(botOutput("第一句。", "new"));
    ingest(botOutput("第二句。", "new"));

    // 缺少账本的 provider 仍能显示文字；有账本后由播放证据接管。
    let live = lastLive();
    expect(live?.text).toBe("第一句。第二句。");
    expect(live?.final).toBe(false);

    // 音频队列的开始 marker 到达，当前句才进入同一条回复气泡并呈灰色。
    ingest(sentenceQueued(1, "第一句。"));
    live = lastLive();
    expect(live?.removes).toContain("assistant-live-live");
    expect(live?.captionParts?.map((p) => p.text)).toEqual(["第一句。"]);
    expect(live?.captionParts?.every((p) => p.spokenChars === 0)).toBe(true);

    // 官方 completed：状态与 accumulated 都带"整句已读"的意思，一个都不许采信。
    ingest(botOutput("第一句。", "completed", "第一句。"));
    live = lastLive();
    expect(live?.captionParts?.[0].spokenChars).toBe(0);
    expect(live?.captionParts?.[0].status).not.toBe("completed");

    // 因果信号：这一句真的播完了 → 点亮它；B 尚未开始播放，所以仍不上屏。
    ingest(sentenceEnded(1, "第一句。"));
    live = lastLive();
    expect(live?.captionParts?.[0].status).toBe("completed");
    expect(live?.captionParts?.[0].spokenChars).toBe(4);
    expect(live?.captionParts?.map((p) => p.text)).toEqual(["第一句。"]);

    // B 的 start marker 到达后，才在同一个回复窗口追加灰色 B。
    ingest(sentenceQueued(2, "第二句。"));
    live = lastLive();
    expect(live?.captionParts?.map((p) => p.text)).toEqual(["第一句。", "第二句。"]);
    expect(live?.captionParts?.[1].spokenChars).toBe(0);

    ingest(sentenceEnded(2, "第二句。"));
    live = lastLive();
    expect(live?.captionParts?.every((p) => p.status === "completed")).toBe(true);
  });

  it("实时 queued/ended 的嵌套报文直接进入播放账本，并被监控去重", () => {
    ingest(liveSentence("assistant.sentence.queued", 1, "实时第一句。"));
    expect(lastLive()?.captionParts?.[0].status).toBe("in-progress");
    expect(getSnapshot().realtimeDelivery).toMatchObject({
      received: 1,
      accepted: 1,
      queued: 1,
      ended: 0,
      duplicates: 0,
    });

    // Re-delivery of the same live marker must not create another caption row.
    ingest(liveSentence("assistant.sentence.queued", 1, "实时第一句。"));
    expect(getSnapshot().realtimeDelivery.duplicates).toBe(1);

    ingest(liveSentence("assistant.sentence.ended", 1, "实时第一句。"));
    expect(lastLive()?.captionParts?.[0].status).toBe("completed");
    expect(getSnapshot().realtimeDelivery).toMatchObject({
      received: 3,
      accepted: 2,
      queued: 1,
      ended: 1,
    });
  });

  it("官方通道没送到时：账本 queued 按「正在播」把这一句放上屏，ended 再点亮", () => {
    ingest(sentenceQueued(1, "第一句。"));
    let live = lastLive();
    expect(live?.captionParts?.map((p) => p.text)).toEqual(["第一句。"]);
    // 正在播 = 灰；这一刻它确实在出声（账本帧被输出传输放行才写）。
    expect(live?.captionParts?.[0].status).not.toBe("completed");
    expect(live?.text).toBe("第一句。");

    ingest(sentenceEnded(2, "第一句。"));
    live = lastLive();
    expect(live?.captionParts?.[0].status).toBe("completed");

    // 下一句开始播 → 它成为当前灰句，屏幕上是"第一句正常 + 第二句灰"。
    ingest(sentenceQueued(3, "第二句。"));
    live = lastLive();
    expect(live?.captionParts?.map((p) => p.text)).toEqual(["第一句。", "第二句。"]);
    expect(live?.captionParts?.[0].status).toBe("completed");
    expect(live?.captionParts?.[1].status).not.toBe("completed");
    expect(live?.text).toBe("第一句。第二句。");
  });

  it("同一句被两条通道各宣布一次时，屏幕上只有一句", () => {
    ingest(sentenceQueued(1, "第一句。"));
    ingest(botOutput("第一句。", "new"));
    const live = lastLive();
    expect(live?.captionParts?.map((p) => p.text)).toEqual(["第一句。"]);
  });

  it("播放账本接管后，更远的句子不会提前上屏", () => {
    ingest(botOutput("第一句。", "new"));
    ingest(botOutput("第二句。", "new"));
    ingest(botOutput("第三句。", "new"));
    let live = lastLive();
    expect(live?.text).toBe("第一句。第二句。第三句。");
    ingest(sentenceQueued(1, "第一句。"));
    live = lastLive();
    expect(live?.captionParts?.map((p) => p.text)).toEqual(["第一句。"]);
    ingest(botOutput("第三句。", "completed", "第三句。"));
    expect(lastLive()?.captionParts?.map((p) => p.text)).toEqual(["第一句。"]);
    ingest(sentenceEnded(1, "第一句。"));
    live = lastLive();
    expect(live?.captionParts?.map((p) => p.text)).toEqual(["第一句。"]);
    ingest(sentenceQueued(2, "第二句。"));
    live = lastLive();
    expect(live?.captionParts?.map((p) => p.text)).toEqual(["第一句。", "第二句。"]);
    ingest(sentenceEnded(2, "第二句。"));
    live = lastLive();
    expect(live?.captionParts?.map((p) => p.text)).toEqual(["第一句。", "第二句。"]);
    ingest(sentenceQueued(3, "第三句。"));
    live = lastLive();
    expect(live?.captionParts?.map((p) => p.text)).toEqual(["第一句。", "第二句。", "第三句。"]);
  });

  it("打断：未播放草稿从不上屏，最终正文只留已经点亮的文本", () => {
    ingest(JSON.stringify({
      label: "rtvi-ai", type: "server-message", data: {
        type: "voice-event", event: {
          event_id: "draft-before-playback", event_seq: 0,
          type: "assistant.llm.delta", turn_id: "turn-1",
          payload: { text: "第一句。第二句。未播放尾巴。" },
        },
      },
    }));
    expect(lastLive()).toBeUndefined();
    ingest(botOutput("第一句。", "new"));
    ingest(botOutput("第二句。", "new"));
    ingest(sentenceQueued(1, "第一句。"));
    ingest(sentenceEnded(1, "第一句。"));

    ingest(turnInterrupted(2, "第一句。"));

    const final = [...updates].reverse().find((u) => u.final);
    expect(final?.interrupted).toBe(true);
    // 未朗读的第二句不进最终正文（"未听到不落库"在前端这一侧的体现）。
    expect(final?.text).toBe("第一句。");
  });

  it("打断正在播放的第二句：同一回复保留第一句和已开读的第二句", () => {
    ingest(botOutput("第一句。", "new"));
    ingest(botOutput("第二句。", "new"));
    ingest(sentenceEnded(1, "第一句。"));
    // `queued` is the causal audio-start marker.  B is gray on screen, but it
    // is now part of the audible prefix and must survive the interruption.
    ingest(sentenceQueued(2, "第二句。"));
    ingest(turnInterrupted(3, "第一句。第二句。"));

    const final = [...updates].reverse().find((u) => u.final);
    expect(final?.interrupted).toBe(true);
    expect(final?.text).toBe("第一句。第二句。");
  });

  it("上一轮 durable final 到达后，新一轮使用新的服务端 turn 身份", () => {
    ingest(botOutput("第一轮。", "new"));
    ingest(sentenceQueued(1, "第一轮。"));
    ingest(assistantFinal(2, "turn-1", "第一轮。"));

    ingest(botOutput("第二轮。", "new"));
    ingest(JSON.stringify({
      label: "rtvi-ai", type: "server-message", data: {
        type: "voice-event", event: {
          event_id: "q3", event_seq: 3, type: "assistant.sentence.queued",
          turn_id: "turn-2", audio_cursor_ms: 0,
          payload: { sentence_seq: 1, text: "第二轮。" },
        },
      },
    }));

    const final = updates.find((u) => u.final && u.text === "第一轮。");
    const live = lastLive();
    expect(final?.text).toBe("第一轮。");
    expect(live?.text).toBe("第二轮。");
  });

  it("legacy user-transcription partials also enter the conversation canvas", () => {
    ingest(JSON.stringify({
      label: "rtvi-ai",
      type: "user-transcription",
      data: { text: "正在识别", final: false },
    }));

    const live = lastLive();
    expect(live?.role).toBe("user");
    expect(live?.text).toBe("正在识别");
    expect(live?.final).toBe(false);
  });

  it("voice-session task notifications do not consume the per-task progress cursor", () => {
    // The voice journal sequence and the sub-agent event sequence are
    // independent. A high session sequence must not make a later task event
    // with seq=1 disappear from the task canvas.
    ingest(JSON.stringify({
      label: "rtvi-ai",
      type: "server-message",
      data: {
        type: "voice-event",
        event: {
          event_id: "voice-task-accepted",
          event_seq: 100,
          type: "task.accepted",
          payload: { task_id: "task-1", status: "queued", title: "研究" },
        },
      },
    }));
    ingest(JSON.stringify({
      label: "rtvi-ai",
      type: "server-message",
      data: {
        type: "voice-event",
        event: {
          event_id: "voice-task-progress",
          event_seq: 1,
          type: "task.progress",
          payload: { task_id: "task-1", progress_summary: "正在读取资料" },
        },
      },
    }));

    const task = getSnapshot().tasks.find((item) => item.taskId === "task-1");
    expect(task?.status).toBe("running");
    expect(task?.activities?.some((item) => item.text.includes("正在读取资料"))).toBe(true);
  });
});
