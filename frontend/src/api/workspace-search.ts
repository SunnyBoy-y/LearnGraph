import { apiClient } from "./client";

export type WorkspaceSearchKind = "all" | "session" | "message" | "file";
export interface WorkspaceSearchHit {
  kind: Exclude<WorkspaceSearchKind, "all">;
  id: string;
  title: string;
  snippet: string;
  session_id: string | null;
  updated_at: string;
  match_field: "id" | "title" | "content";
}
export interface WorkspaceSearchPage {
  items: WorkspaceSearchHit[];
  has_more: boolean;
  next_offset: number;
}

export function searchWorkspace(q: string, kind: WorkspaceSearchKind, offset = 0, signal?: AbortSignal) {
  return apiClient.get<WorkspaceSearchPage>("/workspace/search", {
    query: { q, kind, offset, limit: 20 }, signal,
  });
}
