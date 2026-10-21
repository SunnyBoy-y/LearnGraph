import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '@/test/render';
import { toast } from 'sonner';
import * as api from '@/api/stickers';
import { AnalyzeStickerPackButton, StickerMarketPanel, StickerPipelinePanel } from './sticker-market';

vi.mock('@/api/stickers', () => ({ getStickerMarket: vi.fn(), installMarketPack: vi.fn(), removeMarketPack: vi.fn(), downloadStickerPack: vi.fn(), marketStickerBlob: vi.fn(), getStickerPipeline: vi.fn(), configureStickerPipeline: vi.fn(), analyzeStickerPack: vi.fn() }));
vi.mock('@/features/auth/auth-context-value', () => ({ useAuth: () => ({ workspaceId: 'workspace', userId: 'owner' }) }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/lib/native-download', () => ({ saveBlobViaNative: vi.fn(async () => true), nativeDownloadSupported: vi.fn(() => false) }));

const pack: api.StickerMarketPack = { id: 'fluent-warm', name: 'Fluent · 温暖陪伴', description: '给你一个抱抱', category: '陪伴', author: 'Microsoft', version: '1.0.0', license: 'MIT · Copyright Microsoft', license_id: 'MIT', source: 'https://github.com/microsoft/fluentui-emoji', style: '3D', count: 6, bytes: 42000, installed_pack_id: null, enabled: false, previews: [{ index: 0, name: '抱抱', description: '安慰' }] };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getStickerMarket).mockResolvedValue({ packs: [pack] });
  vi.mocked(api.marketStickerBlob).mockResolvedValue(new Blob(['image'], { type: 'image/png' }));
  vi.mocked(api.installMarketPack).mockResolvedValue({ pack_id: 'installed', imported: 6 });
  vi.mocked(api.removeMarketPack).mockResolvedValue({ pack_id: 'installed', removed: true });
  vi.mocked(api.downloadStickerPack).mockResolvedValue(new Blob(['zip'], { type: 'application/zip' }));
  vi.mocked(api.getStickerPipeline).mockResolvedValue({ auto_analyze: false, available: true, model_id: 'configured-vision', queued: 0, completed: 0, failed: 0, packs: [] });
  vi.mocked(api.configureStickerPipeline).mockResolvedValue({ auto_analyze: true, available: true, model_id: 'configured-vision', queued: 1, completed: 0, failed: 0, packs: [] });
  vi.mocked(api.analyzeStickerPack).mockResolvedValue({ queued: 1 });
  URL.createObjectURL = vi.fn(() => 'blob:fixture');
  URL.revokeObjectURL = vi.fn();
});

