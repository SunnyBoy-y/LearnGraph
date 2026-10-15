import { describe, expect, it, vi } from "vitest";
import { coalesceTextDeltas, createStreamRenderQueue } from "@/features/chat/stream-render-queue";

const delta = (id: string, value: string) => ({
  type: "part.delta",
  message_id: "m",
  part: { id, type: "text", status: "streaming", content_delta: value },
});

describe("stream render queue", () => {
  it("joins only adjacent deltas for the same text part", () => {
    const control = { type: "answer.started" };
    const result = coalesceTextDeltas([delta("a", "one"), delta("a", " two"), control, delta("a", " three")]);
    expect(result).toHaveLength(3);
    expect((result[0].part as { content_delta: string }).content_delta).toBe("one two");
    expect(result[1]).toBe(control);
    expect((result[2].part as { content_delta: string }).content_delta).toBe(" three");
  });

  it("flushes terminal events immediately without playback tail", async () => {
    vi.useFakeTimers();
    const batches: Record<string, unknown>[][] = [];
    const queue = createStreamRenderQueue((batch) => batches.push(batch));
    queue.push(delta("a", "a"));
    queue.push(delta("a", "b"));
    queue.push({ type: "message.completed" });
    await queue.drain();
    expect(batches.flat().map((item) => item.type)).toEqual(["part.delta", "message.completed"]);
    expect((batches[0][0].part as { content_delta: string }).content_delta).toBe("ab");
    vi.useRealTimers();
  });

  it("coalesces a 700-token burst into one paint-sized update", () => {
    vi.useFakeTimers();
    const batches: Record<string, unknown>[][] = [];
    const queue = createStreamRenderQueue((batch) => batches.push(batch));
    for (let index = 0; index < 700; index += 1) queue.push(delta("a", "x"));
    vi.runOnlyPendingTimers();
    expect(batches).toHaveLength(1);
    expect(batches[0]).toHaveLength(1);
    expect((batches[0][0].part as { content_delta: string }).content_delta).toHaveLength(700);
    vi.useRealTimers();
  });

  it("does not hold a structured card behind text batching", () => {
    vi.useFakeTimers();
    const batches: Record<string, unknown>[][] = [];
    const queue = createStreamRenderQueue((batch) => batches.push(batch));
    queue.push(delta("a", "answer"));
    const card = {
      type: "part.delta",
      part: { id: "chart-1", type: "chart", status: "completed", data: {} },
    };
    queue.push(card);
    expect(batches).toHaveLength(2);
    expect(batches[0][0].part).toMatchObject({ id: "a" });
    expect(batches[1][0]).toBe(card);
    vi.useRealTimers();
  });

  it("keeps reasoning delivery on its frame cadence when text is pending", () => {
    vi.useFakeTimers();
    const batches: Record<string, unknown>[][] = [];
    const queue = createStreamRenderQueue((batch) => batches.push(batch));
    queue.push(delta("a", "answer"));
    queue.push({
      type: "part.delta",
      part: { id: "r", type: "reasoning_summary", status: "streaming", content_delta: "thinking" },
    });
    vi.advanceTimersByTime(16);
    expect(batches.flat().some((item) => (item.part as { id?: string })?.id === "r")).toBe(true);
    vi.useRealTimers();
  });
});
