import { describe, expect, it, vi } from 'vitest'
import { dispatchRendererAction, type ActionEnvironment } from './actions'
import type { TrustedComponentAction } from '@/components/chat/trusted-component-renderer'

function environment(): ActionEnvironment {
  return {
    sessionId: () => 's1', send: vi.fn(),
    submitUIAction: vi.fn().mockResolvedValue({ prompt: '解释服务端校验后的组件' }),
    reviewGraph: vi.fn().mockResolvedValue({ id: 'p1', status: 'confirmed', graph_id: 'g1', confirmed_revision: 2, rejection_reason: '' }),
    rejectReason: vi.fn().mockResolvedValue('调整粒度'), graphResolved: vi.fn(), refresh: vi.fn(),
  }
}
function action(componentType: string, event: string, payload: Record<string, unknown>): TrustedComponentAction {
  return { componentId: 'part1', componentType, event, payload }
}

describe('native renderer actions use the existing conversation contract', () => {
  it('sends client-side userMessage and ui_action in zero RTT without calling submitUIAction', async () => {
    const env = environment()
    const userMessage = '请解释图表「知识图谱」中的节点「概念A」及其关联。'
    await dispatchRendererAction(action('stream_ui', 'graph.node.explain', { revision: 3, node_id: 'n1', userMessage }), env)
    const uiAction = { part_id: 'part1', revision: 3, action: 'graph.node.explain', node_id: 'n1' }
    expect(env.submitUIAction).not.toHaveBeenCalled()
    expect(env.send).toHaveBeenCalledWith({ content: userMessage, graph_action: 'none', generation_mode: 'text', ui_action: uiAction })
  })

  it('forwards document.block.explain with block_id, view_state and fallback userMessage', async () => {
    const env = environment()
    await dispatchRendererAction(action('stream_ui', 'document.block.explain', { revision: 7, block_id: 'trend', view_state: { mode: 'bar' } }), env)
    expect(env.submitUIAction).not.toHaveBeenCalled()
    expect(env.send).toHaveBeenCalledWith({
      content: '请解释当前图表，分析其中的主要信息。',
      graph_action: 'none',
      generation_mode: 'text',
      ui_action: { part_id: 'part1', revision: 7, action: 'document.block.explain', block_id: 'trend', view_state: { mode: 'bar' } },
    })
  })

  it('propagates chat admission rejection from send', async () => {
    const rejected = environment()
    vi.mocked(rejected.send).mockRejectedValue(new Error('stale revision'))
    await expect(dispatchRendererAction(action('stream_ui', 'explain', { revision: 1 }), rejected)).rejects.toThrow('stale revision')
  })

  it('reviews graph proposals through the original APIs and honors reject cancellation', async () => {
    const env = environment()
    await dispatchRendererAction(action('graph_update_proposal', 'confirm', { proposal_id: 'p1' }), env)
    expect(env.reviewGraph).toHaveBeenCalledWith('s1', 'p1', 'confirm', '')
    expect(env.graphResolved).toHaveBeenCalled()
    expect(env.refresh).toHaveBeenCalled()
    expect(env.send).not.toHaveBeenCalled()
    const cancel = environment()
    vi.mocked(cancel.rejectReason).mockResolvedValue(null)
    await dispatchRendererAction(action('graph_update_proposal', 'reject', { proposal_id: 'p1' }), cancel)
    expect(cancel.reviewGraph).not.toHaveBeenCalled()
    const reject = environment()
    await dispatchRendererAction(action('graph_update_proposal', 'reject', { proposal_id: 'p1' }), reject)
    expect(reject.reviewGraph).toHaveBeenCalledWith('s1', 'p1', 'reject', '调整粒度')
  })

  it('preserves code analysis mode and aggregated question summaries', async () => {
    const env = environment()
    await dispatchRendererAction(action('code_trainer', 'submit', { prompt: '检查这段代码' }), env)
    expect(env.send).toHaveBeenLastCalledWith({ content: '检查这段代码', graph_action: 'none', generation_mode: 'text', analysis_only: true })
    await dispatchRendererAction(action('question_set', 'submit', { summaryText: '完整的逐题作答与评分' }), env)
    expect(env.send).toHaveBeenLastCalledWith({ content: '完整的逐题作答与评分', graph_action: 'none', generation_mode: 'text' })
    await dispatchRendererAction(action('question_batch', 'submit', { answers: [{ prompt: '每天多久？', labels: '30 分钟' }, { value: '' }] }), env)
    expect(env.send).toHaveBeenLastCalledWith({ content: '【聚合问答】1. 每天多久？ → 30 分钟；2. 问题 2 → （跳过）', graph_action: 'none', generation_mode: 'text' })
  })

  it('keeps goal draft fields and rejects invalid revisions without API calls', async () => {
    const env = environment()
    await dispatchRendererAction(action('goal_draft_editor', 'submit', { draft: { title: '操作系统', time_limit: '两周' } }), env)
    expect(env.send).toHaveBeenLastCalledWith(expect.objectContaining({ content: '【目标草稿更新】请按我修改后的字段确认目标草稿：{"title":"操作系统","time_limit":"两周"}' }))
    await expect(dispatchRendererAction(action('stream_ui', 'explain', { revision: -1 }), env)).rejects.toThrow('版本无效')
    expect(env.submitUIAction).not.toHaveBeenCalled()
  })
})
