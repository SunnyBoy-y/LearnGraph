import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import { StickerSettingsPanel } from './sticker-settings';
import { StickerPicker } from '@/components/chat/sticker-picker';
import * as api from '@/api/stickers';
import type { Sticker, StickerStatus } from '@/api/stickers';

vi.mock('@/api', () => ({ listProviders: vi.fn(async () => []) }));
vi.mock('@/api/stickers', () => ({ getStickerStatus: vi.fn(), searchStickers: vi.fn(), configureStickers: vi.fn(), rebuildStickerIndex: vi.fn(), importStickerPack: vi.fn(), updateSticker: vi.fn(), setStickerPackEnabled: vi.fn(), getStickerPipeline: vi.fn(), configureStickerPipeline: vi.fn(), analyzeStickerPack: vi.fn(), downloadStickerPack: vi.fn(), getStickerMarket: vi.fn(), installMarketPack: vi.fn(), marketStickerBlob: vi.fn(), getSticker: vi.fn(), analyzeSticker: vi.fn() }));
vi.mock('@/features/auth/auth-context-value', () => ({ useAuth: () => ({ workspaceId: 'ws-stickers', userId: 'owner', effectivePermissions: ['workspace.manage'] }) }));
vi.mock('@/components/chat/sticker-image', () => ({ StickerImage: ({ alt }: { alt: string }) => <img alt={alt} /> }));

const sticker: Sticker = { id: 'cat', pack_id: 'private', name: '坏笑猫', content_hash: 'hash', mime_type: 'image/png', needs_description: false, semantics: { description: '友好调侃', ocr: '', tags: ['得意'], emotion: [], intent: ['调侃'], use_when: ['朋友间玩笑'], avoid_when: ['悲伤'], standalone: true } };
let status: StickerStatus;

beforeEach(() => {
  vi.clearAllMocks();
  status = { mode: 'follow', provider_id: '', model_id: '', ai_enabled: true, keyword_available: true, index: { status: 'keyword', completed: 0, total: 1, error: null, model_id: null, fingerprint: null, active_model_id: null }, packs: [{ id: 'private', name: '我的猫咪', builtin: false, enabled: true, license: '私有素材', source: 'private-import' }] };
  vi.mocked(api.getStickerStatus).mockImplementation(async () => status);
  vi.mocked(api.searchStickers).mockResolvedValue({ method: 'keyword', total: 1, candidates: [sticker] });
  vi.mocked(api.configureStickers).mockImplementation(async patch => ({ ...status, ...patch }));
  vi.mocked(api.importStickerPack).mockResolvedValue({ pack_id: 'new', imported: 1, duplicates: 0, private: true });
  vi.mocked(api.updateSticker).mockImplementation(async value => value);
  vi.mocked(api.getStickerPipeline).mockResolvedValue({ auto_analyze: false, available: false, model_id: null, queued: 0, completed: 0, failed: 0, packs: [] });
  vi.mocked(api.getSticker).mockResolvedValue(sticker);
  vi.mocked(api.analyzeSticker).mockResolvedValue({ queued: 1 });
});

