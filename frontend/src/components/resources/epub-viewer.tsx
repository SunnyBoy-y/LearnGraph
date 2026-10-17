import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { openEpub, type EpubBook } from "@/lib/epub";
import type { SeekableFile } from "@/lib/seekable-file";
import { Button } from "@/components/ui/button";
import { SandboxedHtmlFrame } from "./sandboxed-html-frame";

export function EpubViewer({ source, filename }: { source: SeekableFile; filename: string }) {
  const [book, setBook] = useState<EpubBook>();
  const [index, setIndex] = useState(0);
  const [chapter, setChapter] = useState<{ index: number; html: string }>();
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    let opened: EpubBook | undefined;
    void openEpub(source).then((next) => {
      opened = next;
      if (cancelled) void next.close();
      else setBook(next);
    }).catch((reason: unknown) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "EPUB 加载失败"); });
    return () => { cancelled = true; void opened?.close(); };
  }, [source]);
  useEffect(() => {
    if (!book) return;
    let cancelled = false;
    let dispose: (() => void) | undefined;
    setError("");
    setChapter(undefined);
    void book.chapter(index).then((next) => {
      dispose = next.dispose;
      if (cancelled) next.dispose();
      else setChapter({ index, html: next.html });
    }).catch((reason: unknown) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "EPUB 章节加载失败"); });
    return () => { cancelled = true; dispose?.(); };
  }, [book, index]);
  return (
    <div className="flex min-h-[36rem] flex-col" data-preview-kind="epub">
      <div className="file-preview-toolbar">
        <Button aria-label="上一章" disabled={!book || index === 0} onClick={() => setIndex((value) => value - 1)} size="icon-sm" variant="ghost"><ChevronLeft /></Button>
        <select aria-label="EPUB 章节目录" className="min-w-0 flex-1 rounded border bg-background p-1 text-sm" value={index} onChange={(event) => setIndex(Number(event.target.value))} disabled={!book}>
          {book?.chapters.map((item, ordinal) => <option value={ordinal} key={`${item.path}:${ordinal}`}>{item.title}</option>)}
        </select>
        <span className="text-xs tabular-nums">{book ? `${index + 1} / ${book.chapters.length}` : ""}</span>
        <Button aria-label="下一章" disabled={!book || index >= book.chapters.length - 1} onClick={() => setIndex((value) => value + 1)} size="icon-sm" variant="ghost"><ChevronRight /></Button>
      </div>
      {error ? <p className="p-6 text-sm text-destructive" role="alert">{error}</p> : chapter?.index === index ? (
        <SandboxedHtmlFrame className="min-h-[36rem] w-full flex-1 border-0 bg-white" html={chapter.html} title={`${book?.title ?? filename} · ${book?.chapters[index].title ?? ""}`} offline scripts={false} />
      ) : <p className="p-6 text-sm text-muted-foreground" role="status">正在读取 EPUB 章节…</p>}
    </div>
  );
}
