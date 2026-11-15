import { act, cleanup, render, renderHook, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { ComposerFeedback, useComposerFeedback } from './composer-feedback'
afterEach(cleanup)
describe('scoped composer feedback', () => {
  it('stays available until resolved or dismissed', () => {
    render(<ComposerFeedback id="feedback" notice={{ message: '请选择模型' }} />)
    expect(screen.getByRole('status').textContent).toContain('请选择模型')
  })
  it('hides old feedback on session switch and rejects late callbacks', () => {
    const { result, rerender } = renderHook(({ scope }) => useComposerFeedback(scope), { initialProps: { scope: 'a' } })
    const oldShow = result.current.show
    act(() => oldShow('附件不可用'))
    expect(result.current.notice?.message).toBe('附件不可用')
    rerender({ scope: 'b' })
    expect(result.current.notice).toBeNull()
    act(() => result.current.show('请选择模型'))
    act(() => oldShow('旧请求迟到'))
    expect(result.current.notice?.message).toBe('请选择模型')
    act(() => result.current.clear())
    expect(result.current.notice).toBeNull()
  })
})
