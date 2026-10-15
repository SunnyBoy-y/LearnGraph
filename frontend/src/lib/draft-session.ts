/** Client-side tracking for the unused empty chat draft. */

import { createUuid } from "@/lib/uuid";

const DRAFT_SESSION_STORAGE_KEY = "learngraph:draft-session-id";
const DRAFT_SESSION_PROJECT_STORAGE_KEY = "learngraph:draft-session-project-id";
const DRAFT_SESSION_CREATE_KEY_PREFIX = "learngraph:draft-session-create-key:";

/** Requests from the sidebar and /chat/new can overlap during route changes. */
const pendingDraftCreations = new Map<string, Promise<unknown>>();

/** Keep the temporary question-derived title compact in the conversation list. */
export const SESSION_TITLE_FALLBACK_MAX_CHARS = 24;

function normalizeTitleSeed(value: string | null | undefined): string {
  return (value ?? "").replace(/\s+/gu, " ").trim();
}

/**
 * Title shown immediately after the first question, while model naming is
 * still pending. The backend applies the same fixed-width rule when it saves
 * the message, so a refresh does not bring back the generic draft title.
 */
export function fallbackSessionTitle(
  question: string | null | undefined,
): string {
  // Array.from counts Unicode code points, matching Python's string slicing
  // on the backend (plain JS slice would split an emoji surrogate pair).
  return Array.from(normalizeTitleSeed(question))
    .slice(0, SESSION_TITLE_FALLBACK_MAX_CHARS)
    .join("");
}

export function isQuestionFallbackTitle(
  title: string | null | undefined,
  question: string | null | undefined,
): boolean {
  const fallback = fallbackSessionTitle(question);
  return Boolean(fallback) && normalizeTitleSeed(title) === fallback;
}

/** Fallback when sessionStorage is unavailable (tests / restricted browsers). */
let memoryDraftSessionId: string | null = null;
let memoryDraftSessionProjectId: string | null = null;

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

function readProjectStorage(): string | null {
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      const value = window.sessionStorage.getItem(
        DRAFT_SESSION_PROJECT_STORAGE_KEY,
      );
      return value?.trim() || null;
    }
  } catch {
    // Fall through to memory.
  }
  return memoryDraftSessionProjectId;
}

function writeProjectStorage(projectId: string | null): void {
  memoryDraftSessionProjectId = projectId;
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      if (!projectId) {
        window.sessionStorage.removeItem(DRAFT_SESSION_PROJECT_STORAGE_KEY);
        return;
      }
      window.sessionStorage.setItem(DRAFT_SESSION_PROJECT_STORAGE_KEY, projectId);
    }
  } catch {
    // Memory already holds the value for this tab session.
  }
}

function creationStorageKey(scope: string): string {
  return `${DRAFT_SESSION_CREATE_KEY_PREFIX}${scope}`;
}

function clearCreationKeys(): void {
  pendingDraftCreations.clear();
  try {
    if (typeof window === "undefined" || !window.sessionStorage) return;
    const keys: string[] = [];
    for (let index = 0; index < window.sessionStorage.length; index += 1) {
      const key = window.sessionStorage.key(index);
      if (key?.startsWith(DRAFT_SESSION_CREATE_KEY_PREFIX)) keys.push(key);
    }
    keys.forEach((key) => window.sessionStorage.removeItem(key));
  } catch {
    // Storage is optional; the in-memory pending map is still cleared.
  }
}

export function getDraftSessionId(): string | null {
  return readStorage();
}

export function getDraftSessionProjectId(): string | null {
  return readProjectStorage();
}

export function setDraftSessionId(
  sessionId: string | null,
  projectId: string | null = null,
): void {
  writeStorage(sessionId);
  writeProjectStorage(sessionId ? projectId : null);
}

export function clearDraftSessionId(sessionId?: string): void {
  if (sessionId && getDraftSessionId() !== sessionId) return;
  writeStorage(null);
  writeProjectStorage(null);
  clearCreationKeys();
}

/** Scope used by one empty draft identity (root or a project folder). */
export function draftSessionScope(
  workspaceId: string,
  projectId: string | null | undefined,
): string {
  return `${workspaceId}:${projectId ?? "root"}`;
}

/** Stable key for backend Idempotency-Key handling while this tab has storage. */
export function getDraftSessionCreationKey(scope: string): string {
  const key = creationStorageKey(scope);
  try {
    if (typeof window !== "undefined" && window.sessionStorage) {
      const existing = window.sessionStorage.getItem(key)?.trim();
      if (existing) return existing;
      const created = `draft-session:${createUuid()}`;
      window.sessionStorage.setItem(key, created);
      return created;
    }
  } catch {
    // Fall through to a per-call key when storage is unavailable.
  }
  return `draft-session:${createUuid()}`;
}

/** Share one in-flight draft creation between the shell and the chat route. */
export function getOrCreateDraftSession<T>(
  scope: string,
  factory: () => Promise<T>,
): Promise<T> {
  const pending = pendingDraftCreations.get(scope) as Promise<T> | undefined;
  if (pending) return pending;
  const created = factory();
  pendingDraftCreations.set(scope, created);
  void created.then(
    () => {
      if (pendingDraftCreations.get(scope) === created) {
        pendingDraftCreations.delete(scope);
      }
    },
    () => {
      if (pendingDraftCreations.get(scope) === created) {
        pendingDraftCreations.delete(scope);
      }
    },
  );
  return created;
}

/** Generic titles used for brand-new chats before the first message arrives. */
export function isDefaultDraftTitle(title: string | null | undefined): boolean {
  const value = title?.trim() ?? "";
  return value === "新会话" || value === "新学习会话";
}
