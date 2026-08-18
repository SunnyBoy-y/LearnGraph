"use client";

import { Brain, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import type { MessagePart } from "@/types/sessions";

function firstLine(text: string): string {
  const newline = text.indexOf("\n");
  return newline === -1 ? text : text.slice(0, newline);
}

function latestLine(text: string): string {
  const visible = text.trimEnd();
  const newline = visible.lastIndexOf("\n");
  return newline === -1 ? visible : visible.slice(newline + 1);
}

/**
 * One reasoning block inside the outer thinking chain, modeled after the
 * deepseek-harness Think row: collapsed to a single line by default.
 *
 * - While it is the streaming tail, the summary follows the LATEST non-empty
 *   line and the one-line scrollport tracks each delta to the inline end, so
 *   the user sees the model thinking without expanding anything.
 * - When the block settles, the row keeps the stable first-line summary and
 *   the title is fixed to 「思考过程」.
 * - Clicking the row expands the full reasoning body.
 */
export function ReasoningSummaryRow({
  part,
  streaming,
}: {
  part: MessagePart;
  streaming: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const summaryRef = useRef<HTMLSpanElement | null>(null);
  const text = part.content ?? "";
  const running =
    streaming && (part.status === "streaming" || part.status === "pending");
  const summary = running ? latestLine(text) : firstLine(text);

  // Follow the inline end while running (deepseek-harness style: the motion is
  // driven by real deltas, not a synthetic marquee).
  useEffect(() => {
    const element = summaryRef.current;
    if (!element || !running) return;
    element.scrollLeft = element.scrollWidth - element.clientWidth;
  }, [running, summary]);

  return (
    <div
      className="reasoning-summary-row"
      data-running={running || undefined}
      data-expanded={expanded || undefined}
    >
      <button
        aria-expanded={expanded}
        className="reasoning-summary-row__trigger"
        onClick={() => setExpanded((value) => !value)}
        type="button"
      >
        <Brain
          aria-hidden="true"
          className="reasoning-summary-row__icon size-4 shrink-0"
        />
        <span className="reasoning-summary-row__title">思考过程</span>
        {!expanded ? (
          <span
            ref={summaryRef}
            className="reasoning-summary-row__summary"
            data-follow-end={running || undefined}
          >
            {summary}
          </span>
        ) : null}
        <ChevronDown
          className={cn(
            "reasoning-summary-row__chevron size-3.5 text-muted-foreground transition-transform",
            !expanded && "rotate-[-90deg]",
          )}
        />
      </button>
      {expanded ? (
        <div className="reasoning-summary-row__body">{text}</div>
      ) : null}
    </div>
  );
}
