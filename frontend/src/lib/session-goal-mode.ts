/**
 * Per-session goal mode (目标设定) persistence.
 *
 * Goal mode is normally derived from the `?mode=goal` URL query, but session
 * switches from the sidebar navigate without that query. To keep a session's
 * goal state while the user visits other conversations, we remember it here —
 * keyed by workspace + session — and only clear it when the user explicitly
 * exits goal mode (or the goal setup finishes).
 *
 * The capture-stage composer draft is stored alongside the flag so returning
 * to a session mid-setup restores the text the user had typed.
 */

const STORAGE_KEY = "learngraph:session-goal-mode";

interface GoalModeEntry {
  /** True while this session should render in goal mode. */
  goalMode?: boolean;
  /** Last capture-stage composer text (before the goal was submitted). */
  captureDraft?: string;
}

type GoalModeMap = Record<string, GoalModeEntry>;

function readAll(): GoalModeMap {
  try {
    if (typeof window === "undefined" || !window.localStorage) return {};
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parsed as GoalModeMap;
  } catch {
    return {};
  }
}

function writeAll(map: GoalModeMap): void {
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // Quota / private mode — keep in-memory only for this call stack.
  }
}

function scopeKey(workspaceId: string, sessionId: string): string {
  return `${workspaceId}:${sessionId}`;
}

function readEntry(workspaceId: string, sessionId: string): GoalModeEntry {
  return readAll()[scopeKey(workspaceId, sessionId)] ?? {};
}

function writeEntry(
  workspaceId: string,
  sessionId: string,
  entry: GoalModeEntry,
): void {
  const map = readAll();
  if (entry.goalMode || entry.captureDraft) {
    map[scopeKey(workspaceId, sessionId)] = entry;
  } else {
    delete map[scopeKey(workspaceId, sessionId)];
  }
  writeAll(map);
}

/** True when this session should restore goal mode after a session switch. */
export function hasSessionGoalMode(
  workspaceId: string,
  sessionId: string,
): boolean {
  return Boolean(readEntry(workspaceId, sessionId).goalMode);
}

/** Record/clear the per-session goal-mode flag. */
export function setSessionGoalMode(
  workspaceId: string,
  sessionId: string,
  active: boolean,
): void {
  const entry = readEntry(workspaceId, sessionId);
  writeEntry(workspaceId, sessionId, { ...entry, goalMode: active });
}

/** Fully forget this session's goal state (manual exit / setup completed). */
export function clearSessionGoalMode(
  workspaceId: string,
  sessionId: string,
): void {
  const entry = readEntry(workspaceId, sessionId);
  writeEntry(workspaceId, sessionId, { ...entry, goalMode: false });
}

/** Last capture-stage composer text for this session, or "". */
export function getSessionGoalDraft(
  workspaceId: string,
  sessionId: string,
): string {
  return readEntry(workspaceId, sessionId).captureDraft ?? "";
}

export function setSessionGoalDraft(
  workspaceId: string,
  sessionId: string,
  draft: string,
): void {
  const entry = readEntry(workspaceId, sessionId);
  writeEntry(workspaceId, sessionId, { ...entry, captureDraft: draft });
}

export function clearSessionGoalDraft(
  workspaceId: string,
  sessionId: string,
): void {
  const entry = readEntry(workspaceId, sessionId);
  writeEntry(workspaceId, sessionId, { ...entry, captureDraft: undefined });
}
