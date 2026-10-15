import { describe, expect, it } from "vitest";

import {
  emptyLink,
  linkIsCallable,
  linkLabel,
  noteDataChannel,
  notePeerState,
  notePipelineReady,
  notePlayback,
  noteRecoverFailed,
  noteSignalingAnswer,
  recoverBudgetExceeded,
  type VoiceLinkPhase,
  type VoiceLinkState,
} from "./voice-connection";

/**
 * F06 的两个复现在这里必须变成"拒绝错误"：
 *
 * - A：signaling answer 落地（探针里 FakePeerConnection 仍是 `new`、数据通道仍是
 *   `connecting`）时，链路不得被当成已连接；
 * - B：掉线恢复成 `connected` 之后必须退出 recovering，回到 live/pipeline。
 */

function link(patch: Partial<VoiceLinkState> = {}): VoiceLinkState {
  return { ...emptyLink(), ...patch };
}

/** 一条已经真正可通话的链路：ICE 连通 + 数据通道打开 + 导师管线就绪。 */
function liveLink(): VoiceLinkState {
  return link({
    phase: "live",
    signaling: true,
    transport: true,
    dataChannel: true,
    pipeline: true,
  });
}

describe("emptyLink", () => {
  it("starts idle with every fact false and no error", () => {
    const state = emptyLink();
    expect(state.phase).toBe("idle");
    expect(state.signaling).toBe(false);
    expect(state.transport).toBe(false);
    expect(state.dataChannel).toBe(false);
    expect(state.pipeline).toBe(false);
    expect(state.playback).toBe(false);
    expect(state.reconnectAttempt).toBe(0);
    expect(state.recoverStartedAt).toBeNull();
    expect(state.iceRestartUsed).toBe(false);
    expect(state.lastError).toBeNull();
    expect(linkIsCallable(state)).toBe(false);
  });
});

describe("linkIsCallable", () => {
  it("rejects F06-A: a signaling answer is not a connection", () => {
    // 此时探针里的 PeerConnection 还是 `new`、data channel 还是 `connecting`：
    // 两个事实都不在状态里，所以没有任何东西能声称可通话。
    const afterAnswer = noteSignalingAnswer(emptyLink());
    expect(afterAnswer.signaling).toBe(true);
    expect(afterAnswer.transport).toBe(false);
    expect(linkIsCallable(afterAnswer)).toBe(false);
  });

  it("needs a real connected transport, not just an open channel or a ready pipeline", () => {
    expect(
      linkIsCallable(link({ transport: false, dataChannel: true, pipeline: true })),
    ).toBe(false);
  });

  it("accepts a connected transport with either a data channel or a ready pipeline", () => {
    expect(linkIsCallable(link({ transport: true, dataChannel: true }))).toBe(true);
    expect(linkIsCallable(link({ transport: true, pipeline: true }))).toBe(true);
    expect(linkIsCallable(link({ transport: true }))).toBe(false);
  });
});

describe("noteSignalingAnswer", () => {
  it("advances to media without claiming transport or callability", () => {
    const next = noteSignalingAnswer(emptyLink());
    expect(next.phase).toBe("media");
    expect(next.transport).toBe(false);
    expect(next.dataChannel).toBe(false);
    expect(next.pipeline).toBe(false);
  });

  it("moves signaling to media", () => {
    expect(noteSignalingAnswer(link({ phase: "signaling" })).phase).toBe("media");
  });

  it("never pulls a live link backwards", () => {
    const next = noteSignalingAnswer(liveLink());
    expect(next.phase).toBe("live");
    expect(linkIsCallable(next)).toBe(true);
  });

  it("keeps recovering during a renegotiation answer", () => {
    // 重连时的重协商 answer 不能把"正在重连…"顶掉：那张 SDP 还没证明网络恢复了。
    const recovering = link({
      phase: "recovering",
      signaling: true,
      recoverStartedAt: 100,
      iceRestartUsed: true,
    });
    const next = noteSignalingAnswer(recovering);
    expect(next.phase).toBe("recovering");
    expect(next.signaling).toBe(true);
  });
});

