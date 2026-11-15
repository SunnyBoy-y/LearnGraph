import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Route, Routes, useNavigate } from 'react-router-dom'
import { apiClient } from '@/api/client'
import { TooltipProvider } from '@/components/ui/tooltip'
import { abortSessionStream, getSessionStream } from '@/lib/session-streams'
import { setSessionComposerPrefs } from '@/lib/session-composer-prefs'
import { deferred } from '@/test/async'
import { renderWithProviders } from '@/test/render'
import type { Message, Session } from '@/types/sessions'
import { ChatCanvasPage } from './chat-pages'
import { readComposerField } from './composer-session-state'

vi.mock('@/features/auth/auth-context-value', () => ({ useAuth: () => ({ workspaceId: 'test-workspace', username: 'tester', effectivePermissions: [] }) }))
beforeEach(() => {
  vi.spyOn(apiClient, 'getConditional').mockImplementation((async (path: string, _etag?: string | null, options?: Parameters<typeof apiClient.getConditional>[2]) => ({
    notModified: false, etag: null, data: await apiClient.get(path, options),
  })) as typeof apiClient.getConditional)
})
const dispose: Array<() => void> = []
afterEach(() => { dispose.splice(0).forEach(cleanup => cleanup()) })
function setup() {
  const prefix = crypto.randomUUID()
  const a = `${prefix}-A`, b = `${prefix}-B`
  const gates = new Map<string, ReturnType<typeof deferred<void>>>()
  const snapshots = new Map<string, Message>()
  const records = [a, b].map(id => ({ id, workspace_id: 'test-workspace', title: id, status: 'active', memory_enabled: true, model_snapshot: {}, context_capsule: {} } as Session))
  for (const id of [a, b]) setSessionComposerPrefs(id, { responseMode: 'fast', thinkingMode: 'off', providerId: 'provider-1', modelId: 'model-1' })
  vi.spyOn(apiClient, 'get').mockImplementation(async path => {
    if (path === '/sessions') return records
    if (path === '/settings') return []
    if (path === '/providers') return [{ id: 'provider-1', provider_type: 'openai_compatible_chat', display_name: '测试模型', enabled: true, remote_capability: true, capabilities: { default_model: 'model-1' } }]
    if (path === '/providers/provider-1/models') return { provider_id: 'provider-1', status: 'ok', models: [{ id: 'model-1', roles: ['llm'], enabled: true, remote: true, streaming: true }] }
    const message = Array.from(snapshots.values()).find(item => path.endsWith(`/messages/${item.id}`))
    if (message) return message
    if (path.endsWith('/messages')) return { items: [], total_count: 0, has_more_before: false }
    return []
  })
  const post = vi.spyOn(apiClient, 'post').mockImplementation(async path => {
    if (path.endsWith('/cancel')) throw new Error('取消接口离线')
    return records[0]
  })
  const send = vi.spyOn(apiClient, 'postSse').mockImplementation((path, _body, options) => (async function* () {
    const id = path.split('/')[2]
    const gate = deferred<void>(); gates.set(id, gate)
    const answerId = `answer-${id}`
    snapshots.set(answerId, { id: answerId, session_id: id, role: 'assistant', status: 'streaming', version: 1, content: '保留的输出', parts: [], provider_trace: {} } as unknown as Message)
    yield { event: 'message.accepted', rawData: '', data: { type: 'message.accepted', message_id: answerId, payload: { user_message_id: `user-${id}` } } }
    await Promise.race([gate.promise, new Promise<void>(resolve => options?.signal?.addEventListener('abort', () => resolve(), { once: true }))])
    if (options?.signal?.aborted) throw new DOMException('aborted', 'AbortError')
    snapshots.set(answerId, { ...snapshots.get(answerId)!, status: 'completed' })
    yield { event: 'message.completed', rawData: '', data: { type: 'message.completed', message_id: answerId } }
  })())
  function Page() {
    const navigate = useNavigate()
    return <><button onClick={() => navigate(`/w/test-workspace/chat/${a}`)}>会话A</button><button onClick={() => navigate(`/w/test-workspace/chat/${b}`)}>会话B</button><button onClick={() => navigate('/w/test-workspace/chat/new?project=safety-project')}>新草稿</button><ChatCanvasPage /></>
  }
  const view = renderWithProviders(<TooltipProvider><Routes><Route path="/w/:workspaceId/chat/:sessionId" element={<Page />} /></Routes></TooltipProvider>, { initialEntries: [`/w/test-workspace/chat/${a}`] })
  dispose.push(() => { for (const id of [a,b]) { gates.get(id)?.resolve(); abortSessionStream(id) } })
  const input = () => screen.getByRole('combobox', { name: '输入消息' }) as HTMLTextAreaElement
  const submit = async (text: string) => { fireEvent.change(input(), { target: { value: text } }); fireEvent.submit(input().closest('form')!) }
  return { ...view, a, b, input, submit, send, post, gates, snapshots }
}

