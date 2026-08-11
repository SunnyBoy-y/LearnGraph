import type {
  Artifact,
  ArtifactCard,
  ArtifactCardPreview,
  ArtifactCardShareToken,
  ArtifactCardShareTokenCreated,
  ArtifactCardVersion,
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

export function updateArtifact(
  artifactId: string,
  payload: { name?: string; description?: string },
): Promise<Artifact> {
  return apiClient.patch<Artifact, typeof payload>(
    `/artifacts/${encodeURIComponent(artifactId)}`,
    payload,
  );
}

export function deleteArtifact(artifactId: string): Promise<Artifact> {
  return apiClient.delete<Artifact>(`/artifacts/${encodeURIComponent(artifactId)}`);
}

export function updateArtifactVersion(
  versionId: string,
  payload: { release_notes?: string },
): Promise<ArtifactVersion> {
  return apiClient.patch<ArtifactVersion, typeof payload>(
    `/artifacts/versions/${encodeURIComponent(versionId)}`,
    payload,
  );
}

export function deleteArtifactVersion(versionId: string): Promise<ArtifactVersion> {
  return apiClient.delete<ArtifactVersion>(
    `/artifacts/versions/${encodeURIComponent(versionId)}`,
  );
}

export function listArtifactCards(params?: {
  status?: string;
  card_type?: string;
  interactive?: boolean;
  sort?: string;
  order?: string;
  limit?: number;
  offset?: number;
}): Promise<ArtifactCard[]> {
  return apiClient.get<ArtifactCard[]>("/artifacts/cards", { query: params });
}

export function getArtifactCardPreview(
  cardId: string,
  params?: { version?: number },
): Promise<ArtifactCardPreview> {
  return apiClient.get<ArtifactCardPreview>(
    `/artifacts/cards/${encodeURIComponent(cardId)}/preview`,
    { query: params },
  );
}

export function deleteArtifactCard(cardId: string): Promise<ArtifactCard> {
  return apiClient.delete<ArtifactCard>(
    `/artifacts/cards/${encodeURIComponent(cardId)}`,
  );
}

export function publishArtifactCardVersion(
  cardId: string,
  payload: { release_notes?: string },
): Promise<ArtifactCardVersion> {
  return apiClient.post<ArtifactCardVersion, typeof payload>(
    `/artifacts/cards/${encodeURIComponent(cardId)}/versions`,
    payload,
  );
}

export function listArtifactCardVersions(
  cardId: string,
): Promise<ArtifactCardVersion[]> {
  return apiClient.get<ArtifactCardVersion[]>(
    `/artifacts/cards/${encodeURIComponent(cardId)}/versions`,
  );
}

export function deleteArtifactCardVersion(versionId: string): Promise<ArtifactCardVersion> {
  return apiClient.delete<ArtifactCardVersion>(
    `/artifacts/cards/versions/${encodeURIComponent(versionId)}`,
  );
}

export function listArtifactCardShareTokens(
  versionId: string,
): Promise<ArtifactCardShareToken[]> {
  return apiClient.get<ArtifactCardShareToken[]>(
    `/artifacts/cards/versions/${encodeURIComponent(versionId)}/share-tokens`,
  );
}

export function createArtifactCardShareToken(
  versionId: string,
  payload: {
    label?: string;
    expires_at?: string | null;
    max_views?: number | null;
  },
): Promise<ArtifactCardShareTokenCreated> {
  return apiClient.post<ArtifactCardShareTokenCreated, typeof payload>(
    `/artifacts/cards/versions/${encodeURIComponent(versionId)}/share-tokens`,
    payload,
  );
}

export function revokeArtifactCardShareToken(tokenId: string): Promise<ArtifactCardShareToken> {
  return apiClient.delete<ArtifactCardShareToken>(
    `/artifacts/cards/share-tokens/${encodeURIComponent(tokenId)}`,
  );
}

export function cardShareUrl(rawToken: string): string {
  return `/api/v1/card-share/${encodeURIComponent(rawToken)}`;
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
