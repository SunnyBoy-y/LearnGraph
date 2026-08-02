import type { QueryKey } from "@tanstack/react-query";

import { authStore } from "@/api/auth-store";

/**
 * Shared query-key factory for workspace- and identity-scoped React Query
 * caches.
 *
 * MAINTENANCE NOTE: this file is the non-parallelized shared cornerstone of the
 * query-key convention. Nearly every workspace-scoped feature (sidebar, chat,
 * graph, settings, dashboard) imports these helpers instead of inlining bare
 * keys, so changes here must be committed and landed ahead of follow-up audit
 * tasks (P0-B / P0-C / P0-D). Do NOT scatter per-domain key builders elsewhere;
 * if a domain needs a compound key, add a small helper in _this_ file.
 *
 * KEY SELECTION RULE: an endpoint that sends `X-Workspace-ID` (the vast
 * majority of scoped resources) MUST use a `*workspace*` key, with the
 * workspace segment FIRST so lifecycle cleanup can remove one tenant without
 * matching another tenant's entries. Only endpoints that deliberately opt out
 * of the workspace header — current-user and organization management — use
 * `identityQueryKey`. Using `identityQueryKey` for a workspace-scoped request
 * bleeds cache cross-tenant.
 */

/**
 * Canonical cache namespace for resources whose API request is scoped by
 * X-Workspace-ID. Keep the workspace segment first so lifecycle cleanup can
 * remove one tenant without matching another tenant's entries.
 */
export function workspaceQueryKey(
  workspaceId: string,
  ...parts: readonly unknown[]
): QueryKey {
  return ["workspace", workspaceId, ...parts];
}

/**
 * Convenience wrapper that reads the active workspace from `authStore`. Prefer
 * the explicit `workspaceQueryKey(workspaceId, ...)` form when a component
 * already has `workspaceId` in scope (route param / auth context) — that keeps
 * the dependency visible and avoids the `"__unscoped__"` fallback that only
 * exists so a call made before the session is ready still produces a stable
 * key instead of crashing.
 */
export function currentWorkspaceQueryKey(
  ...parts: readonly unknown[]
): QueryKey {
  return workspaceQueryKey(authStore.getWorkspaceId() ?? "__unscoped__", ...parts);
}

/**
 * Prefix for every cached resource belonging to one workspace. Used by
 * `clearWorkspaceClientState` to cancel + remove one tenant's entries on
 * workspace switch without touching other tenants or identity-scoped data.
 */
export function workspaceQueryPrefix(workspaceId: string): QueryKey {
  return ["workspace", workspaceId];
}

/**
 * Prefix for one resource family within a workspace — e.g.
 * `workspaceResourcePrefix(ws, "sessions")` invalidates every sessions query
 * for that workspace without touching projects / graphs / settings.
 */
export function workspaceResourcePrefix(
  workspaceId: string,
  resource: string,
): QueryKey {
  return workspaceQueryKey(workspaceId, resource);
}

/**
 * Identity-scoped entries are for endpoints that deliberately opt out of the
 * workspace header.
 *
 * WHICH ENDPOINTS QUALIFY: request does NOT carry `X-Workspace-ID`. Typical
 * examples are current-user (profile, preferences, sessions list) and
 * organization/account management — data that belongs to the account, not to
 * one workspace, and is therefore safe (and correct) to share across workspace
 * switches. If the endpoint you are keying sends `X-Workspace-ID`, use
 * `workspaceQueryKey` / `currentWorkspaceQueryKey` instead; using an identity
 * key there bleeds cache cross-tenant.
 */
export function identityQueryKey(
  userId: string | null | undefined,
  ...parts: readonly unknown[]
): QueryKey {
  return ["identity", userId ?? "anonymous", ...parts];
}
