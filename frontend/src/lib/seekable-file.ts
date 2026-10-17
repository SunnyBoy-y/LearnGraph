import { readFileRange } from "@/api/files";
import { readDocumentBytes } from "./document-text";
import { readByteRange } from "./byte-range";

export const PREVIEW_BUFFER_LIMIT = 16 * 1024 * 1024;
export const PREVIEW_RANGE_BYTES = 256 * 1024;

export interface SeekableFile {
  size: number;
  read: (begin: number, end: number, signal?: AbortSignal) => Promise<Uint8Array>;
}

export function blobFileSource(blob: Blob): SeekableFile {
  return {
    size: blob.size,
    async read(begin, end, signal) {
      validateRead(begin, end, blob.size);
      const result = new Uint8Array(end - begin);
      for (let offset = begin; offset < end; offset += PREVIEW_RANGE_BYTES) {
        if (signal?.aborted) throw new DOMException("已取消文件读取", "AbortError");
        const part = new Uint8Array(await readDocumentBytes(blob.slice(offset, Math.min(end, offset + PREVIEW_RANGE_BYTES))));
        result.set(part, offset - begin);
      }
      return result;
    },
  };
}

function validateRead(begin: number, end: number, size: number) {
  if (!Number.isSafeInteger(begin) || !Number.isSafeInteger(end) || begin < 0 || end > size || end < begin || end - begin > 8 * 1024 * 1024) throw new Error("文件分段超过读取限制");
}

export async function remoteFileSource(fileId: string, signal: AbortSignal, officePreview = false): Promise<SeekableFile> {
  return rangedSource((begin, end, rangeSignal) => readFileRange(fileId, begin, end, { signal: rangeSignal }, officePreview), signal);
}

/** URL-only pending attachments still use ranges, including disk-backed blob URLs. */
export async function urlFileSource(url: string, signal: AbortSignal): Promise<SeekableFile> {
  if (url.startsWith("data:")) {
    if (url.length > PREVIEW_BUFFER_LIMIT) throw new Error("内嵌附件超过读取限制，请重新选择原文件。");
    return blobFileSource(await (await fetch(url, { signal })).blob());
  }
  return rangedSource(async (begin, end, rangeSignal) => readByteRange(await fetch(url, {
    signal: rangeSignal, headers: { Range: `bytes=${begin}-${end - 1}` },
  }), begin, end), signal);
}

async function rangedSource(load: (begin: number, end: number, signal: AbortSignal) => Promise<{ data: Uint8Array; size: number }>, signal: AbortSignal): Promise<SeekableFile> {
  const initial = await load(0, 64 * 1024, signal);
  return {
    size: initial.size,
    async read(begin, end, requestSignal) {
      if (signal.aborted || requestSignal?.aborted) throw new DOMException("已取消文件读取", "AbortError");
      validateRead(begin, end, initial.size);
      if (end <= initial.data.length) return initial.data.slice(begin, end);
      const result = new Uint8Array(end - begin);
      const controller = new AbortController();
      const abort = () => controller.abort();
      signal.addEventListener("abort", abort, { once: true });
      requestSignal?.addEventListener("abort", abort, { once: true });
      try {
        for (let offset = begin; offset < end; offset += PREVIEW_RANGE_BYTES) {
          const nextEnd = Math.min(end, offset + PREVIEW_RANGE_BYTES);
          const response = offset < initial.data.length && nextEnd <= initial.data.length
            ? { data: initial.data.subarray(offset, nextEnd), size: initial.size }
            : await load(offset, nextEnd, controller.signal);
          if (response.size !== initial.size) throw new Error("文件大小发生变化，请重新打开预览");
          const part = response.data;
          if (part.length !== nextEnd - offset) throw new Error("文件分段长度发生变化，请重新打开预览");
          result.set(part, offset - begin);
        }
        return result;
      } finally {
        signal.removeEventListener("abort", abort);
        requestSignal?.removeEventListener("abort", abort);
      }
    },
  };
}
