import { describe, expect, it, vi } from "vitest";

const fetcher = vi.hoisted(() => vi.fn<typeof fetch>());
vi.mock("./client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./client")>();
  return { ...actual, apiClient: new actual.ApiClient({ fetch: fetcher }) };
});
import { downloadFileForPreview, pollParseJob, uploadFile } from "./files";
import { networkQualityStore } from "@/lib/network-quality";

describe("complete file previews", () => {
  it("keeps the trailing directory of a file larger than 16 MiB", async () => {
    const content = "x".repeat(16 * 1024 * 1024) + "ZIP-CENTRAL-DIRECTORY";
    fetcher.mockImplementation(async (_url, init) => {
      const range = new Headers(init?.headers).get("Range");
      return new Response(range ? content.slice(0, 16 * 1024 * 1024) : content, { status: range ? 206 : 200 });
    });
    const controller = new AbortController();
    let observedSignal: AbortSignal | null | undefined;
    fetcher.mockImplementation(async (_url, init) => {
      observedSignal = init?.signal;
      const range = new Headers(init?.headers).get("Range");
      return new Response(range ? content.slice(0, 16 * 1024 * 1024) : content, { status: range ? 206 : 200 });
    });
    const result = await downloadFileForPreview("large-book", content.length, { signal: controller.signal });
    expect(result.size).toBe(content.length);
    expect(await result.slice(-21).text()).toContain("ZIP-CENTRAL-DIRECTORY");
    expect(observedSignal).toBeDefined();
    expect(observedSignal?.aborted).toBe(false);

    // Caller abort propagates immediately to an in-flight download signal.
    const abortController = new AbortController();
    let inFlightSignal: AbortSignal | null | undefined;
    fetcher.mockImplementationOnce(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          inFlightSignal = init?.signal;
          init?.signal?.addEventListener("abort", () => {
            reject(init.signal?.reason ?? new DOMException("Aborted", "AbortError"));
          });
        }),
    );
    const pending = downloadFileForPreview("large-book", content.length, { signal: abortController.signal });
    await Promise.resolve();
    abortController.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(inFlightSignal?.aborted).toBe(true);
  });
});

describe("chunked resumable upload and resilient parse polling", () => {
  it("uploads in chunks, retries transient chunk errors, and reports progress", async () => {
    networkQualityStore.reset();
    localStorage.clear();
    fetcher.mockReset();

    const receivedChunks = new Map<number, number>();
    let chunk0Attempts = 0;

    fetcher.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/files/uploads/init")) {
        return new Response(
          JSON.stringify({
            upload_id: "up-1",
            filename: "book.pdf",
            total_size: 160 * 1024,
            chunk_size: 64 * 1024,
            total_chunks: 3,
            uploaded_chunks: [],
            already_exists: false,
            existing_file: null,
          }),
          { status: 201 },
        );
      }
      const chunkMatch = url.match(/\/files\/uploads\/up-1\/chunks\/(\d+)$/);
      if (chunkMatch) {
        const idx = Number(chunkMatch[1]);
        if (idx === 0 && chunk0Attempts === 0) {
          chunk0Attempts += 1;
          return new Response("gateway error", { status: 503 });
        }
        const blob = init?.body as Blob;
        receivedChunks.set(idx, blob.size);
        return new Response(
          JSON.stringify({
            upload_id: "up-1",
            chunk_index: idx,
            size_bytes: blob.size,
            uploaded_chunks: [...receivedChunks.keys()],
          }),
          { status: 200 },
        );
      }
      if (url.endsWith("/files/uploads/up-1/complete")) {
        return new Response(
          JSON.stringify({
            id: "file-1",
            workspace_id: "ws",
            original_name: "book.pdf",
            mime_type: "application/pdf",
            size_bytes: 160 * 1024,
            sha256: "a".repeat(64),
            storage_status: "stored",
            parse_capability: "built_in_document",
            parse_status: "not_requested",
            parser_name: null,
            parser_version: null,
            error_message: null,
            created_at: new Date().toISOString(),
          }),
          { status: 201 },
        );
      }
      return new Response("not found", { status: 404 });
    });

    const file = new File([new Uint8Array(160 * 1024)], "book.pdf", { type: "application/pdf" });
    const percents: number[] = [];
    const saved = await uploadFile(file, {
      forceChunked: true,
      chunkSize: 64 * 1024,
      onProgress: (p) => percents.push(p.percent),
    });

    expect(saved.id).toBe("file-1");
    expect([...receivedChunks.keys()].sort()).toEqual([0, 1, 2]);
    expect(percents.at(-1)).toBe(100);
  });

  it("tolerates transient errors while polling a document parse job", async () => {
    networkQualityStore.reset();
    fetcher.mockReset();
    let polls = 0;
    fetcher.mockImplementation(async () => {
      polls += 1;
      if (polls === 1) {
        throw new TypeError("Failed to fetch");
      }
      return new Response(
        JSON.stringify({
          id: "job-1",
          status: "completed",
        }),
        { status: 200 },
      );
    });

    const done = await pollParseJob(
      { id: "job-1", status: "running" } as Parameters<typeof pollParseJob>[0],
      { intervalMs: 10, timeoutMs: 5_000 },
    );
    expect(done.status).toBe("completed");
    expect(polls).toBeGreaterThanOrEqual(2);
  });
});