describe('sticker integration', () => {
  it('shows keyword fallback and changes retrieval mode through persisted settings', async () => {
    renderWithProviders(<StickerSettingsPanel />);
    expect(await screen.findByText('关键词检索始终可用')).toBeInTheDocument();
    expect(await screen.findByText('坏笑猫')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('检索方式'), { target: { value: 'keyword' } });
    await waitFor(() => expect(api.configureStickers).toHaveBeenCalledWith({ mode: 'keyword' }, expect.anything()));
  });

  it('imports the selected files and preserves private import semantics', async () => {
    renderWithProviders(<StickerSettingsPanel />);
    const file = new File(['fixture'], 'cat.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText('选择要导入的表情文件'), { target: { files: [file] } });
    await waitFor(() => expect(api.importStickerPack).toHaveBeenCalledWith([file], ''));
    expect(await screen.findByText('个人表情库 · 私有')).toBeInTheDocument();
  });

  it('edits semantic tags without losing separators while typing', async () => {
    renderWithProviders(<StickerSettingsPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /坏笑猫/ }));
    fireEvent.change(screen.getByLabelText('关键词（用逗号分隔）'), { target: { value: '得意，心虚，' } });
    fireEvent.change(screen.getByLabelText('图片配字（OCR）'), { target: { value: '嘿嘿' } });
    expect(screen.getByLabelText('关键词（用逗号分隔）')).toHaveValue('得意，心虚，');
    fireEvent.click(screen.getByRole('button', { name: '保存描述' }));
    await waitFor(() => expect(api.updateSticker).toHaveBeenCalledWith(expect.objectContaining({ semantics: expect.objectContaining({ tags: ['得意', '心虚'], ocr: '嘿嘿' }) }), expect.anything()));
  });

  it('keeps keyword availability visible when vector indexing fails', async () => {
    status.index = { ...status.index, status: 'failed', model_id: 'configured-model', error: '预算不足' };
    renderWithProviders(<StickerSettingsPanel />);
    expect(await screen.findByRole('alert')).toHaveTextContent('关键词检索不受影响');
    expect(screen.getByText('关键词检索始终可用')).toBeInTheDocument();
  });

  it('selects a registry entry from the composer picker', async () => {
    const onSelect = vi.fn();
    renderWithProviders(<StickerPicker onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button', { name: '选择表情包' }));
    fireEvent.click(await screen.findByRole('button', { name: '发送表情：坏笑猫' }));
    expect(onSelect).toHaveBeenCalledWith(sticker);
  });

  it('opens single-image analysis for a pending sticker instead of sending it', async () => {
    const pending = { ...sticker, needs_description: true, semantics: { ...sticker.semantics, description: '' } };
    vi.mocked(api.searchStickers).mockResolvedValue({ method: 'keyword', total: 1, candidates: [pending] });
    vi.mocked(api.getSticker).mockResolvedValue(pending);
    vi.mocked(api.getStickerPipeline).mockResolvedValue({ auto_analyze: false, available: true, model_id: 'vision', queued: 0, completed: 0, failed: 0, packs: [], assets: [] });
    const onSelect = vi.fn();
    renderWithProviders(<StickerPicker onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('button', { name: '选择表情包' }));
    fireEvent.click(await screen.findByRole('button', { name: '分析表情：坏笑猫' }));
    expect(onSelect).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toHaveTextContent('仅分析当前图片');
    expect(api.analyzeSticker).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole('button', { name: '同意发送并分析' })).toBeEnabled());
    vi.mocked(api.getSticker).mockResolvedValue(sticker);
    fireEvent.click(screen.getByRole('button', { name: '同意发送并分析' }));
    await waitFor(() => expect(api.analyzeSticker).toHaveBeenCalledWith(sticker.id));
    expect(api.analyzeStickerPack).not.toHaveBeenCalled();
    expect(await screen.findByText('表情分析完成')).toBeInTheDocument();
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '发送这张表情' }));
    expect(onSelect).toHaveBeenCalledWith(sticker);
  });

  it('allows manual semantics when no vision provider is configured', async () => {
    const pending = { ...sticker, needs_description: true, semantics: { ...sticker.semantics, description: '' } };
    vi.mocked(api.searchStickers).mockResolvedValue({ method: 'keyword', total: 1, candidates: [pending] });
    vi.mocked(api.getSticker).mockResolvedValue(pending);
    renderWithProviders(<StickerPicker onSelect={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '选择表情包' }));
    fireEvent.click(await screen.findByRole('button', { name: '分析表情：坏笑猫' }));
    expect(screen.getByRole('button', { name: '同意发送并分析' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '手工填写描述' }));
    fireEvent.change(screen.getByLabelText('手工语义描述'), { target: { value: '小得意，适合熟人调侃' } });
    fireEvent.click(screen.getByRole('button', { name: '保存语义描述' }));
    await waitFor(() => expect(api.updateSticker).toHaveBeenCalledWith(expect.objectContaining({ semantics: expect.objectContaining({ description: '小得意，适合熟人调侃' }) }), expect.anything()));
    expect(api.analyzeSticker).not.toHaveBeenCalled();
  });
});
