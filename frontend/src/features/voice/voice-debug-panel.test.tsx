/**
 * 右侧栏「语音延迟」栏目（语音调试面板）的渲染单测。
 *
 * 面板本身不判显隐（显隐由设置开关 + workspace-shell 的条件渲染决定），这里只验证
 * 它把 store 里的时间点读成什么样：总耗时、各环节、通道来源、空态。
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("VoiceLatencyRail", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    cleanup();
  });

  it("没有语音数据时是空态，不给任何数字", async () => {
    const { VoiceLatencyRail } = await import("./voice-debug-panel");
    render(<VoiceLatencyRail />);

    expect(screen.getByLabelText("语音调试面板")).toBeTruthy();
    expect(screen.getByText(/发起一次全双工语音后/)).toBeTruthy();
    expect(screen.getByText("未连接")).toBeTruthy();
    expect(screen.queryByLabelText("语音结束到语音开始耗时")).toBeNull();
  });

  it("有数据时显示总数、各环节与来源", async () => {
    const store = await import("./voice-latency-store");
    store.markVoiceLatency({ at: 1000, source: "ledger", stage: "userStarted" });
    store.markVoiceLatency({ at: 2200, source: "rtvi", stage: "userDone" });
    store.markVoiceLatency({
      at: 2380,
      detail: "今天天气怎么样",
      source: "ledger",
      stage: "asrFinal",
    });
    store.markVoiceLatency({ at: 3500, source: "ledger", stage: "botSpeaking" });
    store.markVoiceLatency({
      at: 4400,
      detail: "今天天气很好。",
      source: "ledger",
      stage: "sentenceEnded",
    });

    const { VoiceLatencyRail } = await import("./voice-debug-panel");
    const { container } = render(<VoiceLatencyRail />);

    expect(screen.getByText("语音结束 → 语音开始")).toBeTruthy();
    expect(screen.getByLabelText("语音结束到语音开始耗时").textContent).toBe("1300 ms");
    expect(screen.getByText(/说话 1200 ms/)).toBeTruthy();
    expect(screen.getByText(/说完锚点：实时通道/)).toBeTruthy();
    expect(screen.getByText(/出声锚点：账本事件/)).toBeTruthy();

    const speaking = container.querySelector('[data-stage="botSpeaking"]');
    expect(speaking?.textContent).toContain("导师出声");
    expect(speaking?.textContent).toContain("2500 ms");
    expect(speaking?.textContent).toContain("+1120 ms");
    expect(speaking?.textContent).toContain("账本事件");

    // 还没到的环节显示等待，而不是编一个 0。
    const done = container.querySelector('[data-stage="answerDone"]');
    expect(done?.textContent).toContain("等待…");

    expect(screen.getByText(/今天天气怎么样/)).toBeTruthy();
    expect(screen.getByText(/今天天气很好。/)).toBeTruthy();
  });

  it("缺「用户说完」时说明为什么没有总量", async () => {
    const store = await import("./voice-latency-store");
    store.markVoiceLatency({ at: 100, source: "ledger", stage: "userStarted" });
    store.markVoiceLatency({ at: 500, source: "ledger", stage: "botSpeaking" });

    const { VoiceLatencyRail } = await import("./voice-debug-panel");
    render(<VoiceLatencyRail />);

    expect(screen.getByLabelText("语音结束到语音开始耗时").textContent).toBe("未测得");
    expect(screen.getByText(/没收到「用户说完」信号/)).toBeTruthy();
  });

  it("身份还没到的记号按身份寄存，并说明它没被算进任何一轮", async () => {
    const store = await import("./voice-latency-store");
    // 身份未知的输出侧记号：没有活跃轮、也没有可归位的回合 → 先寄存，不丢弃。
    store.markVoiceLatency({
      at: 100,
      source: "ledger",
      stage: "sentenceQueued",
      turnId: "turn_unknown",
    });

    const { VoiceLatencyRail } = await import("./voice-debug-panel");
    const { container } = render(<VoiceLatencyRail />);

    expect(container.querySelector('[data-role="dropped"]')?.textContent).toContain(
      "有 1 条信号正按回合身份寄存",
    );
    expect(container.querySelector('[data-role="dropped"]')?.textContent).not.toContain("已丢弃");
    expect(screen.getByText(/发起一次全双工语音后/)).toBeTruthy();
  });

  it("寄存超期仍没人认领：显示为已丢弃（面板自证没把它们算进任何一轮）", async () => {
    const store = await import("./voice-latency-store");
    const latency = await import("./voice-latency");
    store.markVoiceLatency({
      at: 100,
      source: "ledger",
      stage: "sentenceQueued",
      turnId: "turn_unknown",
    });
    // 时间往前走过一个寄存窗口，再来一条无关信号把超期扫描触发掉。
    store.markVoiceLatency({
      at: 100 + latency.VOICE_LATENCY_HOLD_MS + 1000,
      source: "ledger",
      stage: "userStarted",
    });

    const { VoiceLatencyRail } = await import("./voice-debug-panel");
    const { container } = render(<VoiceLatencyRail />);

    expect(container.querySelector('[data-role="dropped"]')?.textContent).toContain(
      "已丢弃 1 条无主信号",
    );
  });

  it("收尾前那一轮标「进行中」：把「还没收尾」与「信号丢了」分开说", async () => {
    const store = await import("./voice-latency-store");
    store.markVoiceLatency({ at: 0, source: "ledger", stage: "userStarted" });
    store.markVoiceLatency({ at: 1200, source: "rtvi", stage: "userDone" });

    const { VoiceLatencyRail } = await import("./voice-debug-panel");
    render(<VoiceLatencyRail />);

    expect(screen.getByText(/· 进行中/)).toBeTruthy();
    expect(screen.getByText(/服务端尚未收尾/)).toBeTruthy();
  });

  it("清空按钮把面板复位", async () => {
    const store = await import("./voice-latency-store");
    store.markVoiceLatency({ at: 1000, source: "rtvi", stage: "userDone" });
    store.markVoiceLatency({ at: 2000, source: "ledger", stage: "botSpeaking" });

    const { VoiceLatencyRail } = await import("./voice-debug-panel");
    render(<VoiceLatencyRail />);
    expect(screen.getByLabelText("语音结束到语音开始耗时").textContent).toBe("1000 ms");

    fireEvent.click(screen.getByRole("button", { name: "清空" }));
    expect(screen.getByText(/发起一次全双工语音后/)).toBeTruthy();
  });
});
