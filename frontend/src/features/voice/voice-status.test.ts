import { describe, expect, it } from "vitest";

import { voiceModelPinText, voiceStatusText } from "./voice-status";

describe("voice model pin wording", () => {
  it("says nothing when no switch is in play", () => {
    expect(voiceModelPinText(null)).toBe("");
  });

  it("describes a pending switch with both names", () => {
    const text = voiceModelPinText({
      requestedModelId: "new-model",
      effectiveModelId: "old-model",
      repointed: false,
      certainty: "next_connection",
      epoch: 2,
      at: "2026-09-18T00:00:00.000Z",
    });
    expect(text).toContain("本次通话仍在使用「old-model」");
    expect(text).toContain("「new-model」将在下次接通后生效");
  });

  it("never prints a placeholder model name for an unnamed request", () => {
    // 「新模型」曾经被当成模型名渲染出来（上下文快照被误读成切换请求的产物），
    // 用户会以为真有一个模型叫这个名字。
    const text = voiceModelPinText({
      requestedModelId: null,
      effectiveModelId: "old-model",
      repointed: false,
      certainty: "next_connection",
      epoch: 2,
      at: "2026-09-18T00:00:00.000Z",
    });
    expect(text).toContain("本次通话仍在使用「old-model」");
    expect(text).not.toContain("新模型");
    expect(text).not.toContain("「null」");
  });

  it("reports a confirmed switch as done, not as pending", () => {
    const text = voiceModelPinText({
      requestedModelId: "new-model",
      effectiveModelId: "new-model",
      repointed: true,
      certainty: "repointed",
      epoch: 3,
      at: "2026-09-18T00:00:00.000Z",
    });
    expect(text).toContain("已切换到「new-model」");
    expect(text).not.toContain("下次接通后生效");
  });

  it("still words a confirmed switch whose model cannot be named", () => {
    const text = voiceModelPinText({
      requestedModelId: null,
      effectiveModelId: null,
      repointed: true,
      certainty: "repointed",
      epoch: 3,
      at: "2026-09-18T00:00:00.000Z",
    });
    expect(text).toBe(" · 已切换模型，下一轮生效");
  });
});

describe("voice status line", () => {
  it("never shows the connection state and the call state as each other", () => {
    expect(voiceStatusText("connecting", "ready")).toContain("接通");
    expect(voiceStatusText("connected", "listening")).toBe("正在聆听，请直接说话");
    expect(voiceStatusText("connected", "speaking")).toContain("插话");
    expect(voiceStatusText("error", "closed", "麦克风被拒绝")).toBe("麦克风被拒绝");
  });
});
