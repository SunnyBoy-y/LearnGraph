import { useEffect } from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import { createMemoryRouter, Outlet, RouterProvider, useParams } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { RouteMotion } from './route-motion'

function fixture(path: string) {
  const mounted = vi.fn(), unmounted = vi.fn()
  function Page() {
    const params = useParams()
    useEffect(() => { mounted(); return () => { unmounted() } }, [])
    return <main><output>{Object.values(params).join(':')}</output><input aria-label="页面草稿" defaultValue="未发送内容" /></main>
  }
  const router = createMemoryRouter([{
    path: '/w/:workspaceId',
    element: <RouteMotion><Outlet /></RouteMotion>,
    children: ['chat/:sessionId', 'graphs/:graphId', 'home', 'sources'].map(path => ({ path, element: <Page /> })),
  }], { initialEntries: [path] })
  const view = render(<RouterProvider router={router} />)
  return { router, view, mounted, unmounted }
}

describe('route motion lifecycle', () => {
  it('keeps the live chat canvas mounted through promotion, query changes and session switches', async () => {
    const { router, mounted, unmounted } = fixture('/w/one/chat/new')
    const input = screen.getByRole('textbox')
    for (const path of ['/w/one/chat/saved', '/w/one/chat/saved?panel=files', '/w/one/chat/other']) {
      await act(async () => { await router.navigate(path) })
      expect(screen.getByRole('textbox')).toBe(input)
    }
    expect(mounted).toHaveBeenCalledTimes(1)
    expect(unmounted).not.toHaveBeenCalled()
  })

  it('preserves the graph for node and panel query changes, and remounts for a different graph', async () => {
    const { router, mounted } = fixture('/w/one/graphs/first')
    const input = screen.getByRole('textbox')
    await act(async () => { await router.navigate('/w/one/graphs/first?node=two&panel=plan') })
    expect(screen.getByRole('textbox')).toBe(input)
    expect(mounted).toHaveBeenCalledTimes(1)
    await act(async () => { await router.navigate('/w/one/graphs/second') })
    await waitFor(() => expect(screen.getByRole('textbox')).not.toBe(input))
    expect(input).not.toBeInTheDocument()
  })

  it('settles on the latest destination after rapid navigation and allows returning to chat', async () => {
    const { router } = fixture('/w/one/chat/first')
    await act(async () => {
      await router.navigate('/w/one/home')
      await router.navigate('/w/one/sources')
      await router.navigate('/w/one/graphs/second')
    })
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('one:second'))
    await waitFor(() => expect(document.querySelectorAll('.workspace-route-motion')).toHaveLength(1))
    await act(async () => { await router.navigate('/w/one/chat/first') })
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('one:first'))
    await waitFor(() => expect(document.querySelectorAll('.workspace-route-motion')).toHaveLength(1))
  })

  it('keeps the current page mounted when the OS motion preference changes', () => {
    let reduced = false
    const listeners = new Set<() => void>()
    vi.spyOn(window, 'matchMedia').mockImplementation(query => ({
      media: query, get matches() { return reduced },
      addEventListener: (_: string, cb: () => void) => { listeners.add(cb) },
      removeEventListener: (_: string, cb: () => void) => { listeners.delete(cb) },
    }) as MediaQueryList)
    const { mounted } = fixture('/w/one/chat/first')
    const input = screen.getByRole('textbox')
    act(() => { reduced = true; listeners.forEach(listener => listener()) })
    expect(screen.getByRole('textbox')).toBe(input)
    expect(mounted).toHaveBeenCalledTimes(1)
  })
})
