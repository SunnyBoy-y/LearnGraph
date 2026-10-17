import { describe, expect, it, vi } from "vitest";

const fetcher = vi.hoisted(() => vi.fn<typeof fetch>());
vi.mock("./client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./client")>();
  return { ...actual, apiClient: new actual.ApiClient({ fetch: fetcher }) };
});
import { downloadFileForPreview } from "./files";

describe("complete file previews", () => {
  it("keeps the trailing directory of a file larger than 16 MiB", async () => {
    const content = "x".repeat(16 * 1024 * 1024) + "ZIP-CENTRAL-DIRECTORY";
    fetcher.mockImplementation(async (_url, init) => {
      const range = new Headers(init?.headers).get("Range");
      return new Response(range ? content.slice(0, 16 * 1024 * 1024) : content, { status: range ? 206 : 200 });
    });
    const signal = new AbortController().signal;
    const result = await downloadFileForPreview("large-book", content.length, { signal });
    expect(result.size).toBe(content.length);
    expect(await result.slice(-21).text()).toContain("ZIP-CENTRAL-DIRECTORY");
    expect(fetcher.mock.calls[0]?.[1]?.signal).toBe(signal);
  });
});
