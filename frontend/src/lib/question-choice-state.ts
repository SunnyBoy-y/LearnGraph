import { authStore } from "@/api/auth-store";

const STORAGE_KEY = "learngraph.question.choices.v1";
const MAX_ENTRIES = 256;
const MAX_BYTES = 256_000;

const memoryCache = new Map<string, unknown>();

function scopedKey(key: string): string {
  const userId = authStore.getSession()?.userId ?? "guest";
  const workspaceId = authStore.getWorkspaceId() ?? "local";
  return `${userId}:${workspaceId}:${key}`;
}

function readAllFromStorage(): Record<string, unknown> {
  try {
    if (typeof window === "undefined" || !window.localStorage) return {};
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw || raw.length > MAX_BYTES) return {};
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

function writeAllToStorage(record: Record<string, unknown>): void {
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    let entries = Object.entries(record).slice(-MAX_ENTRIES);
    let serialized = JSON.stringify(Object.fromEntries(entries));
    while (serialized.length > MAX_BYTES && entries.length > 1) {
      entries = entries.slice(Math.ceil(entries.length / 4));
      serialized = JSON.stringify(Object.fromEntries(entries));
    }
    if (serialized.length <= MAX_BYTES) {
      window.localStorage.setItem(STORAGE_KEY, serialized);
    }
  } catch {
    // Private browsing or quota errors must never break quiz interactions.
  }
}

export function readQuestionChoiceState<T>(key: string): T | null {
  if (!key) return null;
  const fullKey = scopedKey(key);
  if (memoryCache.has(fullKey)) {
    return (memoryCache.get(fullKey) as T) ?? null;
  }
  const stored = readAllFromStorage();
  if (!(fullKey in stored)) return null;
  const value = stored[fullKey] as T;
  memoryCache.set(fullKey, value);
  if (memoryCache.size > MAX_ENTRIES) {
    const oldest = memoryCache.keys().next().value;
    if (oldest !== undefined) memoryCache.delete(oldest);
  }
  return value;
}

export function writeQuestionChoiceState<T>(key: string, value: T): void {
  if (!key) return;
  const fullKey = scopedKey(key);
  memoryCache.delete(fullKey);
  memoryCache.set(fullKey, value);
  if (memoryCache.size > MAX_ENTRIES) {
    const oldest = memoryCache.keys().next().value;
    if (oldest !== undefined) memoryCache.delete(oldest);
  }
  const stored = readAllFromStorage();
  delete stored[fullKey];
  stored[fullKey] = value;
  writeAllToStorage(stored);
}

export function clearQuestionChoiceState(): void {
  memoryCache.clear();
  try {
    if (typeof window !== "undefined" && window.localStorage) {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // Ignore storage errors on cleanup.
  }
}
