import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider, useLocation, useNavigate } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { SettingsModal } from '@/components/layout/settings-modal'
import { renderWithProviders } from '@/test/render'
import { SettingsTabs } from './settings-tabs'
import { useSettingsDirty } from '@/lib/settings-dirty'

function Draft() {
  const [value, setValue] = useState('')
  useSettingsDirty('draft-test', Boolean(value))
  return <input aria-label="未保存表单" value={value} onChange={event => setValue(event.target.value)}/>
}

function LocationText() {
  const location = useLocation()
  return <output aria-label="当前地址">{location.pathname}{location.search}</output>
}

describe('settings navigation', () => {
  it('keeps explicit-save input across internal tabs and preserves unrelated URL parameters', async () => {
    const user = userEvent.setup()
    renderWithProviders(<><SettingsTabs items={[
      { id: 'first', label: '第一组', content: <input aria-label="连接草稿" defaultValue=""/> },
      { id: 'second', label: '第二组', content: <p>用量</p> },
    ]}/><LocationText/></>, { initialEntries: ['/w/ws/settings/models?currency=CNY'] })
    fireEvent.change(screen.getByRole('textbox', { name: '连接草稿' }), { target: { value: 'unsaved endpoint' } })
    await user.click(screen.getByRole('tab', { name: '第二组' }))
    expect(screen.queryByRole('textbox', { name: '连接草稿' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('当前地址')).toHaveTextContent('currency=CNY')
    await user.click(screen.getByRole('tab', { name: '第一组' }))
    expect(screen.getByRole('textbox', { name: '连接草稿' })).toHaveValue('unsaved endpoint')
  })

  it('blocks category navigation without losing the draft when cancellation is chosen', async () => {
    const user = userEvent.setup()
    const confirm = vi.spyOn(window, 'confirm')
    const router = createMemoryRouter([{ path: '/w/:workspaceId/*', element: <SettingsModal open onClose={() => {}}><Draft/></SettingsModal> }], { initialEntries: ['/w/ws/settings/general'] })
    render(<RouterProvider router={router}/>)
    fireEvent.change(screen.getByRole('textbox', { name: '未保存表单' }), { target: { value: '保留修改' } })
    fireEvent.click(screen.getByRole('button', { name: '语音与音频', exact: true }))
    expect(await screen.findByRole('alertdialog', { name: '放弃未保存的修改？' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/w/ws/settings/general')
    const cancel = screen.getByRole('button', { name: '继续编辑' })
    expect(cancel).toHaveFocus()
    await user.click(cancel)
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(screen.getByRole('textbox')).toHaveValue('保留修改')
    fireEvent.click(screen.getByRole('button', { name: '语音与音频', exact: true }))
    await user.click(await screen.findByRole('button', { name: '放弃修改并离开' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/w/ws/settings/voice'))
    expect(confirm).not.toHaveBeenCalled()
  })

  it('guards browser back navigation and refresh for an explicit-save draft', async () => {
    const user = userEvent.setup()
    const router = createMemoryRouter([{ path: '/w/:workspaceId/*', element: <SettingsModal open onClose={() => {}}><Draft/></SettingsModal> }], { initialEntries: ['/w/ws/settings/memory', '/w/ws/settings/general'], initialIndex: 1 })
    render(<RouterProvider router={router}/>)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'draft' } })
    await act(() => router.navigate(-1))
    expect(await screen.findByRole('alertdialog')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(router.state.location.pathname).toBe('/w/ws/settings/general')
    expect(screen.getByRole('textbox')).toHaveValue('draft')
    const unload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unload)
    expect(unload.defaultPrevented).toBe(true)
  })

  it('allows closing settings again after cancelling the discard dialog', async () => {
    const user = userEvent.setup()
    function ClosableSettings() {
      const navigate = useNavigate()
      return <SettingsModal open onClose={() => navigate('/w/ws/chat/new')}><Draft/></SettingsModal>
    }
    const router = createMemoryRouter([
      { path: '/w/:workspaceId/settings/*', element: <ClosableSettings/> },
      { path: '/w/:workspaceId/chat/new', element: <p>对话页面</p> },
    ], { initialEntries: ['/w/ws/settings/account'] })
    render(<RouterProvider router={router}/>)
    await user.type(screen.getByRole('textbox'), 'draft')
    await user.click(screen.getByRole('button', { name: '关闭设置' }))
    await user.click(await screen.findByRole('button', { name: '继续编辑' }))
    await user.click(screen.getByRole('button', { name: '关闭设置' }))
    await user.click(await screen.findByRole('button', { name: '放弃修改并离开' }))
    expect(await screen.findByText('对话页面')).toBeInTheDocument()
  })

  it('navigates directly when there are no unsaved changes', async () => {
    const router = createMemoryRouter([{ path: '/w/:workspaceId/*', element: <SettingsModal open onClose={() => {}}><Draft/></SettingsModal> }], { initialEntries: ['/w/ws/settings/general'] })
    render(<RouterProvider router={router}/>)
    fireEvent.click(screen.getByRole('button', { name: '语音与音频', exact: true }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/w/ws/settings/voice'))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })
})
