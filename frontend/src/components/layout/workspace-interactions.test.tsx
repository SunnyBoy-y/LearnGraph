import { fireEvent, render, screen } from '@testing-library/react'
import { useCallback, useRef, useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { useModalDrawerFocus } from './use-modal-drawer-focus'
import { useWorkspaceRailResize } from './use-workspace-rail-resize'

function ResizeFixture() {
  const [width, setWidth] = useState(360)
  const [collapsed, setCollapsed] = useState(false)
  const collapse = useCallback(() => setCollapsed(true), [])
  const resize = useWorkspaceRailResize({ width, setWidth, sidebarWidth: 50, disabled: collapsed, collapse })
  return <><output data-testid="width">{width}</output><output data-testid="collapsed">{String(collapsed)}</output><div role="separator" tabIndex={0} onPointerDown={resize.begin} onKeyDown={resize.onKeyDown} /></>
}
describe('workspace separator', () => {
  it('supports keyboard width adjustment and Home/End limits', () => {
    render(<ResizeFixture />)
    const separator = screen.getByRole('separator')
    fireEvent.keyDown(separator, { key: 'ArrowLeft' })
    expect(screen.getByTestId('width')).toHaveTextContent('376')
    fireEvent.keyDown(separator, { key: 'Home' })
    expect(screen.getByTestId('width')).toHaveTextContent('280')
    fireEvent.keyDown(separator, { key: 'End' })
    expect(Number(screen.getByTestId('width').textContent)).toBe(Math.max(280, window.innerWidth - 410))
  })
  it('restores width on Escape and removes drag listeners', () => {
    render(<ResizeFixture />)
    fireEvent.pointerDown(screen.getByRole('separator'), { pointerId: 7, pointerType: 'mouse', button: 0 })
    fireEvent.pointerMove(window, { pointerId: 7, clientX: window.innerWidth - 450 })
    expect(screen.getByTestId('width')).toHaveTextContent('450')
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.getByTestId('width')).toHaveTextContent('360')
    expect(document.body).not.toHaveClass('is-resizing')
    fireEvent.pointerMove(window, { pointerId: 7, clientX: 0 })
    expect(screen.getByTestId('width')).toHaveTextContent('360')
  })
  it('cancels pointer gestures and cleans up on unmount', () => {
    const view = render(<ResizeFixture />)
    const separator = screen.getByRole('separator')
    fireEvent.pointerDown(separator, { pointerId: 9 })
    fireEvent.pointerMove(window, { pointerId: 9, clientX: 600 })
    fireEvent.pointerCancel(window, { pointerId: 9 })
    expect(screen.getByTestId('width')).toHaveTextContent('360')
    fireEvent.pointerDown(separator, { pointerId: 9 })
    view.unmount()
    expect(document.body).not.toHaveClass('is-resizing')
  })
  it('snaps closed only after release, never during Escape cancellation', () => {
    render(<ResizeFixture />)
    fireEvent.pointerDown(screen.getByRole('separator'), { pointerId: 2 })
    fireEvent.pointerMove(window, { pointerId: 2, clientX: window.innerWidth - 100 })
    expect(screen.getByTestId('collapsed')).toHaveTextContent('false')
    fireEvent.pointerUp(window, { pointerId: 2 })
    expect(screen.getByTestId('collapsed')).toHaveTextContent('true')
    expect(document.body).not.toHaveClass('is-resizing')
  })
})

function DrawerFixture({ open, close }: { open: boolean; close: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  useModalDrawerFocus(ref, open, close)
  return <div><main data-testid="background"><button>背景按钮</button></main><div ref={ref} role="dialog" tabIndex={-1}><button>第一个</button><button>最后一个</button><div hidden><button>隐藏菜单项</button></div></div></div>
}
describe('persistent modal drawer', () => {
  it('makes the background inert, loops focus and restores interaction on close', () => {
    const close = vi.fn()
    const trigger = document.createElement('button'); document.body.append(trigger); trigger.focus()
    const view = render(<DrawerFixture open close={close} />)
    const first = screen.getByRole('button', { name: '第一个' }), last = screen.getByRole('button', { name: '最后一个' })
    expect(screen.getByTestId('background').inert).toBe(true)
    expect(first).toHaveFocus()
    last.focus(); fireEvent.keyDown(last, { key: 'Tab' }); expect(first).toHaveFocus()
    fireEvent.keyDown(first, { key: 'Tab', shiftKey: true }); expect(last).toHaveFocus()
    fireEvent.keyDown(last, { key: 'Escape' }); expect(close).toHaveBeenCalledOnce()
    view.rerender(<DrawerFixture open={false} close={close} />)
    expect(screen.getByTestId('background').inert).toBe(false)
    expect(trigger).toHaveFocus()
    trigger.remove()
  })
})
