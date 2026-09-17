const ACTIVE_STORAGE_KEY = "learngraph.voice.active-sessions.v1";

/**
 * A reload ends the call: the peer connection, the local mic track and the
 * server-side voice session all go away, so any marker persisted by the previous
 * page load is stale and would leave a phantom "在通话中" badge in the sidebar.
 * The store is therefore emptied once per page load, before anything renders.
 */
function clearStaleVoiceSessionMarkers() {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(ACTIVE_STORAGE_KEY);
  } catch { /* storage is optional */ }
}

clearStaleVoiceSessionMarkers();

export function setVoiceSessionActive(workspaceId: string, sessionId: string, active: boolean) {
  if (!workspaceId || !sessionId || typeof window === "undefined") return;
  try {
    const key = `${workspaceId}:${sessionId}`;
    const raw = JSON.parse(window.localStorage.getItem(ACTIVE_STORAGE_KEY) || "[]");
    const current = new Set<string>(Array.isArray(raw) ? raw.filter((item): item is string => typeof item === "string") : []);
    active ? current.add(key) : current.delete(key);
    window.localStorage.setItem(ACTIVE_STORAGE_KEY, JSON.stringify([...current].slice(-100)));
    window.dispatchEvent(new CustomEvent("learngraph:voice-session-marker", { detail: { key, active } }));
  } catch { /* storage is optional */ }
}

export function isVoiceSessionActive(workspaceId: string, sessionId: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    const raw = JSON.parse(window.localStorage.getItem(ACTIVE_STORAGE_KEY) || "[]");
    return Array.isArray(raw) && raw.includes(`${workspaceId}:${sessionId}`);
  } catch { return false; }
}

const RESUME_STORAGE_KEY = "learngraph.voice.resumable.v1";

/**
 * How long a reloaded page may still offer to rejoin the call.
 *
 * The server closes an idle voice session after its own TTL (45 min by default)
 * and this page cannot tell from localStorage whether it already did, so the
 * offer expires on the same clock as the call it points at instead of lingering
 * as a button that can only fail.
 */
export const VOICE_RESUME_MAX_AGE_MS = 45 * 60 * 1000;

/**
 * Records that a call was live for this session, so a reloaded page can offer a
 * way back into it.
 *
 * `ACTIVE_STORAGE_KEY` above cannot serve this purpose: it is wiped on every
 * load precisely so the sidebar's "在通话中" badge is never a phantom. Rejoining
 * is a different question -- the server-side session, its turns and its task
 * shelf all survive the reload -- so it gets its own record, stamped with the
 * time it was written so a stale entry can expire instead of lying.
 */
export function markVoiceSessionResumable(workspaceId: string, sessionId: string) {
  if (!workspaceId || !sessionId || typeof window === "undefined") return;
  try {
    const raw = JSON.parse(window.localStorage.getItem(RESUME_STORAGE_KEY) || "{}");
    const next = raw && typeof raw === "object" && !Array.isArray(raw) ? { ...raw } : {};
    next[`${workspaceId}:${sessionId}`] = Date.now();
    window.localStorage.setItem(RESUME_STORAGE_KEY, JSON.stringify(next));
    window.dispatchEvent(new CustomEvent("learngraph:voice-session-marker", { detail: { key: `${workspaceId}:${sessionId}`, resumable: true } }));
  } catch { /* storage is optional */ }
}

export function clearVoiceSessionResumable(workspaceId: string, sessionId: string) {
  if (!workspaceId || !sessionId || typeof window === "undefined") return;
  try {
    const raw = JSON.parse(window.localStorage.getItem(RESUME_STORAGE_KEY) || "{}");
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return;
    delete raw[`${workspaceId}:${sessionId}`];
    window.localStorage.setItem(RESUME_STORAGE_KEY, JSON.stringify(raw));
    window.dispatchEvent(new CustomEvent("learngraph:voice-session-marker", { detail: { key: `${workspaceId}:${sessionId}`, resumable: false } }));
  } catch { /* storage is optional */ }
}

export function readVoiceSessionResumable(
  workspaceId: string,
  sessionId: string,
  maxAgeMs: number = VOICE_RESUME_MAX_AGE_MS,
): boolean {
  if (!workspaceId || !sessionId || typeof window === "undefined") return false;
  try {
    const raw = JSON.parse(window.localStorage.getItem(RESUME_STORAGE_KEY) || "{}");
    const at = Number(raw?.[`${workspaceId}:${sessionId}`]);
    if (!Number.isFinite(at) || at <= 0) return false;
    return Date.now() - at < maxAgeMs;
  } catch { return false; }
}
