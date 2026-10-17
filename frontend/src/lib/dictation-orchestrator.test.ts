import { beforeEach, describe, expect, it, vi } from "vitest";
import { startDictationOrchestrator } from "./dictation-orchestrator";
import { startProviderDictation } from "./provider-dictation";
import { startRealtimeDictation } from "./realtime-dictation";

vi.mock("./provider-dictation", () => ({ providerDictationSupported: () => true, startProviderDictation: vi.fn() }));
vi.mock("./realtime-dictation", () => ({ realtimeDictationSupported: () => true, startRealtimeDictation: vi.fn() }));

describe("dictation result drain", () => {
  beforeEach(() => vi.clearAllMocks());

  it.each(["segmented", "realtime"])("delivers the last %s result while stop is draining", async (channel) => {
    const onFinal = vi.fn();
    const onPartial = vi.fn();
    const abort = vi.fn();
    vi.mocked(startProviderDictation).mockImplementation(async (options) => ({
      stop: async () => { await Promise.resolve(); options.onSegmentText("最后一段"); }, abort,
    }));
    vi.mocked(startRealtimeDictation).mockImplementation(async (options) => ({
      stop: async () => { await Promise.resolve(); options.onPartial("最后"); options.onFinal("最后一段"); }, abort,
    }));
    const handle = await startDictationOrchestrator({
      realtime: channel === "realtime" ? { providerId: "asr", modelId: "paraformer-realtime-v2" } : undefined,
      transcribeSegment: async () => "", onPartial, onFinal, onDegrade: vi.fn(), onFatal: vi.fn(),
    });
    await handle.stop();
    expect(onFinal).toHaveBeenCalledExactlyOnceWith("最后一段");
    expect(abort).not.toHaveBeenCalled();
    if (channel === "realtime") expect(onPartial).toHaveBeenCalledWith("最后");
  });

  it("discards results arriving after cancel, including a stop already in flight", async () => {
    let deliver: (() => void) | undefined;
    const onFinal = vi.fn();
    vi.mocked(startProviderDictation).mockImplementation(async (options) => ({
      stop: () => new Promise<void>((resolve) => { deliver = () => { options.onSegmentText("取消后结果"); resolve(); }; }),
      abort: vi.fn(),
    }));
    const handle = await startDictationOrchestrator({ transcribeSegment: async () => "", onPartial: vi.fn(), onFinal, onDegrade: vi.fn(), onFatal: vi.fn() });
    const stopping = handle.stop();
    handle.abort();
    deliver!();
    await stopping;
    expect(onFinal).not.toHaveBeenCalled();
  });
});
