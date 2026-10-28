import type { TrustedComponentAction } from '@/components/chat/trusted-component-renderer'
import type { GraphChangeSet, MessageCreateRequest } from '@/types/sessions'
import type { NativeSendRequest } from './protocol'

type UIAction = NonNullable<MessageCreateRequest['ui_action']>
export type GraphReviewResult = Pick<GraphChangeSet, 'id' | 'graph_id' | 'status' | 'confirmed_revision' | 'rejection_reason'>
export interface ActionEnvironment {
  sessionId(): string
  send(request: NativeSendRequest): void | Promise<void>
  submitUIAction(sessionId: string, action: UIAction): Promise<{ prompt: string }>
  reviewGraph(sessionId: string, proposalId: string, decision: 'confirm' | 'reject' | 'undo', reason: string): Promise<GraphReviewResult>
  rejectReason(proposalId: string): Promise<string | null>
  graphResolved(result: GraphReviewResult): void
  refresh(): void
}

/** Same action semantics as ChatCanvasPage.handleComponentAction. No model code is executed. */
export async function dispatchRendererAction(action: TrustedComponentAction, environment: ActionEnvironment): Promise<void> {
  const sessionId = environment.sessionId()
  const send = async (content: string, options: Partial<NativeSendRequest> = {}) => {
    if (environment.sessionId() !== sessionId) throw new Error('会话已切换，请重新操作')
    if (!content.trim() || content.length > 50_000) throw new Error('交互内容超出消息长度限制')
    await environment.send({ content, graph_action: 'none', generation_mode: 'text', ...options })
  }
  if (action.componentType === 'code_trainer') {
    if (action.event === 'submit' && typeof action.payload.prompt === 'string') await send(action.payload.prompt, { analysis_only: true })
    return
  }
  if (action.componentType === 'stream_ui') {
    if (action.event !== 'explain' && action.event !== 'graph.node.explain') return
    const revision = Number(action.payload.revision)
    if (!Number.isSafeInteger(revision) || revision < 0) throw new Error('交互组件版本无效')
    const uiAction: UIAction = {
      part_id: action.componentId, revision, action: action.event,
      ...(typeof action.payload.node_id === 'string' ? { node_id: action.payload.node_id } : {}),
    }
    const { prompt } = await environment.submitUIAction(sessionId, uiAction)
    await send(prompt, { ui_action: uiAction })
    return
  }
  if (action.componentType === 'graph_update_proposal') {
    const proposalId = action.payload.proposal_id
    if (typeof proposalId !== 'string' || !proposalId) throw new Error('提案缺少有效标识，未执行任何图谱修改。')
    if (action.event !== 'confirm' && action.event !== 'reject' && action.event !== 'undo') return
    const reason = action.event === 'reject' ? await environment.rejectReason(proposalId) : ''
    if (reason === null) return
    if (environment.sessionId() !== sessionId) throw new Error('会话已切换，请重新操作')
    const result = await environment.reviewGraph(sessionId, proposalId, action.event, reason)
    if (environment.sessionId() !== sessionId) return
    environment.graphResolved(result)
    environment.refresh()
    return
  }
  if (action.componentType === 'question_batch' && action.event === 'submit') {
    const answers = Array.isArray(action.payload.answers) ? action.payload.answers : []
    const lines = answers.map((item, index) => {
      const record = item && typeof item === 'object' ? item as Record<string, unknown> : {}
      const label = typeof record.labels === 'string' ? record.labels : ''
      const value = typeof record.value === 'string' ? record.value : ''
      const prompt = typeof record.prompt === 'string' ? record.prompt : `问题 ${index + 1}`
      return `${index + 1}. ${prompt} → ${label || value || '（跳过）'}`
    })
    await send(`【聚合问答】${lines.join('；')}`)
    return
  }
  if (action.componentType === 'goal_draft_editor' && action.event === 'submit') {
    await send(`【目标草稿更新】请按我修改后的字段确认目标草稿：${JSON.stringify(action.payload.draft ?? action.payload)}`)
    return
  }
  if (!['submit', 'create_plan', 'open_plan'].includes(action.event)) return
  if (action.event === 'submit' && typeof action.payload.summaryText === 'string' && action.payload.summaryText.trim()) {
    await send(action.payload.summaryText.trim())
    return
  }
  const labels = Array.isArray(action.payload.labels) ? action.payload.labels.filter((item): item is string => typeof item === 'string' && !!item.trim()) : []
  let answer = labels.join('、') || (typeof action.payload.answer === 'string' ? action.payload.answer : typeof action.payload.value === 'string' ? action.payload.value : '')
  if (!answer) {
    if (action.event === 'create_plan') {
      const location = typeof action.payload.location === 'string' ? action.payload.location : '当前城市'
      const condition = typeof action.payload.condition === 'string' ? action.payload.condition : ''
      const temperature = typeof action.payload.temperature_c === 'number' ? `${action.payload.temperature_c}°C` : ''
      answer = `请根据${location}的天气（${condition} ${temperature}）生成明日学习计划`
    } else answer = action.event === 'open_plan' ? '请根据卡片中的指标继续分析并给出下一步学习建议' : '跳过该问题'
  } else if (action.event === 'submit') answer = `我的回答：${answer}`
  await send(answer)
}
