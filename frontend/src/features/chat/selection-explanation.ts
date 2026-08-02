import { authStore } from "@/api/auth-store";

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

// R-017: selection history holds private document excerpts (selected text +
// surrounding context). It must never be shared across accounts on a shared
// machine or a future WebView. Storage is therefore partitioned per
// (user, workspace): account A's logout cannot leak into account B's login.
const STORAGE_KEY_PREFIX = "learngraph:selection-explanations";
const LEGACY_STORAGE_KEY = "learngraph:selection-explanations";
const OPEN_EVENT = "learngraph:selection-explanation";
const RECORDS_EVENT = "learngraph:selection-explanation-records";

type StorageMap = Record<string, SelectionExplanationRecord[]>;

// Per-namespace budget bounds stored footprint on phones / low-memory desktops
// and bounds the worst-case amount of private text persisted locally.
const MAX_PARENT_SESSIONS = 200;
const MAX_RECORDS_PER_SESSION = 80;
const MAX_TOTAL_RECORDS = 4_000;
// ~1 MiB ceiling. Selections with full prefix/suffix can reach a few KiB each;
// the cap is a coarse backstop, not an exact policy instrument.
const MAX_STORAGE_BYTES = 1 * 1024 * 1024;

function namespaceScope(): { userId: string; workspaceId: string } {
  // On a shared machine the logged-in user owns these records; the workspace
  // further scopes multi-workspace accounts. authStore is keyed off sessionStorage
  // which is cleared on logout, so a not-yet-authenticated window degrades to a
  // shared "__anon__" bucket that is still workspace-partitioned when possible.
  const session = authStore.getSession();
  const userId = session?.userId ?? "__anon__";
  const workspaceId = session?.workspaceId ?? "__default__";
  return { userId, workspaceId };
}

function storageKeyFor(userId: string, workspaceId: string): string {
  return `${STORAGE_KEY_PREFIX}:${userId}:${workspaceId}`;
}

/** Current logged-in partition's storage key. */
function currentStorageKey(): string {
  const { userId, workspaceId } = namespaceScope();
  return storageKeyFor(userId, workspaceId);
}

function readNamespace(key: string): StorageMap {
  try {
    if (typeof window === "undefined" || !window.localStorage) return {};
    const raw = window.localStorage.getItem(key);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parsed as StorageMap;
  } catch {
    return {};
  }
}

// Legacy data (pre-namespace) cannot be attributed to whoever is logged in
// now, so the spec mandates discarding it rather than silently absorbing it
// into the current user's partition. Sweep it once on first access.
let legacySwept = false;

function sweepLegacyKey() {
  if (legacySwept) return;
  legacySwept = true;
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    if (window.localStorage.getItem(LEGACY_STORAGE_KEY) !== null) {
      window.localStorage.removeItem(LEGACY_STORAGE_KEY);
    }
  } catch {
    // Best-effort; a later clear*() call will retry the cleanup.
    legacySwept = false;
  }
}

function readAll(): StorageMap {
  if (typeof window === "undefined" || !window.localStorage) return {};
  sweepLegacyKey();
  return readNamespace(currentStorageKey());
}

/**
 * Persist with a bounded footprint. Enforces per-session record counts and a
 * global (records + bytes) budget, evicting the oldest entries first so a
 * runaway session cannot silently accumulate private text beyond the cap.
 */
function writeAll(map: StorageMap) {
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    const key = currentStorageKey();
    const bounded = boundStorage(map);
    window.localStorage.setItem(key, JSON.stringify(bounded));
  } catch {
    // Quota / private mode — keep the in-memory caller path only.
  }
}

