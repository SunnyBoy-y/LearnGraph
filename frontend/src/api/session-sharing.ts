import { apiClient } from "./client";

export interface SessionShareTokenView {
  id: string;
  token_prefix: string;
  label: string;
  expires_at: string | null;
  max_views: number | null;
  view_count: number;
  last_viewed_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface SessionShareTokenCreated extends SessionShareTokenView {
  token: string;
}

export interface SessionShareMessageView {
  id: string;
  ordinal: number;
  role: string;
  content: string;
  parts: Array<Record<string, unknown>>;
  parent_message_id: string | null;
  created_at: string;
}

export interface SessionShareView {
  id: string;
  title: string;
  scope: string;
  message_count: number;
  status: string;
  created_by: string;
  created_at: string;
}

export interface SessionShareDetailView extends SessionShareView {
  tokens: SessionShareTokenView[];
}

/** Workspace-wide row shown by Settings → 会话与分享. */
export interface SessionShareManagementView extends SessionShareDetailView {
  session_id: string;
  source_session_id: string | null;
  session_title: string;
  /** Sum of all token reads belonging to this immutable snapshot. */
  view_count: number;
  last_viewed_at: string | null;
  active_token_count: number;
  token_id: string | null;
  token_prefix: string;
  label: string;
  expires_at: string | null;
  max_views: number | null;
  revoked_at: string | null;
}

export interface SessionSharePublicView {
  id: string;
  title: string;
  scope: string;
  message_count: number;
  created_at: string;
  messages: SessionShareMessageView[];
}

export interface SessionShareCreate {
  scope: "full" | "range" | "answers";
  from_message_id?: string;
  to_message_id?: string;
  answers_only?: boolean;
  label?: string;
  expires_at?: string | null;
  max_views?: number | null;
}

export function createSessionShare(
  sessionId: string,
  payload: SessionShareCreate,
): Promise<SessionShareTokenCreated> {
  return apiClient.post<SessionShareTokenCreated, SessionShareCreate>(
    `/sessions/${encodeURIComponent(sessionId)}/shares`,
    payload,
  );
}

/** List active shares for one session; pass include_revoked for audit history. */
export function listSessionShares(
  sessionId: string,
  options?: { include_revoked?: boolean },
): Promise<SessionShareDetailView[]> {
  return apiClient.get<SessionShareDetailView[]>(
    `/sessions/${encodeURIComponent(sessionId)}/shares`,
    { query: options },
  );
}

/**
 * List the active session shares in the current workspace.  The server filters
 * revoked snapshots by default, so a cancelled row drops out on refresh while
 * its snapshot and audit history remain retained.
 */
export function listSessionShareManagement(options?: {
  include_revoked?: boolean;
}): Promise<SessionShareManagementView[]> {
  return apiClient.get<SessionShareManagementView[]>('/session-shares', {
    query: options,
  });
}

/** Alias matching the artifact share management API naming. */
export const listAllSessionShares = listSessionShareManagement;

export function revokeSessionShare(shareId: string): Promise<SessionShareView> {
  return apiClient.delete<SessionShareView>(
    `/session-shares/${encodeURIComponent(shareId)}`,
  );
}

export function revokeSessionShareToken(
  shareId: string,
  tokenId: string,
): Promise<SessionShareTokenView> {
  return apiClient.delete<SessionShareTokenView>(
    `/session-shares/${encodeURIComponent(shareId)}/tokens/${encodeURIComponent(tokenId)}`,
  );
}

/** Public (unauthenticated) read-only payload for a shared session. */
export function fetchSharedSession(token: string): Promise<SessionSharePublicView> {
  return apiClient.get<SessionSharePublicView>(
    `/share/${encodeURIComponent(token)}`,
  );
}

/** Frontend route for the public read-only viewer. */
export function sessionShareUrl(rawToken: string): string {
  return `/share/${encodeURIComponent(rawToken)}`;
}