describe("notePeerState", () => {
  it("rejects F06-B: recovering to connected leaves recovering", () => {
    const dropped = notePeerState(liveLink(), "disconnected", "disconnected", 1_000);
    expect(dropped.action).toBe("ice-restart");
    expect(dropped.state.phase).toBe("recovering");
    expect(dropped.state.iceRestartUsed).toBe(true);
    expect(dropped.state.recoverStartedAt).toBe(1_000);
    expect(dropped.state.transport).toBe(false);
    expect(dropped.state.reconnectAttempt).toBe(0);
    expect(linkIsCallable(dropped.state)).toBe(false);

    const back = notePeerState(dropped.state, "connected", "connected", 1_200);
    expect(back.action).toBe("recovered");
    expect(back.state.phase).toBe("live");
    expect(back.state.transport).toBe(true);
    expect(back.state.recoverStartedAt).toBeNull();
    expect(linkIsCallable(back.state)).toBe(true);
    expect(linkLabel(back.state)).toBe("语音导师已连接，请直接说话");
  });

  it("returns to pipeline when the tutor pipeline is not ready yet", () => {
    const before = link({ phase: "pipeline", transport: true, dataChannel: true });
    const dropped = notePeerState(before, "disconnected", "disconnected", 0);
    const back = notePeerState(dropped.state, "connected", "connected", 500);
    expect(back.action).toBe("recovered");
    expect(back.state.phase).toBe("pipeline");
    expect(linkIsCallable(back.state)).toBe(true);
  });

  it("recovers out of a failed phase as well", () => {
    const failed = noteRecoverFailed(liveLink(), "重连失败");
    const back = notePeerState(notePipelineReady(failed, true), "connected", "connected", 300);
    expect(back.action).toBe("recovered");
    expect(back.state.phase).toBe("live");
    expect(back.state.lastError).toBe("重连失败");
  });

  it("asks for a full reconnect on a second drop, once ICE restart is spent", () => {
    const first = notePeerState(liveLink(), "disconnected", "disconnected", 100);
    const second = notePeerState(first.state, "disconnected", "disconnected", 800);
    expect(second.action).toBe("reconnect");
    expect(second.state.phase).toBe("recovering");
    expect(second.state.reconnectAttempt).toBe(1);
    // 预算从第一次掉线算起，重试不重置计时。
    expect(second.state.recoverStartedAt).toBe(100);
  });

  it("asks for a reconnect when the peer connection failed", () => {
    const failed = notePeerState(liveLink(), "failed", "failed", 2_000);
    expect(failed.action).toBe("reconnect");
    expect(failed.state.phase).toBe("recovering");
    expect(failed.state.transport).toBe(false);
    expect(failed.state.recoverStartedAt).toBe(2_000);
    expect(failed.state.reconnectAttempt).toBe(1);
  });

  it("still asks for a reconnect when ICE restart was already tried and failed", () => {
    const restarted = notePeerState(liveLink(), "disconnected", "disconnected", 10).state;
    const failed = notePeerState(restarted, "failed", "failed", 900);
    expect(failed.action).toBe("reconnect");
    expect(failed.state.reconnectAttempt).toBe(1);
    expect(failed.state.recoverStartedAt).toBe(10);
  });

  it("treats a duplicate connected event as a no-op", () => {
    const next = notePeerState(liveLink(), "connected", "connected", 5_000);
    expect(next.action).toBe("none");
    expect(next.state.phase).toBe("live");
    expect(next.state.reconnectAttempt).toBe(0);
    expect(next.state.recoverStartedAt).toBeNull();
  });

  it("counts an ice completed state as connected", () => {
    const next = notePeerState(link({ phase: "media", signaling: true }), "connected", "completed", 10);
    expect(next.action).toBe("none");
    expect(next.state.transport).toBe(true);
    expect(next.state.phase).toBe("pipeline");
  });

  it("keeps new/connecting inert, so a fresh peer connection cannot look connected", () => {
    const before = link({ phase: "media", signaling: true });
    const next = notePeerState(before, "new", "new", 10);
    expect(next.action).toBe("none");
    expect(next.state).toBe(before);
    expect(linkIsCallable(next.state)).toBe(false);

    const checking = notePeerState(before, "connecting", "checking", 20);
    expect(checking.state).toBe(before);
    expect(linkIsCallable(checking.state)).toBe(false);
  });
});

