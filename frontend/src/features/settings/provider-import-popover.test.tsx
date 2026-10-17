import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '@/test/render';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { commitConfigPreview, getConfigSources, previewConfigSources } from '@/api/provider-automation';
import { discoverProviderModels, importCcSwitchProviders } from '@/api/providers';
import { ImportProviderPopover } from './provider-import-popover';
import { parseConnectionFile } from './provider-import-file';

vi.mock('@/api/provider-automation', () => ({ getConfigSources: vi.fn(), previewConfigSources: vi.fn(), commitConfigPreview: vi.fn() }));
vi.mock('@/api/providers', () => ({ discoverProviderModels: vi.fn(), importCcSwitchProviders: vi.fn() }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const config = { providers: [
  { name: 'Alpha', settingsConfig: { env: { OPENAI_BASE_URL: 'https://alpha.example/v1', OPENAI_API_KEY: 'test-secret-alpha' } } },
  { name: 'Broken', settingsConfig: { env: { OPENAI_API_KEY: 'test-secret-broken' } } },
  { name: 'Beta', settingsConfig: { env: { ANTHROPIC_BASE_URL: 'https://beta.example', ANTHROPIC_AUTH_TOKEN: 'test-secret-beta' } } },
] };

beforeEach(() => {
  vi.mocked(getConfigSources).mockResolvedValue({ sources: [{ id: 'cc', label: 'CC Switch', kind: 'ccswitch', status: 'available', message: '' }] });
  vi.mocked(previewConfigSources).mockResolvedValue({ preview_id: 'preview-1', warnings: [], candidates: [
    { id: 'one', name: 'Alpha', source_id: 'cc', source_label: 'CC Switch', provider_type: 'openai_compatible_chat', base_url: 'https://alpha.example/v1', has_key: true, model_ids: [], duplicate: false },
    { id: 'two', name: 'Existing', source_id: 'cc', source_label: 'CC Switch', provider_type: 'openai_compatible_chat', base_url: 'https://existing.example', has_key: true, model_ids: [], duplicate: true },
  ] });
  vi.mocked(commitConfigPreview).mockResolvedValue({ created: [{ name: 'Alpha', provider_id: 'saved', enabled: false, models: [] }], skipped: [], failed: [] });
  vi.mocked(importCcSwitchProviders).mockResolvedValue({ created: [{ name: 'Alpha', provider_id: 'saved', enabled: false }], skipped: [] });
  vi.mocked(discoverProviderModels).mockRejectedValue(new Error('offline'));
});

describe('connection import popover', () => {
  it('uses one popover inside the settings dialog and imports only the selection', async () => {
    const user = userEvent.setup();
    renderWithProviders(<Dialog open><DialogContent aria-describedby={undefined}><DialogTitle>设置</DialogTitle><ImportProviderPopover /></DialogContent></Dialog>);
    await user.click(screen.getByRole('button', { name: '导入配置' }));
    await user.click(await screen.findByRole('button', { name: /CC Switch.*发现 2/ }));
    expect(screen.getAllByRole('dialog')).toHaveLength(2); // Settings + the same Radix popover.
    expect(screen.getByRole('checkbox', { name: '选择 Existing' })).toBeDisabled();
    await user.type(screen.getByRole('textbox', { name: '搜索连接' }), 'alpha');
    expect(screen.queryByText('Existing')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '全选', exact: true }));
    await user.click(screen.getByRole('button', { name: '导入 1 项' }));
    await waitFor(() => expect(commitConfigPreview).toHaveBeenCalledWith('preview-1', ['one']));
    await waitFor(() => expect(screen.queryByText('从 CC Switch 导入')).not.toBeInTheDocument());
    expect(screen.getByRole('dialog', { name: '设置' })).toBeInTheDocument();
    await waitFor(() => expect(discoverProviderModels).toHaveBeenCalledWith('saved'));
  });

  it('returns within the same surface and Escape closes only the popover', async () => {
    const user = userEvent.setup();
    const closeSettings = vi.fn();
    renderWithProviders(<Dialog open onOpenChange={closeSettings}><DialogContent aria-describedby={undefined}><DialogTitle>设置</DialogTitle><ImportProviderPopover /></DialogContent></Dialog>);
    const trigger = screen.getByRole('button', { name: '导入配置' });
    await user.click(trigger);
    await user.click(await screen.findByRole('button', { name: /CC Switch.*发现 2/ }));
    await user.click(screen.getByRole('button', { name: '返回配置来源' }));
    expect(screen.getByText('导入连接')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByText('导入连接')).not.toBeInTheDocument();
    expect(closeSettings).not.toHaveBeenCalled();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it('imports a file selection without displaying or submitting unselected secrets', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ImportProviderPopover />);
    await user.click(screen.getByRole('button', { name: '导入配置' }));
    const file = new File([JSON.stringify(config)], 'connections.json', { type: 'application/json' });
    Object.defineProperty(file, 'text', { value: async () => JSON.stringify(config) });
    fireEvent.change(screen.getByLabelText('选择配置文件'), { target: { files: [file] } });
    await screen.findByText('从配置文件导入', { selector: 'h3' });
    expect(screen.getByRole('checkbox', { name: '选择 Broken' })).toBeDisabled();
    expect(document.body).not.toHaveTextContent('test-secret');
    await user.click(screen.getByRole('checkbox', { name: '选择 Alpha' }));
    await user.click(screen.getByRole('button', { name: '导入 1 项' }));
    await waitFor(() => expect(importCcSwitchProviders).toHaveBeenCalledOnce());
    const submitted = JSON.parse(vi.mocked(importCcSwitchProviders).mock.calls[0][0].config_json);
    expect(submitted.providers).toHaveLength(1);
    expect(submitted.providers[0].settingsConfig.env.OPENAI_API_KEY).toBe('test-secret-alpha');
    expect(JSON.stringify(submitted)).not.toContain('test-secret-beta');
    expect(discoverProviderModels).not.toHaveBeenCalled(); // File endpoint already discovers.
  });

  it('keeps the menu and selection on save failure', async () => {
    vi.mocked(commitConfigPreview).mockRejectedValue(new Error('save failed'));
    const user = userEvent.setup();
    renderWithProviders(<ImportProviderPopover />);
    await user.click(screen.getByRole('button', { name: '导入配置' }));
    await user.click(await screen.findByRole('button', { name: /CC Switch.*发现 2/ }));
    await user.click(screen.getByRole('checkbox', { name: '选择 Alpha' }));
    await user.click(screen.getByRole('button', { name: '导入 1 项' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('已保存的连接会保留');
    expect(screen.getByRole('checkbox', { name: '选择 Alpha' })).toBeChecked();
  });

  it('offers file import when the server has no authorized source', async () => {
    vi.mocked(getConfigSources).mockResolvedValue({ sources: [] });
    const user = userEvent.setup();
    renderWithProviders(<ImportProviderPopover />);
    await user.click(screen.getByRole('button', { name: '导入配置' }));
    await waitFor(() => expect(screen.getByRole('button', { name: /CC Switch/ })).toBeDisabled());
    expect(screen.getByRole('button', { name: '从配置文件导入' })).toBeEnabled();
    expect(previewConfigSources).not.toHaveBeenCalled();
    expect(within(screen.getByRole('dialog')).queryByRole('tab')).not.toBeInTheDocument();
  });
});

describe('connection file formats', () => {
  it('shows invalid and duplicate rows, preserving different credentials at the same URL', () => {
    const same = config.providers[0];
    const items = parseConnectionFile(JSON.stringify({ providers: [same, same, { ...same, settingsConfig: { env: { ...same.settingsConfig.env, OPENAI_API_KEY: 'other' } } }, config.providers[1]] }));
    expect(items.map(item => item.reason)).toEqual([undefined, '文件中存在相同连接', undefined, '缺少 Base URL']);
  });
  it('reads Cherry Studio provider JSON and normalizes Anthropic separately', () => {
    const items = parseConnectionFile(JSON.stringify({ 'persist:cherry-studio': JSON.stringify({ llm: JSON.stringify({ providers: [{ name: 'Cherry', type: 'anthropic', apiHost: 'https://cherry.example/', apiKey: 'cherry-secret' }] }) }) }));
    expect(items[0]).toMatchObject({ name: 'Cherry', base_url: 'https://cherry.example', reason: undefined });
    expect(items[0].config).toMatchObject({ app_type: 'claude', settingsConfig: { env: { ANTHROPIC_AUTH_TOKEN: 'cherry-secret' } } });
  });
  it('does not expose credentials embedded in invalid URLs and rejects invalid files', () => {
    const items = parseConnectionFile(JSON.stringify({ providers: [{ apiHost: 'https://user:secret@host.example/?key=secret', apiKey: 'secret' }] }));
    expect(items[0]).toMatchObject({ base_url: '', reason: '地址格式不支持' });
    expect(() => parseConnectionFile('bad JSON')).toThrow('有效的 JSON');
    expect(() => parseConnectionFile('{}')).toThrow('未找到连接');
  });
});
