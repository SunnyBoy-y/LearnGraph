import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Route, Routes, useNavigate } from 'react-router-dom';
import { apiClient } from '@/api/client';
import type { Sticker } from '@/api/stickers';
import { TooltipProvider } from '@/components/ui/tooltip';
import { setSessionComposerPrefs } from '@/lib/session-composer-prefs';
import { getSessionStream } from '@/lib/session-streams';
import { deferred } from '@/test/async';
import { renderWithProviders } from '@/test/render';
import type { Session } from '@/types/sessions';
import { ChatCanvasPage } from './chat-pages';

vi.mock('@/features/auth/auth-context-value', () => ({
  useAuth: () => ({ workspaceId: 'test-workspace', userId: 'owner', effectivePermissions: [] }),
}));
vi.mock('@/components/chat/sticker-image', () => ({
  StickerImage: ({ alt }: { alt: string }) => <img alt={alt} />,
  StickerPart: () => <span>表情消息</span>,
}));

const stickers: Sticker[] = Array.from({ length: 5 }, (_, index) => ({
  id: `sticker-${index}`, pack_id: 'builtin', name: `表情${index + 1}`,
  content_hash: `hash-${index}`, mime_type: 'image/png', needs_description: false,
  semantics: {
    description: '日常回应', ocr: '', tags: [], emotion: [], intent: [],
    use_when: [], avoid_when: [], standalone: true,
  },
}));

function setup() {
  const records = ['active', 'other'].map(id => ({
    id, workspace_id: 'test-workspace', title: id, status: 'active',
    memory_enabled: true, model_snapshot: {}, context_capsule: {},
  } as Session));
  setSessionComposerPrefs('active', { responseMode: 'fast', thinkingMode: 'off' });
  vi.spyOn(apiClient, 'get').mockImplementation(async path => {
    if (path === '/sessions') return records;
    if (path === '/settings') return [];
    if (path === '/providers') return [{
      id: 'provider-1', provider_type: 'openai_compatible_chat', display_name: '测试模型',
      enabled: true, remote_capability: true, capabilities: { default_model: 'model-1' },
    }];
    if (path === '/providers/provider-1/models') return {
      provider_id: 'provider-1', status: 'ok',
      models: [{ id: 'model-1', roles: ['llm'], enabled: true, remote: true, streaming: true }],
    };
    if (path === '/stickers/search') return { method: 'keyword', total: stickers.length, candidates: stickers };
    if (path === '/stickers/pipeline') return { assets: [], packs: [], auto_analyze: false, available: false };
    if (path.endsWith('/messages')) return { items: [], total_count: 0, has_more_before: false };
    return [];
  });
  vi.spyOn(apiClient, 'postSse').mockImplementation(() => (async function* () {
    yield { event: 'message.completed', rawData: '', data: { type: 'message.completed', message_id: 'answer' } };
  })());
  function Page() {
    const navigate = useNavigate();
    return <>
      <button onClick={() => navigate('/w/test-workspace/chat/other')}>切换会话</button>
      <ChatCanvasPage />
    </>;
  }
  return renderWithProviders(<TooltipProvider><Routes>
    <Route path="/w/:workspaceId/chat/:sessionId" element={<Page />} />
  </Routes></TooltipProvider>, { initialEntries: ['/w/test-workspace/chat/active'] });
}

async function addSticker(index = 0) {
  fireEvent.click(await screen.findByRole('button', { name: '选择表情包' }));
  fireEvent.click(await screen.findByRole('button', { name: `添加表情：表情${index + 1}` }));
}

async function ready() {
  await waitFor(() => expect(apiClient.get).toHaveBeenCalledWith('/providers/provider-1/models'));
}

