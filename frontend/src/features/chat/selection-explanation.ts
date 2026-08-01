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
      records.map((record) => [parentId, record] as const),
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
