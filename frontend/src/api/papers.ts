import { apiClient } from "./client";
import type { PaperReadRequest, PaperReadResponse, PaperSaveRequest, PaperSearchRequest, PaperSearchResponse } from "@/types/papers";
import type { SourceRecord } from "@/types/workflow";

export function searchPapers(request: PaperSearchRequest): Promise<PaperSearchResponse> {
  return apiClient.post("/papers/search", request);
}

export function readPaper(request: PaperReadRequest): Promise<PaperReadResponse> {
  return apiClient.post("/papers/read", request);
}

export function savePaper(request: PaperSaveRequest): Promise<SourceRecord> {
  return apiClient.post("/papers/save", request);
}

export function listPaperLibrary(): Promise<SourceRecord[]> {
  return apiClient.get("/papers/library");
}

export function getPaperBibtex(sourceId: string): Promise<string> {
  return apiClient.get(`/papers/library/${encodeURIComponent(sourceId)}/bibtex`);
}
