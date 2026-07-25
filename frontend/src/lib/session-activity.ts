/**
 * Client-side session activity: concurrent generation tracking, unread-complete
 * dots, and sidebar sort order.
 *
 * Sort tiers (pinned groups first, then within each group):
 *   0. Recently touched by the user (opened / sent a message)
 *   1. Model finished while the user was elsewhere (black-dot / unread)
 *   2. Everything else by updated_at
 */

export type SessionActivity = {
  running: boolean;
  /** Model finished while this session was not the active view. */
  unreadCompleted: boolean;
  completedAt: number | null;
  /** Last time the user opened or sent in this session (epoch ms). */
  lastTouchedAt: number;
};

export type SessionActivityMap = ReadonlyMap<string, SessionActivity>;

export type SidebarSortableSession = {
  id: string;
  pinned: boolean;
  /** ISO timestamp or epoch ms; missing sorts last within a tier. */
  updated_at?: string | number | null;
};

const EMPTY: SessionActivity = {
  running: false,
  unreadCompleted: false,
  completedAt: null,
  lastTouchedAt: 0,
};

let byId = new Map<string, SessionActivity>();
const listeners = new Set<() => void>();
let snapshot: SessionActivityMap = byId;

function emit() {
  snapshot = byId;
  for (const listener of listeners) listener();
}

function read(sessionId: string): SessionActivity {
  return byId.get(sessionId) ?? EMPTY;
}

function write(sessionId: string, next: SessionActivity) {
  if (!sessionId || sessionId === "new") return;
  const prev = byId.get(sessionId);
  if (
    prev &&
    prev.running === next.running &&
    prev.unreadCompleted === next.unreadCompleted &&
    prev.completedAt === next.completedAt &&
    prev.lastTouchedAt === next.lastTouchedAt
  ) {
    return;
  }
  const map = new Map(byId);
  if (
    !next.running &&
    !next.unreadCompleted &&
    next.completedAt == null &&
    next.lastTouchedAt === 0
  ) {
    map.delete(sessionId);
  } else {
    map.set(sessionId, next);
  }
  byId = map;
  emit();
}

export function getSessionActivity(sessionId: string): SessionActivity {
  return read(sessionId);
}

export function getSessionActivitySnapshot(): SessionActivityMap {
  return snapshot;
}

export function subscribeSessionActivity(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Mark generation in flight for a session (sidebar spinner / concurrent runs). */
export function markSessionRunning(sessionId: string, running: boolean): void {
  if (!sessionId || sessionId === "new") return;
  const prev = read(sessionId);
  write(sessionId, { ...prev, running });
}

/**
 * Model finished. If the user is still viewing this session, clear unread;
 * otherwise set the black-dot reminder and stamp completion time for sort.
 */
export function markSessionGenerationFinished(
  sessionId: string,
  options: { viewing: boolean; at?: number } = { viewing: false },
): void {
  if (!sessionId || sessionId === "new") return;
  const prev = read(sessionId);
  const at = options.at ?? Date.now();
  if (options.viewing) {
    write(sessionId, {
      ...prev,
      running: false,
      unreadCompleted: false,
      completedAt: null,
      lastTouchedAt: Math.max(prev.lastTouchedAt, at),
    });
    return;
  }
  write(sessionId, {
    ...prev,
    running: false,
    unreadCompleted: true,
    completedAt: at,
  });
}

/** User opened / focused a session — clear black dot and bump recency. */
export function markSessionViewed(
  sessionId: string,
  at: number = Date.now(),
): void {
  if (!sessionId || sessionId === "new") return;
  const prev = read(sessionId);
  write(sessionId, {
    ...prev,
    unreadCompleted: false,
    completedAt: null,
    lastTouchedAt: Math.max(prev.lastTouchedAt, at),
  });
}

/** User sent a message — bump recency so the session sorts to the front. */
export function markSessionTouched(
  sessionId: string,
  at: number = Date.now(),
): void {
  if (!sessionId || sessionId === "new") return;
  const prev = read(sessionId);
  write(sessionId, {
    ...prev,
    lastTouchedAt: Math.max(prev.lastTouchedAt, at),
  });
}

function toEpoch(value: string | number | null | undefined): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value) {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

/**
 * Sort tier within a pin group:
 * 0 = recently touched by user (and not waiting for unread completion)
 * 1 = model finished while the user was elsewhere (black-dot)
 * 2 = everything else
 */
export function sessionSortTier(
  activity: SessionActivity | undefined,
): 0 | 1 | 2 {
  const state = activity ?? EMPTY;
  // Unread completion wins over stale touch so black-dot sessions gather under
  // pinned and above idle history, ordered by completedAt.
  if (state.unreadCompleted) return 1;
  if (state.lastTouchedAt > 0) return 0;
  return 2;
}

export function compareSidebarSessions(
  left: SidebarSortableSession,
  right: SidebarSortableSession,
  activity: SessionActivityMap = snapshot,
): number {
  const leftPinned = left.pinned === true;
  const rightPinned = right.pinned === true;
  if (leftPinned !== rightPinned) return leftPinned ? -1 : 1;

  const leftActivity = activity.get(left.id);
  const rightActivity = activity.get(right.id);
  const leftTier = sessionSortTier(leftActivity);
  const rightTier = sessionSortTier(rightActivity);
  if (leftTier !== rightTier) return leftTier - rightTier;

  if (leftTier === 1) {
    const completedDelta =
      (rightActivity?.completedAt ?? 0) - (leftActivity?.completedAt ?? 0);
    if (completedDelta !== 0) return completedDelta;
  }
  if (leftTier === 0) {
    const touchDelta =
      (rightActivity?.lastTouchedAt ?? 0) - (leftActivity?.lastTouchedAt ?? 0);
    if (touchDelta !== 0) return touchDelta;
  }

  const updatedDelta = toEpoch(right.updated_at) - toEpoch(left.updated_at);
  if (updatedDelta !== 0) return updatedDelta;
  return left.id.localeCompare(right.id);
}

export function sortSidebarSessions<T extends SidebarSortableSession>(
  sessions: readonly T[],
  activity: SessionActivityMap = snapshot,
): T[] {
  return [...sessions].sort((left, right) =>
    compareSidebarSessions(left, right, activity),
  );
}
