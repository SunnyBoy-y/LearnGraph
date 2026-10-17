import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { fileStreamUrl } from "@/api/files";
import { useFileStreamUrl } from "./use-file-stream-url";

vi.mock("@/api/files", () => ({ fileStreamUrl: vi.fn() }));

describe("native file sources", () => {
  it("clears the previous file immediately and ignores a late ticket after switching", async () => {
    let resolveA!: (source: string) => void;
    let signalA: AbortSignal | undefined;
    vi.mocked(fileStreamUrl).mockImplementation((id, options) => {
      if (id === "B") return Promise.resolve("/B/stream?ticket=b");
      signalA = options?.signal;
      return new Promise((resolve) => { resolveA = resolve; });
    });
    const { result, rerender } = renderHook(({ id }) => useFileStreamUrl(id), { initialProps: { id: "A" } });
    rerender({ id: "B" });
    expect(signalA?.aborted).toBe(true);
    expect(result.current.source).toBe("");
    await waitFor(() => expect(result.current.source).toBe("/B/stream?ticket=b"));
    await act(async () => resolveA("/A/stream?ticket=a"));
    expect(result.current.source).toBe("/B/stream?ticket=b");
    rerender({ id: "" });
    expect(result.current.source).toBe("");
  });

  it("obtains a new source for an updated revision without retaining file bytes", async () => {
    vi.mocked(fileStreamUrl).mockResolvedValueOnce("/image/stream?ticket=old").mockResolvedValueOnce("/image/stream?ticket=new");
    const { result, rerender } = renderHook(({ revision }) => useFileStreamUrl("image", revision), { initialProps: { revision: "1" } });
    await waitFor(() => expect(result.current.source).toContain("old"));
    rerender({ revision: "2" });
    expect(result.current.source).toBe("");
    await waitFor(() => expect(result.current.source).toContain("new"));
  });
});
