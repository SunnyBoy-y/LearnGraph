/**
 * 练习页「去设置换模型」出口的回归测试（git 旁路：本文件不入公开仓库）。
 *
 * 这条链路的价值全在**跳对地方**：错误文案说「设置 → 功能模型 → 练习出题与
 * 判分模型」，按钮就必须落在 /settings/workspace（工作区设置页的「功能模型」
 * 卡片），不能落到 Provider 页 —— 那里只能改 Provider 默认模型。
 */

import { describe, expect, it } from 'vitest'

import { PRACTICE_MODEL_ERROR_CODES, PracticeModelSettingLink } from '@/features/practice/practice-shared'
import { renderWithProviders } from './render'

describe('PracticeModelSettingLink', () => {
  it('跳到工作区设置页（功能模型卡片所在页）', () => {
    const { getByRole } = renderWithProviders(
      <PracticeModelSettingLink workspaceId="ws-1" />,
      { initialEntries: ['/w/ws-1/practice/session/session-1'] },
    )

    const link = getByRole('link', { name: '去设置出题模型' })
    expect(link.getAttribute('href')).toBe('/w/ws-1/settings/workspace')
  })

  it('支持自定义文案', () => {
    const { getByRole } = renderWithProviders(
      <PracticeModelSettingLink label="更换出题模型" workspaceId="ws-2" />,
    )
    expect(getByRole('link').textContent).toBe('更换出题模型')
  })
})

describe('PRACTICE_MODEL_ERROR_CODES', () => {
  it('只认模型侧的两个错误码，不能把网络错误也当成换模型能修的', () => {
    expect(PRACTICE_MODEL_ERROR_CODES.has('remote_model_required')).toBe(true)
    expect(PRACTICE_MODEL_ERROR_CODES.has('remote_model_rejected_request')).toBe(true)
    expect(PRACTICE_MODEL_ERROR_CODES.has('structured_generation_failed')).toBe(false)
    expect(PRACTICE_MODEL_ERROR_CODES.has('practice_plan_unavailable')).toBe(false)
  })
})
