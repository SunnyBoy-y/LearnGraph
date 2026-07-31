import type { QueryClient } from "@tanstack/react-query";

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

export async function clearAuthenticatedClientState() {
  if (!activeQueryClient) return;
  await activeQueryClient.cancelQueries();
  activeQueryClient.clear();
}
