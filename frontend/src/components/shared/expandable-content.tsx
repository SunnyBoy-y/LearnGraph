import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

import { ChevronDown, ChevronUp } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Default visible height for long prose/code blocks. */
export const DEFAULT_EXPANDABLE_LINE_LIMIT = 24;

type ExpandableStyle = CSSProperties & {
  "--expandable-line-limit"?: number;
};

export type ExpandableContentProps = {
  children: ReactNode;
  /** Number of normal text lines kept visible before folding. */
  lineLimit?: number;
  className?: string;
  contentClassName?: string;
  /** Optional stable key used to remember the user's open/closed choice. */
  storageKey?: string;
  /** Keep the block open while a stream is actively producing content. */
  expandedWhileStreaming?: boolean;
  streaming?: boolean;
  /** Accessible label prefix, e.g. "代码" or "思维链". */
  label?: string;
};

function readStoredOpen(storageKey: string | undefined): boolean | undefined {
  if (!storageKey || typeof window === "undefined") return undefined;
  try {
    const value = window.localStorage.getItem(storageKey);
    if (value === "1") return true;
    if (value === "0") return false;
  } catch {
    // Storage is optional (private browsing / embedded WebViews).
  }
  return undefined;
}

function writeStoredOpen(storageKey: string | undefined, open: boolean): void {
  if (!storageKey || typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey, open ? "1" : "0");
  } catch {
    // A visual toggle must remain usable when storage is unavailable.
  }
}

/**
 * Keep the complete child tree mounted and fold it with CSS rather than
 * truncating the source string. This is important for Markdown/HTML: links,
 * code fences and selection offsets continue to refer to the original text.
 */
export function ExpandableContent({
  children,
  lineLimit = DEFAULT_EXPANDABLE_LINE_LIMIT,
  className,
  contentClassName,
  storageKey,
  expandedWhileStreaming = false,
  streaming = false,
  label = "内容",
}: ExpandableContentProps) {
  const contentRef = useRef<HTMLDivElement | null>(null);
  const [overflowing, setOverflowing] = useState(false);
  const [estimatedLines, setEstimatedLines] = useState(lineLimit + 1);
  const storedOpen = useRef(readStoredOpen(storageKey));
  const [open, setOpen] = useState(() => storedOpen.current ?? false);
  const wasStreamingRef = useRef(streaming);

  useEffect(() => {
    const nextStoredOpen = readStoredOpen(storageKey);
    storedOpen.current = nextStoredOpen;
    setOpen(nextStoredOpen ?? false);
  }, [storageKey]);

  const measure = useCallback(() => {
    const node = contentRef.current;
    if (!node) return;
    const style = window.getComputedStyle(node);
    const fontSize = Number.parseFloat(style.fontSize) || 14;
    const parsedLineHeight = Number.parseFloat(style.lineHeight);
    const lineHeight = Number.isFinite(parsedLineHeight)
      ? parsedLineHeight
      : fontSize * 1.5;
    const limitHeight = Math.max(1, lineHeight * lineLimit);
    const fullHeight = node.scrollHeight;
    const hasOverflow = fullHeight > limitHeight + 1;
    setOverflowing(hasOverflow);
    setEstimatedLines(Math.max(lineLimit + 1, Math.ceil(fullHeight / lineHeight)));
  }, [lineLimit]);

  useLayoutEffect(() => {
    measure();
  }, [children, measure]);

  useEffect(() => {
    const node = contentRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [measure]);

  // Do not hide the live tail while the model is writing. Once the stream
  // settles, a long block folds automatically unless the user opened it.
  useEffect(() => {
    if (wasStreamingRef.current && !streaming && overflowing && storedOpen.current !== true) {
      setOpen(false);
    }
    wasStreamingRef.current = streaming;
  }, [overflowing, streaming]);

  const effectiveOpen = streaming && expandedWhileStreaming ? true : open;
  const toggle = () => {
    const next = !open;
    setOpen(next);
    storedOpen.current = next;
    writeStoredOpen(storageKey, next);
  };

  const style: ExpandableStyle = {
    "--expandable-line-limit": Math.max(1, Math.floor(lineLimit)),
  };

  return (
    <div
      className={cn("expandable-content", effectiveOpen && "is-expanded", className)}
      data-expandable-content=""
      data-expanded={effectiveOpen ? "true" : "false"}
      data-overflowing={overflowing ? "true" : "false"}
    >
      <div
        className={cn("expandable-content__body", contentClassName)}
        ref={contentRef}
        style={style}
      >
        {children}
      </div>
      {overflowing ? (
        <div className="expandable-content__footer">
          <Button
            aria-expanded={effectiveOpen}
            className="expandable-content__toggle"
            onClick={toggle}
            size="xs"
            type="button"
            variant="ghost"
          >
            {effectiveOpen ? <ChevronUp aria-hidden="true" /> : <ChevronDown aria-hidden="true" />}
            {effectiveOpen
              ? `收起${label}`
              : `展开${label}（约 ${estimatedLines} 行）`}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

