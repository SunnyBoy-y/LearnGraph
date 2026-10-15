import {
  dehydrate,
  hydrate,
  type DehydratedState,
  type QueryClient,
} from "@tanstack/react-query";

import { authStore } from "@/api/auth-store";

/**
 * Small, deliberately bounded persistence layer for the React Query cache.
 *
 * The cache is namespaced by account and workspace, so a refresh can paint the
 * last known page immediately without ever showing another tenant's data.
 * Network requests still refresh stale entries in the background.
 */
const STORAGE_PREFIX = "learngraph:query-cache:v1:";
const CACHE_VERSION = 1;
const MAX_CACHE_AGE_MS = 24 * 60 * 60 * 1000;
// Keep enough room for other application settings in browsers with a 5 MB
// localStorage quota. Query data is already JSON, so characters are a useful
// conservative proxy for bytes here.
const MAX_CACHE_CHARS = 2_000_000;
const MAX_QUERY_CHARS = 1_500_000;
const WRITE_DELAY_MS = 650;
const SENSITIVE_KEY_PARTS =
  /(?:settings?|provider|token|secret|password|credential|authorization|api[-_]?key|private[-_]?key)/iu;

type PersistedEnvelope = {
  version: number;
  savedAt: number;
  state: DehydratedState;
};

type CacheScope = {
  identity: string;
  storageScope: string;
  workspaceId: string;
};

function getStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function stableHash(value: string): string {
  // FNV-1a is only used to make a stable namespace when an older server does
  // not return userId. It is not used as a security primitive.
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
}

function encodeScopePart(value: string): string {
  return encodeURIComponent(value);
}

function activeScope(): CacheScope | null {
  const session = authStore.getSession();
  if (!session?.workspaceId) return null;
  const identity =
    session.userId?.trim() ||
    session.username?.trim() ||
    stableHash(session.accessToken);
  return {
    identity,
    storageScope: `${encodeScopePart(identity)}:${encodeScopePart(session.workspaceId)}`,
    workspaceId: session.workspaceId,
  };
}

function storageKey(scope: string): string {
  return `${STORAGE_PREFIX}${scope}`;
}

function isPersistableQuery(
  query: {
    queryKey: readonly unknown[];
    state: { status: string; data: unknown };
  },
  scope: CacheScope,
): boolean {
  if (query.state.status !== "success" || query.state.data === undefined) {
    return false;
  }
  const namespace = query.queryKey[0];
  if (namespace !== "workspace" && namespace !== "identity") return false;
  if (namespace === "workspace" && query.queryKey[1] !== scope.workspaceId) {
    return false;
  }
  if (
    namespace === "identity" &&
    query.queryKey[1] !== scope.identity &&
    query.queryKey[1] !== "anonymous"
  ) {
    return false;
  }
  // Query keys are intentionally cheap to inspect here. Avoid putting a
  // future credential-bearing endpoint into localStorage just because it was
  // added under the normal workspace namespace.
  return !query.queryKey.some(
    (part) => typeof part === "string" && SENSITIVE_KEY_PARTS.test(part),
  );
}

function trimState(state: DehydratedState): DehydratedState {
  const queries = [...state.queries].sort(
    (left, right) => right.state.dataUpdatedAt - left.state.dataUpdatedAt,
  );
  const kept = [] as DehydratedState["queries"];
  let totalChars = 64;

  for (const query of queries) {
    let queryChars = 0;
    try {
      queryChars = JSON.stringify(query).length;
    } catch {
      continue;
    }
    if (queryChars > MAX_QUERY_CHARS) continue;
    if (totalChars + queryChars > MAX_CACHE_CHARS) continue;
    kept.push(query);
    totalChars += queryChars;
  }

  return { mutations: [], queries: kept };
}

