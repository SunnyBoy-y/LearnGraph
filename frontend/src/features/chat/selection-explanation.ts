/**
 * Independent 划词解释 (selection explanation) records.
 *
 * Parent-session local history drives underline markers and reopen. Each record
 * may point at a child chat session that holds the isolated Q&A thread.
 */

export type SelectionExplainAction = "define" | "explain";

export type SelectionExplanationRecord = {
  id: string;
  parentSessionId: string;
  sourceMessageId: string;
  selectedText: string;
  prefix: string;
  suffix: string;
  contentMatched: boolean;
  action: SelectionExplainAction;
  explanationSessionId?: string;
  createdAt: string;
};

export type SelectionExplanationOpenDetail = {
  parentSessionId: string;
  sourceMessageId: string;
  selectedText: string;
  prefix?: string;
  suffix?: string;
  contentMatched?: boolean;
  action?: SelectionExplainAction;
  /** Reopen an existing history entry instead of creating a new one. */
  recordId?: string;
  explanationSessionId?: string;
};

const STORAGE_KEY = "learngraph:selection-explanations";
const OPEN_EVENT = "learngraph:selection-explanation";
const RECORDS_EVENT = "learngraph:selection-explanation-records";

type StorageMap = Record<string, SelectionExplanationRecord[]>;

function readAll(): StorageMap {
  try {
    if (typeof window === "undefined" || !window.localStorage) return {};
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parsed as StorageMap;
  } catch {
    return {};
  }
}

function writeAll(map: StorageMap) {
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // Quota / private mode — keep the in-memory caller path only.
  }
}

function notifyRecordsChanged(parentSessionId: string) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(RECORDS_EVENT, {
      detail: { parentSessionId },
    }),
  );
}

export function clearSelectionExplanations() {
  pendingOpenDetail = null;
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Best-effort privacy cleanup for restricted browser storage.
  }
}