describe("noteDataChannel", () => {
  it("does not promote the phase before the transport is connected", () => {
    const next = noteDataChannel(link({ phase: "media", signaling: true }), true);
    expect(next.dataChannel).toBe(true);
    expect(next.phase).toBe("media");
    expect(linkIsCallable(next)).toBe(false);
  });

  it("promotes to pipeline once the transport is connected", () => {
    const next = noteDataChannel(link({ phase: "media", signaling: true, transport: true }), true);
    expect(next.phase).toBe("pipeline");
    expect(linkIsCallable(next)).toBe(true);
  });

  it("drops callability when the only available channel closes", () => {
    const next = noteDataChannel(link({ phase: "pipeline", transport: true, dataChannel: true }), false);
    expect(next.dataChannel).toBe(false);
    expect(linkIsCallable(next)).toBe(false);
  });

  it("leaves recovering alone", () => {
    const recovering = link({ phase: "recovering", transport: true, recoverStartedAt: 1 });
    expect(noteDataChannel(recovering, true).phase).toBe("recovering");
  });
});

describe("notePipelineReady", () => {
  it("reaches live only with a connected transport", () => {
    const ready = notePipelineReady(link({ phase: "media", signaling: true, transport: true }), true);
    expect(ready.phase).toBe("live");
    expect(linkIsCallable(ready)).toBe(true);

    const notConnected = notePipelineReady(link({ phase: "media", signaling: true }), true);
    expect(notConnected.pipeline).toBe(true);
    expect(notConnected.phase).toBe("media");
    expect(linkIsCallable(notConnected)).toBe(false);
  });

  it("falls back to pipeline when a live pipeline goes away", () => {
    const gone = notePipelineReady(liveLink(), false);
    expect(gone.phase).toBe("pipeline");
    // 数据通道还开着，所以仍可通话，只是导师还没准备好。
    expect(linkIsCallable(gone)).toBe(true);

    const noChannel = notePipelineReady(
      link({ phase: "live", transport: true, pipeline: true }),
      false,
    );
    expect(noChannel.phase).toBe("pipeline");
    expect(linkIsCallable(noChannel)).toBe(false);
  });
});

describe("notePlayback", () => {
  it("does not call silence a blockage before the link is up", () => {
    expect(notePlayback(emptyLink(), false).phase).toBe("idle");
    expect(notePlayback(link({ phase: "media", signaling: true }), false).phase).toBe("media");
  });

  it("marks blocked audio when an established call stays silent", () => {
    const blocked = notePlayback(liveLink(), false);
    expect(blocked.playback).toBe(false);
    expect(blocked.phase).toBe("blocked-audio");
    expect(linkLabel(blocked)).toBe("浏览器阻止了声音播放，请点击恢复声音");
  });

  it("keeps recovering and failed phases intact", () => {
    const recovering = link({ phase: "recovering", transport: true, recoverStartedAt: 1 });
    expect(notePlayback(recovering, false).phase).toBe("recovering");
    expect(notePlayback(link({ phase: "failed" }), false).phase).toBe("failed");
  });

  it("restores the factual phase once audio is audible again", () => {
    const resumed = notePlayback(notePlayback(liveLink(), false), true);
    expect(resumed.playback).toBe(true);
    expect(resumed.phase).toBe("live");

    const notReady = notePlayback(
      notePlayback(link({ phase: "pipeline", transport: true, dataChannel: true }), false),
      true,
    );
    expect(notReady.phase).toBe("pipeline");
  });

  it("records playback without touching an otherwise healthy phase", () => {
    const next = notePlayback(liveLink(), true);
    expect(next.playback).toBe(true);
    expect(next.phase).toBe("live");
  });
});

