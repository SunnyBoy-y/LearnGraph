import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppToaster } from './app-toaster'
vi.mock('sonner', () => ({ Toaster: (props: { theme: string; visibleToasts: number; containerAriaLabel: string; toastOptions: { closeButtonAriaLabel: string } }) => <div data-testid="surface" data-theme={props.theme} data-limit={props.visibleToasts} aria-label={props.containerAriaLabel}>{props.toastOptions.closeButtonAriaLabel}</div>, toast: { dismiss: vi.fn() } }))
afterEach(() => { cleanup(); document.documentElement.classList.remove('dark'); vi.restoreAllMocks() })
describe('shared notification surface', () => {
  it('follows application theme changes rather than the OS theme', async () => {
    render(<AppToaster />)
    expect(screen.getByTestId('surface').getAttribute('data-theme')).toBe('light')
    await act(async () => { document.documentElement.classList.add('dark') })
    expect(screen.getByTestId('surface').getAttribute('data-theme')).toBe('dark')
    expect(screen.getByTestId('surface').getAttribute('data-limit')).toBe('2')
    expect(screen.getByLabelText('通知').textContent).toBe('关闭通知')
  })
  it('keeps offline status visible until the browser reports recovery', async () => {
    const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    render(<AppToaster />)
    expect(screen.getByRole('status').textContent).toContain('网络已断开')
    online.mockReturnValue(true)
    await act(async () => { window.dispatchEvent(new Event('online')) })
    expect(screen.queryByRole('status')).toBeNull()
  })
})