describe('sticker marketplace', () => {
  it('searches the market and installs licensed packs through the real API contract', async () => {
    renderWithProviders(<StickerMarketPanel onOpenLibrary={vi.fn()} />);
    expect(await screen.findByText(pack.name)).toBeInTheDocument();
    expect(screen.getByText(/Microsoft · MIT · 6 张/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('搜索表情市场'), { target: { value: '抱抱' } });
    await waitFor(() => expect(api.getStickerMarket).toHaveBeenCalledWith('抱抱'));
    fireEvent.click(await screen.findByRole('button', { name: '添加到表情库' }));
    await waitFor(() => expect(api.installMarketPack).toHaveBeenCalledWith('fluent-warm', expect.anything()));
    expect(screen.queryByText(/不抓取微信|这是精选资源目录|语义卡 → 关键词索引/)).not.toBeInTheDocument();
  });

  it('previews the pack and downloads an authenticated ZIP including license metadata', async () => {
    renderWithProviders(<StickerMarketPanel onOpenLibrary={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: `预览 ${pack.name}` }));
    expect(screen.getByRole('dialog')).toHaveTextContent(pack.license);
    expect(screen.getByRole('link', { name: /官方来源与许可证/ })).toHaveAttribute('href', pack.source);
    fireEvent.click(screen.getAllByRole('button', { name: `下载 ${pack.name}` }).at(-1)!);
    await waitFor(() => expect(api.downloadStickerPack).toHaveBeenCalledWith(pack.id, true));
  });

  it('opens installed packs in the existing private library instead of duplicating them', async () => {
    vi.mocked(api.getStickerMarket).mockResolvedValue({ packs: [{ ...pack, installed_pack_id: 'already-installed', enabled: true }] });
    const open = vi.fn();
    renderWithProviders(<StickerMarketPanel onOpenLibrary={open} />);
    fireEvent.click(await screen.findByRole('button', { name: '已添加 · 查看' }));
    expect(open).toHaveBeenCalledWith('already-installed');
    expect(api.installMarketPack).not.toHaveBeenCalled();
  });

  it('keeps an empty state when the selected category has no packs', async () => {
    renderWithProviders(<StickerMarketPanel onOpenLibrary={vi.fn()} />);
    await screen.findByText(pack.name);
    fireEvent.click(screen.getByRole('button', { name: '趣味' }));
    expect(screen.getByText(/没有找到匹配的表情包/)).toBeInTheDocument();
  });

  it('removes an installed pack and allows adding it again without navigating away', async () => {
    vi.mocked(api.getStickerMarket).mockResolvedValue({ packs: [{ ...pack, installed_pack_id: 'installed', enabled: true }] });
    const open = vi.fn();
    renderWithProviders(<StickerMarketPanel onOpenLibrary={open} />);
    const remove = await screen.findByRole('button', { name: `移除 ${pack.name}` });
    vi.mocked(api.getStickerMarket).mockResolvedValue({ packs: [pack] });
    fireEvent.click(remove);
    await waitFor(() => expect(api.removeMarketPack).toHaveBeenCalledWith(pack.id, expect.anything()));
    const add = await screen.findByRole('button', { name: '添加到表情库' });
    expect(screen.queryByRole('button', { name: `移除 ${pack.name}` })).not.toBeInTheDocument();
    fireEvent.click(add);
    await waitFor(() => expect(api.installMarketPack).toHaveBeenCalledWith(pack.id, expect.anything()));
    expect(open).not.toHaveBeenCalled();
  });

  it('offers restoration rather than opening a disabled private pack', async () => {
    vi.mocked(api.getStickerMarket).mockResolvedValue({ packs: [{ ...pack, installed_pack_id: 'installed', enabled: false }] });
    renderWithProviders(<StickerMarketPanel onOpenLibrary={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: '添加到表情库' }));
    await waitFor(() => expect(api.installMarketPack).toHaveBeenCalledWith(pack.id, expect.anything()));
  });

  it('keeps removal available in the detail dialog and retains state when removal fails', async () => {
    vi.mocked(api.getStickerMarket).mockResolvedValue({ packs: [{ ...pack, installed_pack_id: 'installed', enabled: true }] });
    vi.mocked(api.removeMarketPack).mockRejectedValue(new Error('移除失败，请重试'));
    renderWithProviders(<StickerMarketPanel onOpenLibrary={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: `预览 ${pack.name}` }));
    fireEvent.click(screen.getAllByRole('button', { name: `移除 ${pack.name}` }).at(-1)!);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('移除失败，请重试'));
    expect(screen.getByRole('dialog')).toHaveTextContent('已添加 · 查看');
    expect(screen.getByRole('dialog')).toHaveTextContent(pack.license);
  });

  it('filters no-approval sources and exposes the correct per-source license', async () => {
    vi.mocked(api.getStickerMarket).mockResolvedValue({ packs: [{ ...pack, source_id: 'fluent' }, { ...pack, id: 'noto-reactions', source_id: 'noto', name: 'Noto · 彩色日常', author: 'Google', license_id: 'Apache-2.0', license: 'Apache-2.0 · Google' }], total_packs: 5, total_assets: 72, sources: ['fluent', 'noto', 'openmoji'] });
    renderWithProviders(<StickerMarketPanel onOpenLibrary={vi.fn()} />);
    await screen.findByText('Noto · 彩色日常');
    expect(screen.queryByText('让回应，多一点表情。')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', { name: '素材来源' }), { target: { value: 'noto' } });
    expect(screen.getByText('Noto · 彩色日常')).toBeInTheDocument();
    expect(screen.queryByText('Fluent · 温暖陪伴')).not.toBeInTheDocument();
    expect(screen.getByText(/Google · Apache-2.0/)).toBeInTheDocument();
  });

  it('requires a separate explicit consent action before enabling remote image analysis', async () => {
    renderWithProviders(<StickerPipelinePanel />);
    const control = await screen.findByRole('switch', { name: '自动分析我的新导入表情' });
    await waitFor(() => expect(control).toBeEnabled());
    fireEvent.click(control);
    expect(api.configureStickerPipeline).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toHaveTextContent('最多三张关键帧');
    fireEvent.click(screen.getByRole('button', { name: '同意并启用自动分析' }));
    await waitFor(() => expect(api.configureStickerPipeline).toHaveBeenCalledWith(true, expect.anything()));
  });

  it('supports a one-time pack analysis without turning on automatic analysis', async () => {
    renderWithProviders(<AnalyzeStickerPackButton id="private-pack" />);
    fireEvent.click(screen.getByRole('button', { name: '补全语义' }));
    expect(api.analyzeStickerPack).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '同意发送并分析' }));
    await waitFor(() => expect(api.analyzeStickerPack).toHaveBeenCalledWith('private-pack'));
    expect(api.configureStickerPipeline).not.toHaveBeenCalled();
  });
});
