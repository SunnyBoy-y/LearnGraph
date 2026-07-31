import type { QueryClient } from "@tanstack/react-query";

let activeQueryClient: QueryClient | null = null;

export function registerAuthQueryClient(queryClient: QueryClient) {
  activeQueryClient = queryClient;
}

export async function clearAuthenticatedClientState() {
  if (!activeQueryClient) return;
  await activeQueryClient.cancelQueries();
  activeQueryClient.clear();
}
