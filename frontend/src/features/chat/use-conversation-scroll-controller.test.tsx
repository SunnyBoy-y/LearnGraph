import { act, renderHook } from '@testing-library/react'
import type { WheelEvent } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useConversationScrollController } from './use-conversation-scroll-controller'

const roots: HTMLElement[] = []
afterEach(() => roots.splice(0).forEach(root => root.remove()))
function setup() {
  const frames: FrameRequestCallback[] = []
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(callback => { frames.push(callback); return frames.length })
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {})
  const flush = () => act(() => { for (let count = 0; frames.length && count < 20; count++) frames.shift()!(performance.now()) })
  let height = 2000
  const scroller = document.createElement('div')
  const content = document.createElement('div'); content.className = 'chat-messages-content'
  const message = document.createElement('div'); message.dataset.messageMountId = 'history'
  content.append(message); scroller.append(content); document.body.append(scroller); roots.push(scroller)
  Object.defineProperties(scroller, { clientHeight: { value: 400 }, scrollHeight: { get: () => height } })
  scroller.getBoundingClientRect = () => ({ top: 0, bottom: 400, height: 400, width: 600 } as DOMRect)
  message.getBoundingClientRect = () => ({ top: 100, bottom: 200, height: 100, width: 600 } as DOMRect)
  const hook = renderHook(() => useConversationScrollController())
  act(() => { hook.result.current.scrollRef(scroller); hook.result.current.contentRef(content) })
  flush()
  return { hook, scroller, flush, grow: (amount: number) => { height += amount } }
}
describe('learner-controlled streaming viewport', () => {
  it('never replaces manual reading with the first answer anchor', () => {
    const { hook, scroller, flush } = setup()
    act(() => { hook.result.current.handleWheel({ deltaY: -100 } as WheelEvent<HTMLDivElement>) })
    flush()
    scroller.scrollTop = 250
    act(() => hook.result.current.notifyCommittedAnswerStarted('new-answer'))
    flush()
    expect(hook.result.current.mode).toBe('MANUAL_READING')
    expect(hook.result.current.hasNewContent).toBe(true)
    expect(scroller.scrollTop).toBe(250)
  })
  it('resumes continuous bottom following only after an explicit return to latest', () => {
    const { hook, scroller, flush, grow } = setup()
    act(() => hook.result.current.handleWheel({ deltaY: -100 } as WheelEvent<HTMLDivElement>))
    act(() => hook.result.current.notifyCommittedAnswerStarted('answer'))
    act(() => hook.result.current.returnToLatest())
    flush()
    expect(hook.result.current.mode).toBe('FOLLOW_ACTIVITY')
    expect(scroller.scrollTop).toBe(1600)
    grow(300)
    act(() => hook.result.current.notifyLayoutChanged())
    flush()
    expect(scroller.scrollTop).toBe(1900)
    expect(hook.result.current.hasNewContent).toBe(false)
  })
  it('signals content appended below a manual anchor without moving the reader', () => {
    const { hook, scroller, flush, grow } = setup()
    act(() => hook.result.current.handleWheel({ deltaY: -100 } as WheelEvent<HTMLDivElement>))
    flush(); scroller.scrollTop = 300
    grow(200)
    act(() => hook.result.current.notifyLayoutChanged())
    flush()
    expect(hook.result.current.hasNewContent).toBe(true)
    expect(scroller.scrollTop).toBe(300)
  })
})
