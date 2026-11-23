import { createContext, useCallback, useContext, useSyncExternalStore } from "react";

const RESERVED_KEYS = new Set(["__proto__", "prototype", "constructor"]);
const DATA_BINDING_RE = /^@\/[a-zA-Z0-9_-]+(?:\/[a-zA-Z0-9_-]+)*$/;
const DATA_PATH_RE = /^@?\/[a-zA-Z0-9_-]+(?:\/[a-zA-Z0-9_-]+)*$/;
const DATA_TEMPLATE_RE = /@\{(\/[a-zA-Z0-9_-]+(?:\/[a-zA-Z0-9_-]+)*)\}/g;

export function isDataBinding(value: unknown): value is `@/${string}` {
  return typeof value === "string" && value.length <= 200 && DATA_BINDING_RE.test(value);
}

export function parseDataPath(expr: string): string[] | null {
  if (typeof expr !== "string" || expr.length > 200 || !DATA_PATH_RE.test(expr)) {
    return null;
  }
  const raw = expr.startsWith("@") ? expr.slice(1) : expr;
  const parts = raw.split("/").filter(Boolean);
  if (!parts.length || parts.length > 8 || parts.some((part) => RESERVED_KEYS.has(part))) {
    return null;
  }
  return parts;
}

export function normalizeBindingPath(expr: string): string | null {
  const parts = parseDataPath(expr);
  return parts ? `/${parts.join("/")}` : null;
}

export function extractBindingPaths(value: unknown): string[] {
  if (typeof value !== "string") return [];
  if (isDataBinding(value)) {
    const normalized = normalizeBindingPath(value);
    return normalized ? [normalized] : [];
  }
  if (!value.includes("@{")) return [];
  const paths = new Set<string>();
  DATA_TEMPLATE_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = DATA_TEMPLATE_RE.exec(value)) !== null) {
    const normalized = normalizeBindingPath(match[1]);
    if (normalized) paths.add(normalized);
  }
  return Array.from(paths);
}

export function readDataPath(data: Record<string, unknown>, expr: string): unknown {
  const parts = parseDataPath(expr);
  if (!parts) return undefined;
  let current: unknown = data;
  for (const part of parts) {
    if (Array.isArray(current) && /^\d+$/.test(part)) {
      const idx = Number(part);
      if (idx < 0 || idx >= current.length) return undefined;
      current = current[idx];
    } else if (current && typeof current === "object" && !Array.isArray(current)) {
      const record = current as Record<string, unknown>;
      if (!Object.hasOwn(record, part)) return undefined;
      current = record[part];
    } else {
      return undefined;
    }
  }
  return current;
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== typeof b || a === null || b === null) return false;
  if (typeof a !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
      if (!deepEqual(a[i], b[i])) return false;
    }
    return true;
  }
  const objA = a as Record<string, unknown>;
  const objB = b as Record<string, unknown>;
  const keysA = Object.keys(objA);
  const keysB = Object.keys(objB);
  if (keysA.length !== keysB.length) return false;
  for (const key of keysA) {
    if (!Object.hasOwn(objB, key) || !deepEqual(objA[key], objB[key])) return false;
  }
  return true;
}

export class DataModelStore {
  private data: Record<string, unknown>;
  private listeners = new Map<string, Set<() => void>>();
  private pathVersions = new Map<string, number>();
  private version = 0;

  constructor(initial: Record<string, unknown> = {}) {
    this.data = initial;
  }

  getDataModel(): Record<string, unknown> {
    return this.data;
  }

  getAtPath(path: string): unknown {
    return readDataPath(this.data, path);
  }

  getPathsStamp(paths: readonly string[]): string {
    if (!paths.length) return "";
    return paths.map((p) => `${p}:${this.pathVersions.get(p) ?? 0}`).join("|");
  }

  resolveValue(value: unknown): unknown {
    if (typeof value !== "string") return value;
    if (isDataBinding(value)) {
      return readDataPath(this.data, value);
    }
    if (value.includes("@{")) {
      return this.resolveText(value);
    }
    return value;
  }

