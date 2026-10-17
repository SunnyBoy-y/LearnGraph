import { act, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TaskStatusIcon } from './task-status-icon'

afterEach(() => vi.useRealTimers())

describe('task completion feedback', () => {
  it('celebrates live completion once, but not restored success or another task', () => {
    vi.useFakeTimers()
    const view = render(<TaskStatusIcon taskKey="a" status="succeeded" />)
    const icon = () => view.container.querySelector('.task-status-icon')!
    expect(icon()).not.toHaveAttribute('data-completed')
    view.rerender(<TaskStatusIcon taskKey="a" status="running" />)
    view.rerender(<TaskStatusIcon taskKey="a" status="succeeded" />)
    expect(icon()).toHaveAttribute('data-completed', 'true')
    act(() => vi.advanceTimersByTime(701))
    expect(icon()).not.toHaveAttribute('data-completed')
    view.rerender(<TaskStatusIcon taskKey="a" status="running" />)
    view.rerender(<TaskStatusIcon taskKey="b" status="succeeded" />)
    expect(icon()).not.toHaveAttribute('data-completed')
  })

  it('keeps failure and cancellation out of the success animation', () => {
    const view = render(<TaskStatusIcon taskKey="a" status="running" />)
    view.rerender(<TaskStatusIcon taskKey="a" status="failed" />)
    expect(view.container.querySelector('[data-completed]')).toBeNull()
    view.rerender(<TaskStatusIcon taskKey="a" status="cancelled" />)
    expect(view.container.querySelector('[data-completed]')).toBeNull()
  })
})
