import { ApiClient } from '@/api/client'

export function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json', ...init.headers },
    ...init,
  })
}

export function createWorkspaceApiClient(
  workspaceId: string,
  fetcher: typeof fetch,
): ApiClient {
  return new ApiClient({
    fetch: fetcher,
    workspaceId: () => workspaceId,
    accessToken: () => null,
  })
}
