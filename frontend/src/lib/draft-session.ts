/** Client-side tracking for the single unused empty chat draft. */

const DRAFT_SESSION_STORAGE_KEY = "learngraph:draft-session-id";

/** Fallback when sessionStorage is unavailable (tests / restricted browsers). */
let memoryDraftSessionId: string | null = null;

function readStorage(): string | null {
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      const value = window.sessionStorage.getItem(DRAFT_SESSION_STORAGE_KEY);
      return value?.trim() || null;
    }
  } catch {
    // Fall through to memory.
  }
  return memoryDraftSessionId;
}

function writeStorage(sessionId: string | null): void {
  memoryDraftSessionId = sessionId;
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      if (!sessionId) {
        window.sessionStorage.removeItem(DRAFT_SESSION_STORAGE_KEY);
        return;
      }
      window.sessionStorage.setItem(DRAFT_SESSION_STORAGE_KEY, sessionId);
    }
  } catch {
    // Memory already holds the value for this tab session.
  }
}

export function getDraftSessionId(): string | null {
  return readStorage();
}

export function setDraftSessionId(sessionId: string | null): void {
  writeStorage(sessionId);
}

export function clearDraftSessionId(sessionId?: string): void {
  if (sessionId && getDraftSessionId() !== sessionId) return;
  writeStorage(null);
}

/** Titles used for brand-new chats before the first message auto-titles them. */
export function isDefaultDraftTitle(title: string | null | undefined): boolean {
  const value = title?.trim() ?? "";
  return value === "新会话" || value === "新学习会话";
}