function boundStorage(map: StorageMap): StorageMap {
  const entries = Object.entries(map)
    .filter(([, records]) => Array.isArray(records))
    .map(([parentId, records]) => [
      parentId,
      records.slice(0, MAX_RECORDS_PER_SESSION),
    ] as [string, SelectionExplanationRecord[]]);

  // Flatten to oldest-first order so eviction always drops the least-recent
  // underline sets regardless of which parent session they belong to.
  const flat: Array<[string, SelectionExplanationRecord]> = entries
    .flatMap(([parentId, records]) =>
      records.map(
        (record) => [parentId, record] as [string, SelectionExplanationRecord],
      ),
    )
    .sort(
      (a, b) => (a[1].createdAt ?? "").localeCompare(b[1].createdAt ?? ""),
    );

  // Cap distinct parent sessions (each is a separate chat session's underline set).
  const keptParents = new Set(
    flat
      .slice(-MAX_PARENT_SESSIONS)
      .map(([parentId]) => parentId),
  );

  // Enforce a global record budget + a byte budget, evicting oldest-first.
  // Both budgets are cross-partition: a single runaway parent session cannot
  // starve the others, and the device-wide footprint stays bounded.
  const kept: StorageMap = {};
  let bytes = 2; // account for the leading/trailing JSON braces
  let totalCount = 0;
  for (const [parentId, record] of flat) {
    if (!keptParents.has(parentId)) continue;
    if (totalCount >= MAX_TOTAL_RECORDS) continue;
    let bucket = kept[parentId];
    if (!bucket) {
      bucket = [];
      kept[parentId] = bucket;
    }
    const candidate = [...bucket, record];
    const delta =
      // Approximate serialized size of this record plus its array slot.
      JSON.stringify(candidate).length - JSON.stringify(bucket).length + 1;
    if (bytes + delta > MAX_STORAGE_BYTES) continue;
    bucket.push(record);
    bytes += delta;
    totalCount += 1;
  }
  return kept;
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
    // Remove the current partition (the logged-in user + active workspace).
    // Logout/deleteAccount call this while authStore still holds the session,
    // so the key resolves to the partition about to become invalid.
    window.localStorage.removeItem(currentStorageKey());
    // Legacy pre-namespace key held private text without account partitioning.
    // It cannot be safely attributed to whoever is logging out now, so it is
    // discarded outright rather than absorbed into the current partition.
    window.localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    // Best-effort privacy cleanup for restricted browser storage.
  }
}

/**
 * Wipe every partition on this device. Used when the user deletes their
 * account entirely, where the device itself must not retain prior private
 * excerpts under any partition (including stale anonymous buckets left by a
 * not-yet-authenticated window).
 */
export function clearAllSelectionExplanations() {
  pendingOpenDetail = null;
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    const toRemove: string[] = [];
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index);
      if (!key) continue;
      if (key === LEGACY_STORAGE_KEY || key.startsWith(`${STORAGE_KEY_PREFIX}:`)) {
        toRemove.push(key);
      }
    }
    for (const key of toRemove) window.localStorage.removeItem(key);
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
      : [record, ...current].slice(0, MAX_RECORDS_PER_SESSION);
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
 * Locate a stored selection in current rendered content. Prefer an exact
 * prefix/suffix anchor, then the strongest partial context match, and finally
 * the first non-overlapping occurrence for older or edited records.
 */
type SelectionExplanationMark = Pick<
  SelectionExplanationRecord,
  "id" | "selectedText" | "prefix" | "suffix"
>;

type TextRange = { start: number; end: number };

function matchingPrefixLength(content: string, prefix: string): number {
  const maxLength = Math.min(content.length, prefix.length);
  for (let length = maxLength; length > 0; length -= 1) {
    if (content.endsWith(prefix.slice(-length))) return length;
  }
  return 0;
}

function matchingSuffixLength(content: string, suffix: string): number {
  const maxLength = Math.min(content.length, suffix.length);
  for (let length = maxLength; length > 0; length -= 1) {
    if (content.startsWith(suffix.slice(0, length))) return length;
  }
  return 0;
}

function rangesOverlap(left: TextRange, right: TextRange): boolean {
  return left.start < right.end && left.end > right.start;
}

