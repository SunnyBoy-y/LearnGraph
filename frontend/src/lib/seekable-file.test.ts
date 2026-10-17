import { describe, expect, it, vi } from "vitest";
import { readFileRange } from "@/api/files";
import { blobFileSource, remoteFileSource, PREVIEW_RANGE_BYTES } from "./seekable-file";

vi.mock("@/api/files", () => ({ readFileRange: vi.fn() }));

describe("seekable preview sources", () => {
  it("only reads the requested slice of a local file", async () => {
    const slice = vi.fn(() => ({ arrayBuffer: async () => new Uint8Array([1, 2]).buffer }));
    const source = blobFileSource({ size: 512 * 1024 * 1024, slice } as unknown as Blob);
    expect([...await source.read(1024, 1026)]).toEqual([1, 2]);
    expect(slice).toHaveBeenCalledExactlyOnceWith(1024, 1026);
  });
  it("uses small ranges, reuses the prefix and aborts with the reader", async () => {
    vi.mocked(readFileRange).mockImplementation(async (_id, begin, end) => ({ data: new Uint8Array(end - begin), size: 512 * 1024 * 1024 }));
    const controller = new AbortController();
    const source = await remoteFileSource("big", controller.signal);
    await source.read(0, 64);
    expect(readFileRange).toHaveBeenCalledTimes(1);
    await source.read(1024 * 1024, 2 * 1024 * 1024);
    expect(vi.mocked(readFileRange).mock.calls.every(([, begin, end]) => end - begin <= PREVIEW_RANGE_BYTES)).toBe(true);
    controller.abort();
    await expect(source.read(0, 64)).rejects.toThrow("已取消");
  });
});