export function createSelectionExplanationId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `sel-exp-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * Short / term-like picks get a definition prompt; longer spans get explanation.
 * Chinese terms without spaces still count as short when under ~18 chars.
 */
export function inferSelectionAction(text: string): SelectionExplainAction {
  const trimmed = text.trim();
  if (!trimmed) return "explain";
  const compact = trimmed.replace(/\s+/gu, "");
  const hasSentenceBreak = /[。！？!?;；\n]/.test(trimmed);
  if (hasSentenceBreak) return "explain";
  if (compact.length <= 18 && trimmed.length <= 24) return "define";
  if (trimmed.split(/\s+/u).length <= 3 && trimmed.length <= 40) return "define";
  return "explain";
}

export function buildSelectionExplainPrompt(
  action: SelectionExplainAction,
  selectedText: string,
): string {
  const quote = selectedText.trim();
  if (action === "define") {
    return (
      `请用简洁中文定义或解释下面这个词/短语的含义。` +
      `先给一句话定义，再补充必要的语境、易混点与一个简短例子。\n\n` +
      `「${quote}」`
    );
  }
  return (
    `请解释下面这段内容的含义。` +
    `先概括它在上下文中的意思，再说明关键概念、为什么这样写/说，以及需要注意的点。\n\n` +
    `「${quote}」`
  );
}

export function listSelectionExplanations(
  parentSessionId: string | null | undefined,
): SelectionExplanationRecord[] {
  if (!parentSessionId || parentSessionId === "new") return [];
  const list = readAll()[parentSessionId];
  return Array.isArray(list) ? list : [];
}

/**
 * Map child explanation-session id → parent session id from local history.
 * Used to nest older 划词解释 sessions that predate parent_session_id writes.
 */
export function selectionExplanationParentMap(): Record<string, string> {
  const map = readAll();
  const out: Record<string, string> = {};
  for (const [parentSessionId, records] of Object.entries(map)) {
    if (!Array.isArray(records)) continue;
    for (const record of records) {
      if (record?.explanationSessionId) {
        out[record.explanationSessionId] = parentSessionId;
      }
    }
  }
  return out;
}

export function getSelectionExplanation(
  parentSessionId: string | null | undefined,
  recordId: string | null | undefined,
): SelectionExplanationRecord | null {
  if (!parentSessionId || !recordId) return null;
  return (
    listSelectionExplanations(parentSessionId).find((item) => item.id === recordId) ??
    null
  );
}

export function upsertSelectionExplanation(
  record: SelectionExplanationRecord,
): SelectionExplanationRecord {
  const map = readAll();
  const current = Array.isArray(map[record.parentSessionId])
    ? map[record.parentSessionId]!
    : [];
  const index = current.findIndex((item) => item.id === record.id);
  const next =
    index >= 0
      ? current.map((item, itemIndex) => (itemIndex === index ? record : item))
      : [record, ...current].slice(0, 80);
  map[record.parentSessionId] = next;
  writeAll(map);
  notifyRecordsChanged(record.parentSessionId);
  return record;
}

export function bindExplanationSession(
  parentSessionId: string,
  recordId: string,
  explanationSessionId: string,
): SelectionExplanationRecord | null {
  const existing = getSelectionExplanation(parentSessionId, recordId);
  if (!existing) return null;
  return upsertSelectionExplanation({
    ...existing,
    explanationSessionId,
  });
}

/** Survives the brief unmount/remount when a collapsed rail is force-opened. */
let pendingOpenDetail: SelectionExplanationOpenDetail | null = null;

export function openSelectionExplanation(detail: SelectionExplanationOpenDetail) {
  pendingOpenDetail = detail;
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(OPEN_EVENT, {
      detail,
    }),
  );
}

/** ContextRail calls this on mount / session change to claim a pending open. */
export function consumePendingSelectionExplanation(
  parentSessionId: string | null | undefined,
): SelectionExplanationOpenDetail | null {
  if (!pendingOpenDetail || !parentSessionId) return null;
  const pendingParent =
    pendingOpenDetail.parentSessionId ||
    (pendingOpenDetail as SelectionExplanationOpenDetail & { sessionId?: string })
      .sessionId;
  if (pendingParent !== parentSessionId) return null;
  const detail = pendingOpenDetail;
  pendingOpenDetail = null;
  return detail;
}

export function clearPendingSelectionExplanation() {
  pendingOpenDetail = null;
}

export function selectionExplanationOpenEventName() {
  return OPEN_EVENT;
}

export function selectionExplanationRecordsEventName() {
  return RECORDS_EVENT;
}

/**
 * Split plain text into segments with clickable explain marks for the first
 * exact occurrence of each mark. Used for user messages (React-owned text).
 */
export function splitTextWithSelectionMarks(
  content: string,
  marks: Array<Pick<SelectionExplanationRecord, "id" | "selectedText">>,
): Array<
  | { type: "text"; value: string }
  | { type: "mark"; id: string; value: string }
> {
  if (!content || !marks.length) return [{ type: "text", value: content }];
  type Hit = { id: string; start: number; end: number };
  const hits: Hit[] = [];
  const usedRanges: Array<{ start: number; end: number }> = [];
  for (const mark of marks) {
    const needle = mark.selectedText.trim();
    if (!needle) continue;
    let from = 0;
    while (from <= content.length - needle.length) {
      const start = content.indexOf(needle, from);
      if (start < 0) break;
      const end = start + needle.length;
      const overlaps = usedRanges.some(
        (range) => start < range.end && end > range.start,
      );
      if (!overlaps) {
        hits.push({ id: mark.id, start, end });
        usedRanges.push({ start, end });
        break;
      }
      from = start + 1;
    }
  }
  if (!hits.length) return [{ type: "text", value: content }];
  hits.sort((a, b) => a.start - b.start);
  const segments: Array<
    | { type: "text"; value: string }
    | { type: "mark"; id: string; value: string }
  > = [];
  let cursor = 0;
  for (const hit of hits) {
    if (hit.start > cursor) {
      segments.push({ type: "text", value: content.slice(cursor, hit.start) });
    }
    segments.push({
      type: "mark",
      id: hit.id,
      value: content.slice(hit.start, hit.end),
    });
    cursor = hit.end;
  }
  if (cursor < content.length) {
    segments.push({ type: "text", value: content.slice(cursor) });
  }
  return segments;
}

/**
 * Wrap first exact occurrence of each mark's selected text under `root` with a
 * clickable underline span. Safe to re-run; previous marks are cleared first.
 * Used for assistant markdown (HTML) where React does not own the text nodes.
 */
export function decorateSelectionExplanationMarks(
  root: HTMLElement,
  marks: Array<Pick<SelectionExplanationRecord, "id" | "selectedText">>,
  onOpen: (recordId: string) => void,
): () => void {
  const previous = root.querySelectorAll<HTMLElement>("[data-selection-explain-id]");
  previous.forEach((node) => {
    const parent = node.parentNode;
    if (!parent) return;
    while (node.firstChild) parent.insertBefore(node.firstChild, node);
    parent.removeChild(node);
    parent.normalize();
  });

  const handlers: Array<{ node: HTMLElement; handler: (event: Event) => void }> =
    [];
  const used = new Set<string>();

  for (const mark of marks) {
    const needle = mark.selectedText.trim();
    if (!needle || used.has(mark.id)) continue;
    const hit = findTextRange(root, needle);
    if (!hit) continue;
    used.add(mark.id);
    const span = document.createElement("button");
    span.type = "button";
    span.className = "selection-explain-mark";
    span.dataset.selectionExplainId = mark.id;
    span.setAttribute("aria-label", `打开划词解释：${needle.slice(0, 40)}`);
    span.title = "打开历史划词解释";
    try {
      hit.range.surroundContents(span);
    } catch {
      // Cross-element ranges (markdown mid-token) cannot surround; skip quietly.
      continue;
    }
    const handler = (event: Event) => {
      event.preventDefault();
      event.stopPropagation();
      onOpen(mark.id);
    };
    span.addEventListener("click", handler);
    handlers.push({ node: span, handler });
  }

  return () => {
    for (const { node, handler } of handlers) {
      node.removeEventListener("click", handler);
    }
  };
}

function findTextRange(
  root: HTMLElement,
  needle: string,
): { range: Range } | null {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  let pending = "";
  type Piece = { node: Text; start: number; end: number };
  const pieces: Piece[] = [];

  while (node) {
    const textNode = node as Text;
    const value = textNode.nodeValue ?? "";
    if (value) {
      // Skip text already inside an explain mark (or other interactive control).
      const parent = textNode.parentElement;
      if (
        parent &&
        (parent.closest("[data-selection-explain-id]") ||
          parent.closest("button, a, input, textarea"))
      ) {
        node = walker.nextNode();
        continue;
      }
      const start = pending.length;
      pending += value;
      pieces.push({ node: textNode, start, end: pending.length });
      const index = pending.indexOf(needle);
      if (index >= 0) {
        const end = index + needle.length;
        const startPiece = pieces.find(
          (piece) => piece.start <= index && piece.end > index,
        );
        const endPiece = pieces.find(
          (piece) => piece.start < end && piece.end >= end,
        );
        if (!startPiece || !endPiece || startPiece.node !== endPiece.node) {
          // Multi-node wrap is unreliable for markdown; only decorate single-node hits.
          return null;
        }
        const range = document.createRange();
        range.setStart(startPiece.node, index - startPiece.start);
        range.setEnd(endPiece.node, end - endPiece.start);
        return { range };
      }
    }
    node = walker.nextNode();
  }
  return null;
}