describe('real chat composer safety', () => {
  it('preserves raw Markdown drafts without preview when switching sessions', async () => {
    const view = setup()
    await waitFor(() => expect(view.input()).not.toBeDisabled())
    fireEvent.change(view.input(), { target: { value: '#### A 的格式化草稿' } })
    expect(view.input()).toHaveValue('#### A 的格式化草稿')
    expect(screen.queryByRole('heading', { level: 4, name: 'A 的格式化草稿' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '会话B' }))
    await waitFor(() => expect(document.querySelector('.chat-canvas-page')).toHaveAttribute('data-session-id', view.b))
    fireEvent.change(view.input(), { target: { value: 'B 独立草稿' } })
    fireEvent.click(screen.getByRole('button', { name: '会话A' }))
    await waitFor(() => expect(document.querySelector('.chat-canvas-page')).toHaveAttribute('data-session-id', view.a))
    await waitFor(() => expect(view.input()).toHaveValue('#### A 的格式化草稿'))
    expect(document.querySelector('[contenteditable="true"]')).toBeNull()
    expect(readComposerField(`test-workspace:${view.b}`, 'text', '')).toBe('B 独立草稿')
  })
  it('keeps unsupported attachment feedback beside the composer and isolates it on session switch', async () => {
    const view = setup()
    await waitFor(() => expect(view.input()).not.toBeDisabled())
    const file = new File(['legacy presentation'], 'legacy.ppt', { type: 'application/vnd.ms-powerpoint' })
    fireEvent.change(screen.getByLabelText('Upload files'), { target: { files: [file] } })
    await view.submit('请解释这个附件')
    await waitFor(() => expect(document.querySelector('.composer-feedback')).toHaveTextContent('legacy.ppt'))
    expect(view.input()).toHaveValue('请解释这个附件')
    expect(view.send).not.toHaveBeenCalled()
    expect(view.input()).toHaveAttribute('aria-describedby')
    fireEvent.click(screen.getByRole('button', { name: '会话B' }))
    await waitFor(() => expect(document.querySelector('.chat-canvas-page')).toHaveAttribute('data-session-id', view.b))
    expect(document.querySelector('.composer-feedback')).toBeNull()
  })
  it('does not dispatch A queued text into B and keeps it when returning to A', async () => {
    const view = setup()
    await waitFor(() => expect(view.input()).not.toBeDisabled())
    await view.submit('A 正在执行')
    await waitFor(() => expect(getSessionStream(view.a)?.messageId).toBe(`answer-${view.a}`))
    await view.submit('只属于 A 的排队消息')
    await screen.findByRole('region', { name: '待发送队列' })
    fireEvent.click(screen.getByRole('button', { name: '会话B' }))
    await waitFor(() => expect(document.querySelector('.chat-canvas-page')).toHaveAttribute('data-session-id', view.b))
    expect(screen.queryByRole('region', { name: '待发送队列' })).toBeNull()
    expect(view.send).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: '会话A' }))
    expect(await screen.findByRole('region', { name: '待发送队列' })).toHaveTextContent('只属于 A 的排队消息')
    expect(view.send).toHaveBeenCalledTimes(1)
  })
  it('preserves a fresh draft and direct File on an immediate A/B switch', async () => {
    const view = setup()
    await waitFor(() => expect(view.input()).not.toBeDisabled())
    const file = new File(['draft bytes'], 'draft.txt', { type: 'text/plain' })
    fireEvent.change(view.input(), { target: { value: '来不及等待去抖的草稿' } })
    fireEvent.change(screen.getByLabelText('Upload files'), { target: { files: [file] } })
    fireEvent.click(screen.getByRole('button', { name: '会话B' }))
    fireEvent.change(view.input(), { target: { value: 'B 独立草稿' } })
    fireEvent.click(screen.getByRole('button', { name: '会话A' }))
    await waitFor(() => expect(view.input()).toHaveValue('来不及等待去抖的草稿'))
    expect(readComposerField<Array<{ localFile: File }>>(`test-workspace:${view.a}`, 'files', [])[0].localFile).toBe(file)
    expect(screen.getByRole('button', { name: '预览附件 draft.txt' })).toBeInTheDocument()
  })
  it('edits a queued item in place without replacing another composer draft or dropping its File', async () => {
    const view = setup()
    await waitFor(() => expect(view.input()).not.toBeDisabled())
    await view.submit('执行中')
    await waitFor(() => expect(getSessionStream(view.a)?.messageId).toBeTruthy())
    const file = new File(['queue bytes'], 'queued.txt', { type: 'text/plain' })
    fireEvent.change(screen.getByLabelText('Upload files'), { target: { files: [file] } })
    await view.submit('旧队列消息')
    const queue = await screen.findByRole('region', { name: '待发送队列' })
    fireEvent.change(view.input(), { target: { value: '当前正在写的新草稿' } })
    fireEvent.click(within(queue).getByRole('button', { name: '重新编辑' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByRole('textbox', { name: '编辑待发送消息正文' }), { target: { value: '修改后的队列消息' } })
    fireEvent.click(within(dialog).getByRole('button', { name: '保存' }))
    expect(view.input()).toHaveValue('当前正在写的新草稿')
    expect(queue).toHaveTextContent('修改后的队列消息')
    expect(readComposerField<Array<{ files: Array<{ localFile: File }> }>>(`test-workspace:${view.a}`, 'queue', [])[0].files[0].localFile).toBe(file)
  })
  it('keeps the stream alive and blocks sending when cancellation fails', async () => {
    const view = setup()
    await waitFor(() => expect(view.input()).not.toBeDisabled())
    await view.submit('执行中')
    await waitFor(() => expect(getSessionStream(view.a)?.messageId).toBeTruthy())
    fireEvent.click(await screen.findByRole('button', { name: '中断回答' }))
    await screen.findByText(/停止未确认，任务可能仍在运行/)
    expect(getSessionStream(view.a)?.controller.signal.aborted).toBe(false)
    await view.submit('未确认停止前不得发送')
    expect(view.send).toHaveBeenCalledTimes(1)
    expect(view.input()).toHaveValue('未确认停止前不得发送')
  })
  it('runs draft toolbar callbacks against the newly selected session', async () => {
    const view = setup()
    await waitFor(() => expect(view.input()).not.toBeDisabled())
    fireEvent.paste(view.input(), { clipboardData: { getData: () => 'A'.repeat(10001) } })
    fireEvent.click(screen.getByRole('button', { name: '会话B' }))
    fireEvent.paste(view.input(), { clipboardData: { getData: () => 'B'.repeat(10001) } })
    fireEvent.click(screen.getByRole('button', { name: '移除', exact: true }))
    expect(readComposerField(`test-workspace:${view.b}`, 'longPaste', null)).toBeNull()
    expect(readComposerField(`test-workspace:${view.a}`, 'longPaste', null)).toBe('A'.repeat(10001))
  })

  it('keeps a new project draft when navigating away and back', async () => {
    const view = setup()
    await waitFor(() => expect(view.input()).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: '新草稿' }))
    fireEvent.change(view.input(), { target: { value: '新会话也必须保留' } })
    fireEvent.click(screen.getByRole('button', { name: '会话A' }))
    fireEvent.click(screen.getByRole('button', { name: '新草稿' }))
    await waitFor(() => expect(view.input()).toHaveValue('新会话也必须保留'))
  })
})
