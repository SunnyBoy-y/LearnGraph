import type {
  Artifact,
  ArtifactShareToken,
  ArtifactShareTokenCreated,
  ArtifactSummary,
  ArtifactVersion,
} from "@/types/artifacts";

import { apiClient } from "./client";

export function listArtifacts(): Promise<ArtifactSummary[]> {
  return apiClient.get<ArtifactSummary[]>("/artifacts");
}

export function createArtifact(payload: {
  name: string;
  description?: string;
}): Promise<Artifact> {
  return apiClient.post<Artifact, typeof payload>("/artifacts", payload);
}

export function listArtifactVersions(
  artifactId: string,
): Promise<ArtifactVersion[]> {
  return apiClient.get<ArtifactVersion[]>(
    `/artifacts/${encodeURIComponent(artifactId)}/versions`,
  );
}

export function publishArtifactVersion(
  artifactId: string,
  payload: {
    file_id: string;
    source_chat_session_id?: string | null;
    release_notes?: string;
  },
): Promise<ArtifactVersion> {
  return apiClient.post<ArtifactVersion, typeof payload>(
    `/artifacts/${encodeURIComponent(artifactId)}/versions`,
    payload,
  );
}

export function listArtifactShareTokens(
  versionId: string,
): Promise<ArtifactShareToken[]> {
  return apiClient.get<ArtifactShareToken[]>(
    `/artifacts/versions/${encodeURIComponent(versionId)}/share-tokens`,
  );
}

export function createArtifactShareToken(
  versionId: string,
  payload: {
    label?: string;
    expires_at?: string | null;
    max_downloads?: number | null;
  },
): Promise<ArtifactShareTokenCreated> {
  return apiClient.post<ArtifactShareTokenCreated, typeof payload>(
    `/artifacts/versions/${encodeURIComponent(versionId)}/share-tokens`,
    payload,
  );
}

export function revokeArtifactShareToken(
  tokenId: string,
): Promise<ArtifactShareToken> {
  return apiClient.delete<ArtifactShareToken>(
    `/artifacts/share-tokens/${encodeURIComponent(tokenId)}`,
  );
}

export function artifactShareUrl(rawToken: string): string {
  return `/api/v1/artifact-share/${encodeURIComponent(rawToken)}`;
}
