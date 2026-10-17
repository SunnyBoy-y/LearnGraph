import { useEffect, useId, useRef, useState } from "react";
import { FileText, Globe } from "lucide-react";
import { citationHttpUrl } from "@/lib/citation-url";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import "./citation.css";

export type CitationSource = {
  /** Original citation identity, independent of its display position. */
  id: string;
  url: string;
  title: string;
  siteName?: string;
  faviconUrl?: string;
  publishedAt?: string;
  kind?: "web" | "document";
  locator?: string;
};

function sourceUrl(source: CitationSource) {
  // Only application-generated document routes may use relative URLs.
  if (source.kind === "document" && /^\/w\/[^/]+\/documents\/[^/]+/.test(source.url)) {
    return source.url;
  }
  return citationHttpUrl(source.url);
}

function sourceName(source: CitationSource) {
  if (source.siteName?.trim()) return source.siteName.trim();
  if (source.kind === "document") return "文档";
  const url = citationHttpUrl(source.url);
  return url ? new URL(url).hostname.replace(/^www\./, "") : "来源不可用";
}

function SourceFavicon({ source }: { source: CitationSource }) {
  const url = citationHttpUrl(source.faviconUrl);
  const [failedUrl, setFailedUrl] = useState<string>();
  return (
    <span aria-hidden="true" className="citation-favicon">
      {url && failedUrl !== url ? (
        <img alt="" src={url} referrerPolicy="no-referrer" onError={() => setFailedUrl(url)} />
      ) : source.kind === "document" ? (
        <FileText />
      ) : (
        <Globe />
      )}
    </span>
  );
}

function sourceDate(value: string | undefined) {
  if (!value) return undefined;
  // Do not interpret year-only or ambiguous numeric metadata as a date.
  if (!/^\d{4}-\d{2}-\d{2}(?:$|T|\s)/.test(value)) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? undefined
    : date.toLocaleDateString("en-US", {
        year: "numeric", month: "short", day: "numeric", timeZone: "UTC",
      });
}

function CitationSourceItem({ source }: { source: CitationSource }) {
  const href = sourceUrl(source);
  const date = sourceDate(source.publishedAt);
  const body = (
    <>
      <span className="citation-source-header">
        <SourceFavicon source={source} />
        <span>{sourceName(source)}</span>
      </span>
      <span className="citation-source-title">{source.title}</span>
      {source.locator ? <span className="citation-source-meta">{source.locator}</span> : null}
      {date ? <time className="citation-source-meta" dateTime={source.publishedAt}>{date}</time> : null}
    </>
  );
  return href ? (
    <a className="citation-source-item" href={href} target="_blank" rel="noopener noreferrer"
      data-citation-id={source.id} onClick={(event) => event.stopPropagation()}>
      {body}
    </a>
  ) : (
    <div className="citation-source-item" data-citation-id={source.id}>{body}</div>
  );
}

/** A source group shared by web, RAG and document citations. */
export function Citation({ sources }: { sources: CitationSource[] }) {
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const contentRef = useRef<HTMLDivElement>(null);
  const pointerOpen = useRef(false);
  const cardId = useId();
  useEffect(() => () => clearTimeout(timer.current), []);

  if (!sources.length) return null;
  const first = sources[0];
  const name = sourceName(first);
  const changeOpen = (value: boolean) => {
    clearTimeout(timer.current);
    setOpen(value);
  };
  const scheduleClose = (ignoreFocus = false) => {
    clearTimeout(timer.current);
    if (!ignoreFocus && contentRef.current?.contains(document.activeElement)) return;
    timer.current = setTimeout(() => setOpen(false), 200);
  };

  return (
    <Popover open={open} onOpenChange={changeOpen}>
      <PopoverTrigger asChild>
        <button type="button" className="citation-pill"
          aria-label={`查看引用来源：${name}${sources.length > 1 ? `，共 ${sources.length} 个来源` : ""}`}
          aria-controls={open ? cardId : undefined}
          data-citation-ids={sources.map((source) => source.id).join(",")}
          onPointerEnter={(event) => {
            if (event.pointerType === "touch") return;
            clearTimeout(timer.current);
            pointerOpen.current = true;
            timer.current = setTimeout(() => setOpen(true), 120);
          }}
          onPointerLeave={(event) => { if (event.pointerType !== "touch") scheduleClose(); }}
          onPointerDown={() => { pointerOpen.current = true; clearTimeout(timer.current); }}
          onKeyDown={() => { pointerOpen.current = false; clearTimeout(timer.current); }}
          onClick={(event) => { clearTimeout(timer.current); event.stopPropagation(); }}>
          <SourceFavicon source={first} />
          <span className="citation-pill-name">{name}</span>
          {sources.length > 1 ? <span className="citation-pill-count">· +{sources.length - 1}</span> : null}
        </button>
      </PopoverTrigger>
      <PopoverContent id={cardId} ref={contentRef} aria-label="引用来源"
        className="citation-hover-card" side="bottom" align="start" sideOffset={8}
        collisionPadding={16} avoidCollisions sticky="always"
        onOpenAutoFocus={(event) => { if (pointerOpen.current) event.preventDefault(); }}
        onCloseAutoFocus={(event) => event.preventDefault()}
        onPointerEnter={() => { clearTimeout(timer.current); }}
        onPointerLeave={(event) => { if (event.pointerType !== "touch") scheduleClose(); }}
        onFocusCapture={() => clearTimeout(timer.current)}
        onBlurCapture={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) scheduleClose(true);
        }}>
        {sources.map((source) => <CitationSourceItem key={source.id} source={source} />)}
      </PopoverContent>
    </Popover>
  );
}
