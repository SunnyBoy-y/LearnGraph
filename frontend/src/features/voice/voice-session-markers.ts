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