function findSelectionRange(
  content: string,
  mark: SelectionExplanationMark,
  usedRanges: TextRange[],
): TextRange | null {
  const needle = mark.selectedText.trim();
  if (!needle) return null;

  const candidates: Array<TextRange & { exact: boolean; score: number }> = [];
  let from = 0;
  while (from <= content.length - needle.length) {
    const start = content.indexOf(needle, from);
    if (start < 0) break;
    const range = { start, end: start + needle.length };
    if (!usedRanges.some((used) => rangesOverlap(range, used))) {
      const before = content.slice(0, start);
      const after = content.slice(range.end);
      const prefixScore = matchingPrefixLength(before, mark.prefix);
      const suffixScore = matchingSuffixLength(after, mark.suffix);
      candidates.push({
        ...range,
        exact:
          (!mark.prefix || prefixScore === mark.prefix.length) &&
          (!mark.suffix || suffixScore === mark.suffix.length),
        score: prefixScore + suffixScore,
      });
    }
    from = start + Math.max(1, needle.length);
  }
  if (!candidates.length) return null;

  candidates.sort(
    (left, right) =>
      Number(right.exact) - Number(left.exact) ||
      right.score - left.score ||
      left.start - right.start,
  );
  const { start, end } = candidates[0]!;
  return { start, end };
}

/**
 * Split plain text into segments with clickable explain marks. Stored context
 * keeps repeated selections anchored to their original occurrence.
 */
export function splitTextWithSelectionMarks(
  content: string,
  marks: SelectionExplanationMark[],
): Array<
  | { type: "text"; value: string }
  | { type: "mark"; id: string; value: string }
> {
  if (!content || !marks.length) return [{ type: "text", value: content }];
  type Hit = TextRange & { id: string };
  const hits: Hit[] = [];
  const usedRanges: TextRange[] = [];
  for (const mark of marks) {
    const range = findSelectionRange(content, mark, usedRanges);
    if (!range) continue;
    hits.push({ id: mark.id, ...range });
    usedRanges.push(range);
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
 * Wrap anchored occurrences under `root` with clickable underline buttons.
 * Used for assistant markdown (HTML) where React does not own the text nodes.
 */
export function decorateSelectionExplanationMarks(
  root: HTMLElement,
  marks: SelectionExplanationMark[],
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

  type Piece = { node: Text; start: number; end: number };
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const pieces: Piece[] = [];
  let content = "";
  let node = walker.nextNode();
  while (node) {
    const textNode = node as Text;
    const value = textNode.nodeValue ?? "";
    const parent = textNode.parentElement;
    if (
      value &&
      !parent?.closest("button, a, input, textarea")
    ) {
      const start = content.length;
      content += value;
      pieces.push({ node: textNode, start, end: content.length });
    }
    node = walker.nextNode();
  }

  const selected: Array<TextRange & { id: string; needle: string }> = [];
  const usedRanges: TextRange[] = [];
  for (const mark of marks) {
    const range = findSelectionRange(content, mark, usedRanges);
    if (!range) continue;
    selected.push({ id: mark.id, needle: mark.selectedText.trim(), ...range });
    usedRanges.push(range);
  }

  const handlers: Array<{ node: HTMLElement; handler: (event: Event) => void }> =
    [];
  for (const hit of selected.sort((left, right) => right.start - left.start)) {
    const startPiece = pieces.find(
      (piece) => piece.start <= hit.start && piece.end > hit.start,
    );
    const endPiece = pieces.find(
      (piece) => piece.start < hit.end && piece.end >= hit.end,
    );
    if (!startPiece || !endPiece || startPiece.node !== endPiece.node) continue;

    const range = document.createRange();
    range.setStart(startPiece.node, hit.start - startPiece.start);
    range.setEnd(endPiece.node, hit.end - endPiece.start);
    const span = document.createElement("button");
    span.type = "button";
    span.className = "selection-explain-mark";
    span.dataset.selectionExplainId = hit.id;
    span.setAttribute("aria-label", `打开划词解释：${hit.needle.slice(0, 40)}`);
    span.title = "打开历史划词解释";
    try {
      range.surroundContents(span);
    } catch {
      // Cross-element ranges (markdown mid-token) cannot surround; skip quietly.
      continue;
    }
    const handler = (event: Event) => {
      event.preventDefault();
      event.stopPropagation();
      onOpen(hit.id);
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
