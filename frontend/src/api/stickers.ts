import { apiClient } from './client';

export interface StickerSemantics {
  description: string;
  ocr: string;
  tags: string[];
  emotion: string[];
  intent: string[];
  use_when: string[];
  avoid_when: string[];
  standalone: boolean;
}

export interface Sticker {
  id: string;
  pack_id: string;
  name: string;
  content_hash: string;
  mime_type: string;
  semantics: StickerSemantics;
  needs_description: boolean;
}

export interface StickerSettings {
  mode: 'follow' | 'independent' | 'keyword';
  provider_id: string;
  model_id: string;
  ai_enabled: boolean;
}

export interface StickerStatus extends StickerSettings {
  keyword_available: boolean;
  index: {
    status: string;
    completed: number;
    total: number;
    error: string | null;
    model_id: string | null;
    fingerprint: string | null;
    active_model_id: string | null;
  };
  packs: Array<{ id: string; name: string; builtin: boolean; enabled: boolean; license: string; source: string }>;
}

export const getStickerStatus = () => apiClient.get<StickerStatus>('/stickers');
export const searchStickers = (query: string, semantic = false, options: { packId?: string; offset?: number } = {}) => apiClient.get<{ method: string; total: number; candidates: Sticker[] }>('/stickers/search', { query: { q: query, semantic, limit: 24, pack_id: options.packId, offset: options.offset } });
export const configureStickers = (settings: Partial<StickerSettings>) => apiClient.put<StickerStatus>('/stickers/settings', settings);
export const rebuildStickerIndex = () => apiClient.post<StickerStatus>('/stickers/rebuild', {});
export const setStickerPackEnabled = (id: string, enabled: boolean) => apiClient.put<StickerStatus>(`/stickers/packs/${encodeURIComponent(id)}`, { enabled });
export const updateSticker = (sticker: Sticker) => apiClient.put<Sticker>(`/stickers/${encodeURIComponent(sticker.id)}`, { name: sticker.name, semantics: sticker.semantics });
export const stickerBlob = (id: string, poster: boolean) => apiClient.getBlob(`/stickers/${encodeURIComponent(id)}/content`, { query: { poster } }, 5 * 1024 * 1024);

export interface StickerMarketPack {
  id: string;
  name: string;
  description: string;
  category: string;
  author: string;
  version: string;
  source: string;
  license: string;
  license_id: string;
  style: string;
  count: number;
  bytes: number;
  installed_pack_id: string | null;
  enabled: boolean;
  previews: Array<{ index: number; name: string; description: string }>;
  source_id?: string;
  attribution?: string;
  license_url?: string;
  approval_required?: boolean;
}

export interface StickerPipelineStatus {
  auto_analyze: boolean;
  available: boolean;
  model_id: string | null;
  queued: number;
  failed: number;
  completed: number;
  packs: Array<{ pack_id: string; queued: number; failed: number }>;
  assets?: Array<{ sticker_id: string; status: string }>;
}

export const getStickerMarket = (query = '') => apiClient.get<{ packs: StickerMarketPack[]; policy: string; total_packs?: number; total_assets?: number; sources?: string[] }>('/stickers/market', { query: { q: query } });
export const installMarketPack = (id: string) => apiClient.post<{ pack_id: string; imported: number }>(`/stickers/market/${encodeURIComponent(id)}/install`, {});
export const marketStickerBlob = (id: string, index: number) => apiClient.getBlob(`/stickers/market/${encodeURIComponent(id)}/preview/${index}`, undefined, 5 * 1024 * 1024);
export const downloadStickerPack = (id: string, market = false) => apiClient.getBlob(`/stickers/${market ? 'market' : 'packs'}/${encodeURIComponent(id)}/download`, undefined, 32 * 1024 * 1024);
export const getStickerPipeline = () => apiClient.get<StickerPipelineStatus>('/stickers/pipeline');
export const configureStickerPipeline = (auto_analyze: boolean) => apiClient.put<StickerPipelineStatus>('/stickers/pipeline', { auto_analyze });
export const analyzeStickerPack = (id: string) => apiClient.post<{ queued: number }>(`/stickers/packs/${encodeURIComponent(id)}/analyze`, { consent: true });
export const getSticker = (id: string) => apiClient.get<Sticker>(`/stickers/${encodeURIComponent(id)}`);
export const analyzeSticker = (id: string) => apiClient.post<{ queued: number }>(`/stickers/${encodeURIComponent(id)}/analyze`, { consent: true });

export function importStickerPack(files: File[], name: string) {
  const form = new FormData();
  for (const file of files) form.append('files', file);
  form.set('name', name);
  return apiClient.upload<{ pack_id: string; imported: number; duplicates: number; private: boolean }>('/stickers/import', form);
}
