import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { listSettings, updateSetting } from '@/api'
import { readLocalAppearance } from '@/lib/appearance'
import { renderWithProviders } from '@/test/render'
import { AppearanceSettings } from './appearance-settings'

vi.mock('@/api', () => ({ listSettings: vi.fn(), updateSetting: vi.fn() }))
let remote: Record<string, unknown>
beforeEach(() => {
  remote = { theme: 'dark', sidebar_project_folders: { project: false }, anotherPreference: 42 }
  vi.mocked(listSettings).mockImplementation(async () => [{ key: 'ui.preferences', value: remote, updated_at: '' }])
  vi.mocked(updateSetting).mockImplementation(async (_key, value) => {
    remote = value as Record<string, unknown>
    return { key: 'ui.preferences', value, updated_at: '' }
  })
})
afterEach(() => {
  document.documentElement.classList.remove('dark')
  delete document.documentElement.dataset.colorMode
})

describe('workspace appearance settings', () => {
  it('saves a palette without dropping sidebar preferences or the selected mode', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppearanceSettings/>)
    await user.click(await screen.findByRole('radio', { name: '松绿' }))
    await waitFor(() => expect(updateSetting).toHaveBeenCalledWith('ui.preferences', expect.objectContaining({
      theme: 'dark', colorTheme: 'forest', sidebar_project_folders: { project: false }, anotherPreference: 42,
    })))
    expect(document.documentElement).toHaveClass('dark')
    expect(readLocalAppearance().colorTheme).toBe('forest')
  })

  it('validates HEX and saves a custom color with keyboard submission', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AppearanceSettings/>)
    const input = await screen.findByRole('textbox', { name: '自定义主题色 HEX' })
    fireEvent.change(input, { target: { value: '#nope' } })
    await user.click(screen.getByRole('button', { name: '应用颜色' }))
    expect(screen.getByRole('alert')).toHaveTextContent('六位十六进制')
    expect(updateSetting).not.toHaveBeenCalled()
    fireEvent.change(input, { target: { value: '#FFAA00' } })
    await user.type(input, '{Enter}')
    await waitFor(() => expect(updateSetting).toHaveBeenCalledWith('ui.preferences', expect.objectContaining({ colorTheme: 'custom', customColor: '#ffaa00' })))
    expect(document.documentElement.dataset.colorMode).toBe('custom')
  })

  it('rolls back visible and persisted preferences if saving fails', async () => {
    vi.mocked(updateSetting).mockRejectedValue(new Error('保存不可用'))
    const user = userEvent.setup()
    renderWithProviders(<AppearanceSettings/>)
    await user.click(await screen.findByRole('radio', { name: '海蓝' }))
    await waitFor(() => expect(screen.getByRole('radio', { name: '石墨' })).toBeChecked())
    expect(readLocalAppearance()).toMatchObject({ theme: 'dark', colorTheme: 'mono' })
    expect(document.documentElement.dataset.colorMode).toBe('mono')
  })

  it('prevents overlapping saves and restores the saved choice on remount', async () => {
    let finish: (value: Awaited<ReturnType<typeof updateSetting>>) => void
    vi.mocked(updateSetting).mockImplementation((_key, value) => new Promise(resolve => {
      finish = saved => { remote = value as Record<string, unknown>; resolve(saved) }
    }))
    const user = userEvent.setup()
    const view = renderWithProviders(<AppearanceSettings/>)
    await user.click(await screen.findByRole('radio', { name: '鸢紫' }))
    expect(screen.getByRole('radio', { name: '海蓝' })).toBeDisabled()
    finish!({ key: 'ui.preferences', value: { ...remote, colorTheme: 'violet' }, updated_at: '' })
    await waitFor(() => expect(screen.getByRole('radio', { name: '海蓝' })).toBeEnabled())
    view.unmount()
    renderWithProviders(<AppearanceSettings/>)
    expect(await screen.findByRole('radio', { name: '鸢紫' })).toBeChecked()
  })
})