function persistQueryCache(queryClient: QueryClient): void {
  const scope = activeScope();
  const storage = getStorage();
  if (!scope || !storage) return;

  try {
    const state = trimState(
      dehydrate(queryClient, {
        shouldDehydrateQuery: (query) => isPersistableQuery(query, scope),
      }),
    );
    const envelope: PersistedEnvelope = {
      version: CACHE_VERSION,
      savedAt: Date.now(),
      state,
    };
    const serialized = JSON.stringify(envelope);
    if (serialized.length > MAX_CACHE_CHARS) return;
    storage.setItem(storageKey(scope.storageScope), serialized);
  } catch {
    // Quota errors, circular application data, and private browsing policies
    // must never make the live query client unusable.
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readEnvelope(
  raw: string,
  scope: CacheScope,
): PersistedEnvelope | null {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) return null;
    if (parsed.version !== CACHE_VERSION || typeof parsed.savedAt !== "number") {
      return null;
    }
    if (Date.now() - parsed.savedAt > MAX_CACHE_AGE_MS) return null;

    const state = parsed.state;
    if (!isRecord(state) || !Array.isArray(state.queries)) return null;
    const queries = state.queries.filter(
      (query): query is DehydratedState["queries"][number] => {
        if (
          !isRecord(query) ||
          !Array.isArray(query.queryKey) ||
          !isRecord(query.state)
        ) {
          return false;
        }
        const namespace = query.queryKey[0];
        if (
          query.queryKey.some(
            (part) =>
              typeof part === "string" && SENSITIVE_KEY_PARTS.test(part),
          )
        ) {
          return false;
        }
        return (
          ((namespace === "workspace" &&
            query.queryKey[1] === scope.workspaceId) ||
            (namespace === "identity" &&
              (query.queryKey[1] === scope.identity ||
                query.queryKey[1] === "anonymous"))) &&
          query.state.status === "success"
        );
      },
    );
    return {
      version: CACHE_VERSION,
      savedAt: parsed.savedAt,
      state: { mutations: [], queries },
    };
  } catch {
    return null;
  }
}

/** Hydrate the active account/workspace cache before React starts rendering. */
export function hydratePersistedQueryCache(queryClient: QueryClient): void {
  const scope = activeScope();
  const storage = getStorage();
  if (!scope || !storage) return;

  const key = storageKey(scope.storageScope);
  let raw: string | null = null;
  try {
    raw = storage.getItem(key);
  } catch {
    return;
  }
  if (!raw) return;

  const envelope = readEnvelope(raw, scope);
  if (!envelope) {
    try {
      storage.removeItem(key);
    } catch {
      // Ignore unavailable storage.
    }
    return;
  }
  try {
    hydrate(queryClient, envelope.state);
  } catch {
    // A stale schema or malformed cached value should fall back to the API.
    try {
      storage.removeItem(key);
    } catch {
      // Ignore unavailable storage.
    }
  }
}

/**
 * Subscribe once to cache changes and write a debounced snapshot. The caller
 * owns the returned cleanup function (the app normally lives for the whole
 * browser tab).
 */
export function startPersistedQueryCache(queryClient: QueryClient): () => void {
  if (typeof window === "undefined") return () => undefined;

  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  const flush = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    if (!stopped) persistQueryCache(queryClient);
  };
  const schedule = () => {
    if (stopped) return;
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      persistQueryCache(queryClient);
    }, WRITE_DELAY_MS);
  };

  const unsubscribe = queryClient.getQueryCache().subscribe((event) => {
    if (
      event.type === "added" ||
      event.type === "updated"
    ) {
      schedule();
    }
  });
  window.addEventListener("pagehide", flush);
  window.addEventListener("beforeunload", flush);

  return () => {
    stopped = true;
    unsubscribe();
    window.removeEventListener("pagehide", flush);
    window.removeEventListener("beforeunload", flush);
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };
}

/** Remove persisted entries when a tenant is left or an account logs out. */
export function clearPersistedQueryCache(options?: {
  workspaceId?: string;
  all?: boolean;
}): void {
  const storage = getStorage();
  if (!storage) return;

  try {
    if (options?.all) {
      for (let index = storage.length - 1; index >= 0; index -= 1) {
        const key = storage.key(index);
        if (key?.startsWith(STORAGE_PREFIX)) storage.removeItem(key);
      }
      return;
    }

    if (options?.workspaceId) {
      const suffix = `:${encodeScopePart(options.workspaceId)}`;
      for (let index = storage.length - 1; index >= 0; index -= 1) {
        const key = storage.key(index);
        if (key?.startsWith(STORAGE_PREFIX) && key.endsWith(suffix)) {
          storage.removeItem(key);
        }
      }
      return;
    }

    const scope = activeScope();
    if (scope) storage.removeItem(storageKey(scope.storageScope));
  } catch {
    // Storage is an optimization; cleanup failures are non-fatal.
  }
}