describe("noteRecoverFailed", () => {
  it("degrades to failed with nothing left callable", () => {
    const failed = noteRecoverFailed(liveLink(), "重连超时");
    expect(failed.phase).toBe("failed");
    expect(failed.lastError).toBe("重连超时");
    expect(failed.transport).toBe(false);
    expect(failed.dataChannel).toBe(false);
    expect(failed.pipeline).toBe(false);
    expect(failed.playback).toBe(false);
    expect(linkIsCallable(failed)).toBe(false);
    expect(linkLabel(failed)).toBe("语音连接无法恢复，已降级为文字模式");
  });

  it("stops the recovery budget once the link has given up", () => {
    const recovering = link({ phase: "recovering", recoverStartedAt: 0 });
    expect(recoverBudgetExceeded(recovering, 30_000)).toBe(true);
    expect(recoverBudgetExceeded(noteRecoverFailed(recovering, "超时"), 30_000)).toBe(false);
  });
});

describe("linkLabel", () => {
  const labels: Record<VoiceLinkPhase, string> = {
    idle: "语音导师未连接",
    signaling: "正在接通语音导师…",
    media: "正在建立语音通道…",
    pipeline: "语音通道已建立，正在准备导师…",
    live: "语音导师已连接，请直接说话",
    recovering: "语音连接中断，正在重连…",
    "blocked-audio": "浏览器阻止了声音播放，请点击恢复声音",
    failed: "语音连接无法恢复，已降级为文字模式",
  };

  it("uses the frozen wording for every phase", () => {
    for (const [phase, text] of Object.entries(labels)) {
      expect(linkLabel(link({ phase: phase as VoiceLinkPhase }))).toBe(text);
    }
  });
});

describe("recoverBudgetExceeded", () => {
  it("never expires while no recovery is running", () => {
    expect(recoverBudgetExceeded(emptyLink(), 1_000_000)).toBe(false);
    expect(recoverBudgetExceeded(liveLink(), 1_000_000)).toBe(false);
  });

  it("expires exactly at the default 20s budget", () => {
    const recovering = link({ phase: "recovering", recoverStartedAt: 1_000 });
    expect(recoverBudgetExceeded(recovering, 20_999)).toBe(false);
    expect(recoverBudgetExceeded(recovering, 21_000)).toBe(true);
  });

  it("honours a custom budget", () => {
    const recovering = link({ phase: "recovering", recoverStartedAt: 1_000 });
    expect(recoverBudgetExceeded(recovering, 4_000, 3_000)).toBe(true);
    expect(recoverBudgetExceeded(recovering, 3_999, 3_000)).toBe(false);
  });

  it("accumulates across retries instead of restarting on each one", () => {
    const first = notePeerState(liveLink(), "disconnected", "disconnected", 1_000);
    const second = notePeerState(first.state, "disconnected", "disconnected", 9_000);
    const failed = notePeerState(second.state, "failed", "failed", 15_000);
    expect(failed.state.recoverStartedAt).toBe(1_000);
    expect(recoverBudgetExceeded(failed.state, 20_999)).toBe(false);
    expect(recoverBudgetExceeded(failed.state, 21_000)).toBe(true);
  });

  it("clears the clock after a successful recovery", () => {
    const dropped = notePeerState(liveLink(), "disconnected", "disconnected", 0);
    const back = notePeerState(dropped.state, "connected", "connected", 900);
    expect(recoverBudgetExceeded(back.state, 999_999)).toBe(false);
  });
});
