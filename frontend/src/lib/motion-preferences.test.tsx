import { act, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Shimmer } from '@/components/ai-elements/shimmer'
import { motionDuration, useReducedMotionPreference } from './motion-preferences'

describe('OS motion preference', () => {
  it('reads first paint synchronously and updates existing subscribers live', () => {
    let reduce = true
    const listeners = new Set<() => void>()
    vi.spyOn(window, 'matchMedia').mockImplementation(query => ({
      media: query, get matches() { return reduce }, onchange: null,
      addEventListener: (_type: string, callback: () => void) => { listeners.add(callback) },
      removeEventListener: (_type: string, callback: () => void) => { listeners.delete(callback) },
      addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: () => false,
    }) as MediaQueryList)
    function Preference() { return <output>{String(useReducedMotionPreference())}</output> }
    const view = render(<><Preference /><Shimmer>正在思考</Shimmer></>)
    expect(screen.getByRole('status')).toHaveTextContent('true')
    expect(screen.getByText('正在思考')).not.toHaveClass('text-transparent')
    expect(motionDuration(480)).toBe(0)
    act(() => { reduce = false; listeners.forEach(listener => listener()) })
    expect(screen.getByRole('status')).toHaveTextContent('false')
    expect(motionDuration(480)).toBe(480)
    const subscriptions = listeners.size
    view.unmount()
    expect(listeners.size).toBeLessThan(subscriptions)
  })
})