describe('sticker message drafts', () => {
  it('adds and removes a sticker without sending or changing the text', async () => {
    setup();
    await ready();
    fireEvent.change(screen.getByRole('combobox', { name: '输入消息' }), { target: { value: '今天完成任务了' } });
    await addSticker();
    const draft = screen.getByRole('list', { name: '待发送表情' });
    expect(within(draft).getByRole('img', { name: '表情1' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '输入消息' })).toHaveValue('今天完成任务了');
    expect(apiClient.postSse).not.toHaveBeenCalled();
    fireEvent.click(within(draft).getByRole('button', { name: '移除表情：表情1' }));
    expect(screen.queryByRole('list', { name: '待发送表情' })).not.toBeInTheDocument();
    expect(apiClient.postSse).not.toHaveBeenCalled();
  });

  it('sends text and multiple stickers in the same request only on submit', async () => {
    const view = setup();
    await ready();
    fireEvent.change(screen.getByRole('combobox', { name: '输入消息' }), { target: { value: '一起庆祝' } });
    await addSticker(0);
    await addSticker(1);
    expect(apiClient.postSse).not.toHaveBeenCalled();
    fireEvent.submit(view.container.querySelector('form')!);
    await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(1));
    expect(vi.mocked(apiClient.postSse).mock.calls[0][1]).toMatchObject({
      content: '一起庆祝', sticker_ids: ['sticker-0', 'sticker-1'],
    });
    expect(screen.queryByRole('list', { name: '待发送表情' })).not.toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '输入消息' })).toHaveValue('');
  });

  it('supports sticker-only messages through the normal send button and deduplicates selections', async () => {
    setup();
    await ready();
    await addSticker(0);
    await addSticker(0);
    expect(within(screen.getByRole('list', { name: '待发送表情' })).getAllByRole('listitem')).toHaveLength(1);
    expect(apiClient.postSse).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
    await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(1));
    expect(vi.mocked(apiClient.postSse).mock.calls[0][1]).toMatchObject({
      content: '[表情]', sticker_ids: ['sticker-0'],
    });
  });

  it('limits drafts to four stickers and removes the last one with Backspace', async () => {
    setup();
    await ready();
    for (let index = 0; index < 5; index++) await addSticker(index);
    expect(within(screen.getByRole('list', { name: '待发送表情' })).getAllByRole('listitem')).toHaveLength(4);
    fireEvent.keyDown(screen.getByRole('combobox', { name: '输入消息' }), { key: 'Backspace' });
    expect(within(screen.getByRole('list', { name: '待发送表情' })).getAllByRole('listitem')).toHaveLength(3);
    expect(apiClient.postSse).not.toHaveBeenCalled();
  });

  it('clears unsent stickers when switching conversations', async () => {
    setup();
    await ready();
    await addSticker();
    fireEvent.click(screen.getByRole('button', { name: '切换会话' }));
    await waitFor(() => expect(screen.queryByRole('list', { name: '待发送表情' })).not.toBeInTheDocument());
    expect(apiClient.postSse).not.toHaveBeenCalled();
  });

  it('keeps stickers in the queued snapshot and restores them for editing', async () => {
    const view = setup();
    await ready();
    const release = deferred<void>();
    vi.mocked(apiClient.postSse).mockImplementation(() => (async function* () {
      await release.promise;
      yield { event: 'message.completed', rawData: '', data: { type: 'message.completed', message_id: 'answer' } };
    })());
    fireEvent.change(screen.getByRole('combobox', { name: '输入消息' }), { target: { value: '第一条' } });
    fireEvent.submit(view.container.querySelector('form')!);
    await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(1));
    expect(getSessionStream('active')).toBeDefined();
    await addSticker();
    fireEvent.submit(view.container.querySelector('form')!);
    const queue = await screen.findByRole('region', { name: '待发送队列' });
    expect(within(queue).getByLabelText('1 张表情')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: '待发送表情' })).not.toBeInTheDocument();
    expect(apiClient.postSse).toHaveBeenCalledTimes(1);
    fireEvent.click(within(queue).getByRole('button', { name: '重新编辑' }));
    expect(screen.getByRole('list', { name: '待发送表情' })).toBeInTheDocument();
    await act(async () => { release.resolve(); });
    await waitFor(() => expect(getSessionStream('active')).toBeUndefined());
    expect(apiClient.postSse).toHaveBeenCalledTimes(1);
  });

  it('does not clear a newer draft when an explicitly queued sticker is sent', async () => {
    const view = setup();
    await ready();
    const release = deferred<void>();
    let invocation = 0;
    vi.mocked(apiClient.postSse).mockImplementation(() => (async function* () {
      if (invocation++ === 0) await release.promise;
      yield { event: 'message.completed', rawData: '', data: { type: 'message.completed', message_id: 'answer' } };
    })());
    fireEvent.change(screen.getByRole('combobox', { name: '输入消息' }), { target: { value: '第一条' } });
    fireEvent.submit(view.container.querySelector('form')!);
    await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(1));
    await addSticker(0);
    fireEvent.submit(view.container.querySelector('form')!);
    await screen.findByRole('region', { name: '待发送队列' });
    fireEvent.change(screen.getByRole('combobox', { name: '输入消息' }), { target: { value: '继续编辑，暂不发送' } });
    await addSticker(1);
    await act(async () => { release.resolve(); });
    await waitFor(() => expect(apiClient.postSse).toHaveBeenCalledTimes(2));
    expect(vi.mocked(apiClient.postSse).mock.calls[1][1]).toMatchObject({
      content: '[表情]', sticker_ids: ['sticker-0'],
    });
    expect(screen.getByRole('combobox', { name: '输入消息' })).toHaveValue('继续编辑，暂不发送');
    expect(within(screen.getByRole('list', { name: '待发送表情' })).getByRole('img', { name: '表情2' })).toBeInTheDocument();
    await waitFor(() => expect(getSessionStream('active')).toBeUndefined());
  });
});
