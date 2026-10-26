import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
} from "react";
import type { Dispatch, SetStateAction } from "react";
import { authStore } from "@/api/auth-store";

export const StreamUIStateScope = createContext("");

// Final-answer artifact placement can move a block between React parents.
// Keep choices by workspace + durable Part ID, including browser reloads.
// Only bounded UI preferences are saved; answer/source data stays on the server.
const blocks = new Map<string, Map<string, unknown>>();
const MAX_BLOCKS = 128;
const STORAGE_KEY = "learngraph.genui.preferences.v1";
const persistentKeys = new Set(["mode", "hidden", "limit", "query", "sort", "selected", "zoom"]);

function namespace(scope: string) {
  return `${authStore.getSession()?.userId ?? "guest"}:${authStore.getWorkspaceId() ?? "local"}:${scope}`;
}
function validPreference(key: string, value: unknown): boolean {
  if (key === "mode") return value === null || value === "line" || value === "bar";
  if (key === "limit") return [3, 5, 10, 500].includes(value as number);
  if (key === "query" || key === "selected" || key.endsWith(":tab")) return typeof value === "string" && value.length <= 500;
  if (key === "hidden") return Array.isArray(value) && value.length <= 12 && value.every(item => typeof item === "string" && /^[a-zA-Z0-9_-]{1,100}$/.test(item));
  if (key === "sort") return value === null || (typeof value === "object" && value !== null && "key" in value && typeof value.key === "string" && value.key.length <= 100 && "descending" in value && typeof value.descending === "boolean");
  if (key === "zoom") return typeof value === "number" && value >= .2 && value <= 4;
  if (key === "background") return value === null || ["auto", "paper", "warm", "cool", "dark"].includes(value as string);
  if (key === "graph_layout") return value === null || value === "flow" || value === "mindmap";
  if (key.endsWith(":parameter")) return typeof value === "number" && value >= -3 && value <= 5;
  if (key.endsWith(":step")) return Number.isInteger(value) && (value as number) >= 0 && (value as number) <= 435;
  if (key.endsWith(":open")) return typeof value === "boolean";
  return false;
}
function readSaved(): Record<string, Record<string, unknown>> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw || raw.length > 256_000) return {};
    const value: unknown = JSON.parse(raw);
    return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, Record<string, unknown>> : {};
  } catch { return {}; }
}
function save(scope: string, state: Map<string, unknown>) {
  try {
    const saved = readSaved();
    delete saved[scope];
    saved[scope] = Object.fromEntries([...state].filter(([key, value]) => validPreference(key, value)));
    const entries = Object.entries(saved).slice(-MAX_BLOCKS);
    const serialized = JSON.stringify(Object.fromEntries(entries));
    if (serialized.length <= 256_000) localStorage.setItem(STORAGE_KEY, serialized);
  } catch { /* Private browsing/quota failure must never break an answer. */ }
}

export function streamUIViewState(scope: string): Record<string, unknown> {
  return Object.fromEntries([...stateFor(scope)].filter(([key, value]) => persistentKeys.has(key) && ["mode", "hidden", "limit", "query", "sort"].includes(key) && validPreference(key, value)));
}

function stateFor(scope: string) {
  const scoped = namespace(scope);
  const stored = blocks.has(scoped) ? undefined : readSaved()[scoped];
  const state = blocks.get(scoped) ?? new Map<string, unknown>(stored && typeof stored === "object" && !Array.isArray(stored) ? Object.entries(stored).filter(([key, value]) => validPreference(key, value)) : []);
  blocks.delete(scoped);
  blocks.set(scoped, state);
  if (blocks.size > MAX_BLOCKS) blocks.delete(blocks.keys().next().value!);
  return state;
}

export function useStreamUIState<T>(
  key: string,
  initial: T,
): [T, Dispatch<SetStateAction<T>>] {
  const scope = useContext(StreamUIStateScope);
  const initialValue = useRef(initial);
  const [value, setValue] = useState<T>(() => {
    const state = stateFor(scope);
    if (!state.has(key)) state.set(key, initialValue.current);
    return state.get(key) as T;
  });
  const update = useCallback<Dispatch<SetStateAction<T>>>(
    (action) => {
      const state = stateFor(scope);
      const previous = state.has(key)
        ? (state.get(key) as T)
        : initialValue.current;
      const next =
        typeof action === "function"
          ? (action as (value: T) => T)(previous)
          : action;
      state.set(key, next);
      if (validPreference(key, next)) save(namespace(scope), state);
      setValue(next);
    },
    [scope, key],
  );
  return [value, update];
}