  resolveText(value: string): string {
    if (isDataBinding(value)) {
      const resolved = readDataPath(this.data, value);
      if (resolved == null) return "";
      return typeof resolved === "number" ? resolved.toLocaleString() : String(resolved);
    }
    if (!value.includes("@{")) return value;
    DATA_TEMPLATE_RE.lastIndex = 0;
    return value.replace(DATA_TEMPLATE_RE, (_full, rawPath: string) => {
      const resolved = readDataPath(this.data, rawPath);
      if (resolved == null) return "";
      return typeof resolved === "number" ? resolved.toLocaleString() : String(resolved);
    });
  }

  subscribe(path: string, listener: () => void): () => void {
    const normalized = normalizeBindingPath(path) ?? "";
    let set = this.listeners.get(normalized);
    if (!set) {
      set = new Set();
      this.listeners.set(normalized, set);
    }
    set.add(listener);
    return () => {
      const current = this.listeners.get(normalized);
      if (!current) return;
      current.delete(listener);
      if (current.size === 0) {
        this.listeners.delete(normalized);
      }
    };
  }

  subscribePaths(paths: readonly string[], listener: () => void): () => void {
    if (!paths.length) return () => {};
    const unsubs = paths.map((p) => this.subscribe(p, listener));
    return () => {
      for (const unsub of unsubs) unsub();
    };
  }

  setDataModel(next: Record<string, unknown>): void {
    const prev = this.data;
    if (deepEqual(prev, next)) return;
    this.data = next;
    this.version += 1;

    const toNotify = new Set<() => void>();
    for (const [subPath, set] of this.listeners.entries()) {
      const prevVal = subPath ? readDataPath(prev, subPath) : prev;
      const nextVal = subPath ? readDataPath(next, subPath) : next;
      if (!deepEqual(prevVal, nextVal)) {
        if (subPath) {
          this.pathVersions.set(subPath, this.version);
        }
        for (const listener of set) {
          toNotify.add(listener);
        }
      }
    }
    for (const listener of toNotify) {
      listener();
    }
  }
}

export const DataModelStoreContext = createContext<DataModelStore | null>(null);

export function useDataModelStore(): DataModelStore | null {
  return useContext(DataModelStoreContext);
}

export function useDataModelPath(path: string | null | undefined): unknown {
  const store = useContext(DataModelStoreContext);
  const normalized = path ? normalizeBindingPath(path) : null;
  return useSyncExternalStore(
    useCallback(
      (onStoreChange) => {
        if (!store || !normalized) return () => {};
        return store.subscribe(normalized, onStoreChange);
      },
      [store, normalized],
    ),
    () => {
      if (!store || !normalized) return undefined;
      return store.getAtPath(normalized);
    },
  );
}

export function useDataModelPaths(paths: readonly string[]): string {
  const store = useContext(DataModelStoreContext);
  const key = paths.join("|");
  return useSyncExternalStore(
    useCallback(
      (onStoreChange) => {
        if (!store || !paths.length) return () => {};
        return store.subscribePaths(paths, onStoreChange);
      },
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [store, key],
    ),
    () => {
      if (!store || !paths.length) return "";
      return store.getPathsStamp(paths);
    },
  );
}

export function useBoundValue<T>(value: T): unknown {
  const store = useContext(DataModelStoreContext);
  const paths = extractBindingPaths(value);
  const key = paths.join("|");
  return useSyncExternalStore(
    useCallback(
      (onStoreChange) => {
        if (!store || !paths.length) return () => {};
        return store.subscribePaths(paths, onStoreChange);
      },
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [store, key],
    ),
    () => {
      if (!store || !paths.length) return value;
      return store.resolveValue(value);
    },
  );
}

export function useBoundText(value: string): string {
  const store = useContext(DataModelStoreContext);
  const paths = extractBindingPaths(value);
  const key = paths.join("|");
  return useSyncExternalStore(
    useCallback(
      (onStoreChange) => {
        if (!store || !paths.length) return () => {};
        return store.subscribePaths(paths, onStoreChange);
      },
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [store, key],
    ),
    () => {
      if (!store || !paths.length) return value;
      return store.resolveText(value);
    },
  );
}
