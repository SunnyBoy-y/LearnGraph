import type { IsoDateTime } from "./common";

export interface Artifact {
  id: string;
  tenant_id: string;
  workspace_id: string;
  created_by: string;
  name: string;
  description: string;
  status: string;
  created_at: IsoDateTime;
}

export interface ArtifactSummary extends Artifact {
  version_count: number;
}

export interface ArtifactVersion {
  id: string;
  artifact_id: string;
  version: number;
  file_id: string;
  original_name: string;
  sha256: string;
  size_bytes: number;
  mime_type: string;
  source_workspace_id: string;
  source_chat_session_id: string | null;
  published_by: string;
  release_notes: string;
  status: string;
  created_at: IsoDateTime;
}

export interface ArtifactShareToken {
  id: string;
  artifact_version_id: string;
  token_prefix: string;
  label: string;
  expires_at: IsoDateTime | null;
  max_downloads: number | null;
  download_count: number;
  revoked_at: IsoDateTime | null;
  created_at: IsoDateTime;
}

export interface ArtifactShareTokenCreated extends ArtifactShareToken {
  token: string;
}

/** Indexed chat card (magic_card / declarative component) in the artifacts page. */
export interface ArtifactCard {
  id: string;
  card_id: string;
  card_instance_id: string;
  card_type: "magic_card" | "component";
  interactive: boolean;
  title: string;
  status: "draft" | "published" | "deleted";
  chat_session_id: string | null;
  message_id: string | null;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

/** Full render data for previewing a card in a popup window. */
export interface ArtifactCardPreview extends ArtifactCard {
  preview_snapshot: Record<string, unknown>;
}
