export type PaperProvider = "openalex" | "arxiv";

export interface PaperSearchRequest {
  query: string;
  provider: PaperProvider;
  max_results?: number;
  year_from?: number;
  year_to?: number;
  author?: string;
  open_access_only?: boolean;
  sort?: "relevance" | "newest";
}

export interface Paper {
  provider: PaperProvider;
  paper_id: string;
  title: string;
  authors: string[];
  year: number | null;
  published_at: string | null;
  venue: string;
  doi: string | null;
  url: string;
  abstract: string;
  pdf_url: string | null;
  open_access: boolean;
  publication_type: string;
  cited_by_count: number | null;
  fetched_at: string;
  evidence_kind: "metadata" | "abstract";
  page_read: false;
}

export interface PaperSearchResponse {
  query: string;
  provider: string;
  results: Paper[];
  notice: string;
}

export interface PaperReference {
  provider: PaperProvider;
  paper_id: string;
}

export interface PaperReadRequest extends PaperReference {
  authorized_domains?: string[];
}

export interface PaperExcerpt {
  locator: string;
  content: string;
}

export interface PaperReadResponse {
  paper: Paper;
  url: string;
  content_hash: string;
  excerpts: PaperExcerpt[];
  evidence_kind: "full_text";
  page_read: true;
  truncated: boolean;
  notice: string;
}

export interface PaperSaveRequest extends PaperReadRequest {
  include_full_text?: boolean;
}
