import type { QueryClient } from "@tanstack/react-query";

import { workspaceQueryPrefix } from "@/lib/query-keys";
import { cancelSessionRefresh } from "@/lib/session-refresh";
import { clearPersistedQueryCache } from "@/lib/query-persistence";

let activeQueryClient: QueryClient | null = null;
let authInvalidated: (() => void) | null = null;

export function registerAuthQueryClient(queryClient: QueryClient) {
  activeQueryClient = queryClient;
}

export function registerAuthInvalidationHandler(handler: () => void) {
  authInvalidated = handler;
  return () => {
    if (authInvalidated === handler) authInvalidated = null;
  };
}

export async function invalidateAuthenticatedClient() {
  authInvalidated?.();
  await clearAuthenticatedClientState();
}

export async function clearWorkspaceClientState(workspaceId: string) {
  if (!workspaceId) return;
  clearPersistedQueryCache({ workspaceId });
  if (!activeQueryClient) return;
  // Match the canonical prefix from the query-key factory so this only ever
  // removes one tenant's entries; workspace segment stays first by construction.
  cancelSessionRefresh(activeQueryClient, workspaceId);
  const queryKey = workspaceQueryPrefix(workspaceId);
  await activeQueryClient.cancelQueries({ queryKey });
  activeQueryClient.removeQueries({ queryKey });
}

export async function clearAuthenticatedClientState() {
  clearPersistedQueryCache({ all: true });
  if (!activeQueryClient) return;
  cancelSessionRefresh(activeQueryClient);
  await activeQueryClient.cancelQueries();
  activeQueryClient.clear();
}
