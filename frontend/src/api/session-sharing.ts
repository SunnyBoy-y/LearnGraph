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

export function listSessionShares(
  sessionId: string,
): Promise<SessionShareDetailView[]> {
  return apiClient.get<SessionShareDetailView[]>(
    `/sessions/${encodeURIComponent(sessionId)}/shares`,
  );
}

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
