import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PromptInput, PromptInputTextarea } from './prompt-input'

function setup() {
  const submit = vi.fn().mockResolvedValue(false)
  const external = vi.fn()
  const compositionStart = vi.fn(), compositionEnd = vi.fn()
  render(<PromptInput onSubmit={submit}><PromptInputTextarea aria-label="输入测试" defaultValue="中文" onKeyDown={external} onCompositionStart={compositionStart} onCompositionEnd={compositionEnd} /><button type="submit">发送</button></PromptInput>)
  return { input: screen.getByLabelText('输入测试'), submit, external, compositionStart, compositionEnd }
}
describe('IME owns the composer before menu shortcuts', () => {
  it.each(['Enter', 'ArrowUp', 'ArrowDown', 'Tab', 'Escape', 'Backspace'])('does not pass composing %s to a menu or submit', key => {
    const { input, external, submit, compositionStart } = setup()
    fireEvent.compositionStart(input)
    fireEvent.keyDown(input, { key })
    expect(compositionStart).toHaveBeenCalledOnce()
    expect(external).not.toHaveBeenCalled()
    expect(submit).not.toHaveBeenCalled()
  })
  it('consumes only the first companion Enter and allows the next normal Enter', async () => {
    const { input, external, submit, compositionEnd } = setup()
    fireEvent.compositionStart(input)
    fireEvent.compositionEnd(input)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(compositionEnd).toHaveBeenCalledOnce()
    expect(external).not.toHaveBeenCalled()
    expect(submit).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(submit).toHaveBeenCalledOnce())
  })
  it('lets Shift+Enter insert a newline even when an external menu handles Enter', () => {
    const { input, external, submit } = setup()
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(external).not.toHaveBeenCalled()
    expect(submit).not.toHaveBeenCalled()
  })
  it('also guards native isComposing when compositionstart was not observed', () => {
    const { input, external, submit } = setup()
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(external).not.toHaveBeenCalled()
    expect(submit).not.toHaveBeenCalled()
  })
  it('does not swallow Enter after a subsequent normal character', async () => {
    const { input, submit } = setup()
    fireEvent.compositionStart(input)
    fireEvent.compositionEnd(input)
    fireEvent.keyDown(input, { key: 'a' })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(submit).toHaveBeenCalledOnce())
  })
})
