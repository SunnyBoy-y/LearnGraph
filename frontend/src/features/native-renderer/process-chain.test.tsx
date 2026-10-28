import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MessagePart } from '@/types/sessions'
import type { NativeRendererBridge, NativeThinkingChainPayload } from './protocol'
import { installRendererDrawer } from './drawer'
import { NativeProcessChain } from './process-chain'

const reasoning = (content = '先明确问题\n最新的思路', status: MessagePart['status'] = 'streaming'): MessagePart => ({
  id: 'reason', type: 'reasoning_content', status, content,
})
const state: NativeThinkingChainPayload = {
  messageStatus: 'streaming', startedAt: '2026-10-18T12:00:00Z', finalAnswerStarted: false, defaultOpen: true,
}
const renderPart = (part: MessagePart) => <div key={part.id} data-testid={part.id}>{part.content}</div>

describe('native process uses the same ThinkingChain and ReasoningSummaryRow as Web', () => {
  it('keeps a collapsed live fold manually expandable without reopening it on deltas', () => {
    const initial = reasoning();
    const collapsed = {...state, defaultOpen: false};
    const view = render(<NativeProcessChain parts={[initial]} siblingParts={[initial]} state={collapsed} renderPart={renderPart} />);
    expect(screen.getByRole('button', {name: '展开思维链条'})).toHaveAttribute('data-state', 'closed');
    const delta = reasoning('先明确问题\n继续分析');
    view.rerender(<NativeProcessChain parts={[delta]} siblingParts={[delta]} state={collapsed} renderPart={renderPart} />);
    expect(screen.getByRole('button', {name: '展开思维链条'})).toHaveAttribute('data-state', 'closed');
    fireEvent.click(screen.getByRole('button', {name: '展开思维链条'}));
    expect(screen.getByRole('button', {name: '收起思维链条'})).toHaveAttribute('data-state', 'open');
    fireEvent.click(screen.getByRole('button', {name: '收起思维链条'}));
    expect(screen.getByRole('button', {name: '展开思维链条'})).toHaveAttribute('data-state', 'closed');
  });
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-18T12:00:20Z')) })
  afterEach(() => { vi.clearAllTimers(); vi.useRealTimers() })

  it('loads history collapsed and uses persisted thinking time plus settled tool count', () => {
    const parts: MessagePart[] = [reasoning('完整历史过程', 'completed'),
      { id: 'tool-ok', type: 'tool_call', status: 'completed' },
      { id: 'tool-fail', type: 'tool_call', status: 'failed' },
      { id: 'tool-active', type: 'tool_call', status: 'streaming' }]
    render(<NativeProcessChain parts={parts} siblingParts={parts} state={{ ...state, messageStatus: 'completed' }}
      providerTrace={{ thinking_duration_ms: 65_900, generation_duration_ms: 99_000 }} renderPart={renderPart} />)
    expect(screen.getByRole('button', { name: '展开思维链条' })).toHaveAttribute('data-state', 'closed')
    expect(screen.getByText('思考了 1m 5s')).toBeInTheDocument()
    expect(screen.getByText('2 次工具调用')).toBeInTheDocument()
    expect(screen.queryByText('完整历史过程')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '展开思维链条' }))
    const row = document.querySelector('.reasoning-summary-row')!
    expect(row).not.toHaveAttribute('data-expanded')
    fireEvent.click(row.querySelector('button')!)
    expect(row.querySelector('.reasoning-summary-row__body')).toHaveTextContent('完整历史过程')
  })

  it('keeps expanded reasoning through real deltas and collapses only once at the answer boundary', () => {
    const initial = reasoning()
    const view = render(<NativeProcessChain parts={[initial]} siblingParts={[initial]} state={state} renderPart={renderPart} />)
    expect(screen.getByText('正在思考 20s')).toBeInTheDocument()
    expect(document.querySelector('.reasoning-summary-row__summary')).toHaveTextContent('最新的思路')
    const row = document.querySelector('.reasoning-summary-row')!
    fireEvent.click(row.querySelector('button')!)
    const delta = reasoning('先明确问题\n最新的思路\n继续分析，保留完整内容')
    view.rerender(<NativeProcessChain parts={[delta]} siblingParts={[delta]} state={state} renderPart={renderPart} />)
    expect(document.querySelector('.reasoning-summary-row')).toBe(row)
    expect(row).toHaveAttribute('data-expanded', 'true')
    expect(row.querySelector('.reasoning-summary-row__body')).toHaveTextContent('继续分析，保留完整内容')

    const answered = { ...state, finalAnswerStarted: true, thinkingDurationMs: 12_700 }
    view.rerender(<NativeProcessChain parts={[delta]} siblingParts={[delta]} state={answered} renderPart={renderPart} />)
    expect(screen.getByRole('button', { name: '展开思维链条' })).toBeInTheDocument()
    expect(screen.getByText('思考了 12s')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '展开思维链条' }))
    const next = reasoning(delta.content + '\n最后一段')
    view.rerender(<NativeProcessChain parts={[next]} siblingParts={[next]} state={{ ...answered }} renderPart={renderPart} />)
    expect(screen.getByRole('button', { name: '收起思维链条' })).toBeInTheDocument()
    expect(screen.getByText('思考了 12s')).toBeInTheDocument()
  })

  it('respects the collapsed live preference and retains all Web activity chips', () => {
    const parts: MessagePart[] = [reasoning(),
      { id: 'search', type: 'tool_call', status: 'streaming', data: { tool_name: 'search_web', input: { query: '真实查询' } } },
      { id: 'other', type: 'tool_call', status: 'pending', data: { title: '检查资料' } }]
    render(<NativeProcessChain parts={parts} siblingParts={parts} state={{ ...state, defaultOpen: false }} renderPart={renderPart} />)
    expect(screen.getByRole('button', { name: '展开思维链条' })).toBeInTheDocument()
    const chips = Array.from(document.querySelectorAll('.thinking-chain__chip')).map(node => node.textContent)
    expect(chips).toEqual(['正在思考', '检查资料', 'search 真实查询'])
    expect(screen.queryByText('正在思考 20s')).not.toBeInTheDocument()
  })

  it('uses the original 400ms terminal fold and server timestamp fallback', () => {
    const part = reasoning()
    const view = render(<NativeProcessChain parts={[part]} siblingParts={[part]} state={state} renderPart={renderPart} />)
    view.rerender(<NativeProcessChain parts={[part]} siblingParts={[part]} state={{ ...state, messageStatus: 'completed' }}
      providerTrace={{ generation_started_at: '2026-10-18T12:00:00Z', generation_completed_at: '2026-10-18T12:00:09.900Z' }} renderPart={renderPart} />)
    expect(screen.getByText('思考了 9s')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '收起思维链条' })).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(399))
    expect(screen.getByRole('button', { name: '收起思维链条' })).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(1))
    expect(screen.getByRole('button', { name: '展开思维链条' })).toBeInTheDocument()
  })

  it.each([['failed', '处理失败'], ['cancelled', '已取消']])('keeps the original %s status label', (messageStatus, label) => {
    const part = reasoning('已保存的过程', 'completed')
    render(<NativeProcessChain parts={[part]} siblingParts={[part]} state={{ ...state, messageStatus }}
      providerTrace={{ thinking_duration_ms: 2_999 }} renderPart={renderPart} />)
    expect(screen.getByText(`${label} · 用时 2s`)).toBeInTheDocument()
  })

  it('lets process controls own horizontal touches instead of forwarding a drawer gesture', () => {
    const part = reasoning()
    render(<NativeProcessChain parts={[part]} siblingParts={[part]} state={state} renderPart={renderPart} />)
    const drawer = vi.fn()
    const remove = installRendererDrawer({ drawer } as unknown as NativeRendererBridge, () => false)
    const target = document.querySelector('.reasoning-summary-row__trigger')!
    const touch = (type: string, x: number, y: number) => {
      const event = new Event(type, { bubbles: true, cancelable: true })
      Object.defineProperty(event, 'touches', { value: [{ identifier: 1, clientX: x, clientY: y }] })
      target.dispatchEvent(event)
    }
    try {
      touch('touchstart', 100, 100)
      touch('touchmove', 160, 101)
      expect(drawer).not.toHaveBeenCalled()
    } finally { remove() }
  })
})
