import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { PREVIEW_RANGE_BYTES, type SeekableFile } from "@/lib/seekable-file";
import { Button } from "@/components/ui/button";

export function RangeTextViewer({ source }: { source: SeekableFile }) {
  const [page, setPage] = useState(0);
  const [content, setContent] = useState<{ page: number; text?: string; error?: string }>();
  const pages = Math.max(1, Math.ceil(source.size / PREVIEW_RANGE_BYTES));
  useEffect(() => {
    const controller = new AbortController();
    // Adjacent fragments overlap by four bytes so multibyte characters at a
    // boundary remain readable without retaining earlier fragments in memory.
    const begin = Math.max(0, page * PREVIEW_RANGE_BYTES - 4);
    const end = Math.min(source.size, (page + 1) * PREVIEW_RANGE_BYTES + 4);
    void Promise.all([source.read(0, Math.min(64, source.size), controller.signal), source.read(begin, end, controller.signal)]).then(([header, bytes]) => {
      let start = 0;
      let text: string;
      if (header[0] === 0xff && header[1] === 0xfe || header[0] === 0xfe && header[1] === 0xff) {
        text = new TextDecoder(header[0] === 0xff ? "utf-16le" : "utf-16be").decode(bytes, { stream: true });
      } else {
        while (start < 4 && bytes[start] >= 0x80 && bytes[start] < 0xc0) start++;
        try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes.subarray(start), { stream: true }); }
        catch { text = new TextDecoder("gb18030").decode(bytes, { stream: true }); }
      }
      if (!controller.signal.aborted) setContent({ page, text });
    }).catch((reason: unknown) => { if (!controller.signal.aborted) setContent({ page, error: reason instanceof Error ? reason.message : "文本片段读取失败" }); });
    return () => controller.abort();
  }, [page, source]);
  const current = content?.page === page ? content : undefined;
  return (
    <div>
      <div className="file-preview-toolbar">
        <Button aria-label="上一段文本" disabled={!page} onClick={() => setPage((value) => value - 1)} size="icon-sm" variant="ghost"><ChevronLeft /></Button>
        <span className="text-xs">分段阅读 · {page + 1} / {pages}</span>
        <Button aria-label="下一段文本" disabled={page + 1 >= pages} onClick={() => setPage((value) => value + 1)} size="icon-sm" variant="ghost"><ChevronRight /></Button>
      </div>
      {current?.error ? <p className="p-6 text-sm text-destructive" role="alert">{current.error}</p> : current ? (
        <pre className="max-h-[70svh] overflow-auto p-4 text-xs leading-5"><code>{current.text}</code></pre>
      ) : <p className="p-6 text-sm text-muted-foreground" role="status">正在读取文本片段…</p>}
    </div>
  );
}
