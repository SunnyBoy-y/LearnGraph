import { fireEvent, screen } from '@testing-library/react'
import { useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '@/test/render'
import { RouteErrorBoundary } from './route-error-boundary'

describe('route render error recovery', () => {
  it('exposes the actual render error without clearing stored data and can retry', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    localStorage.setItem('route-boundary-draft', '未发送草稿')
    let failed = true
    function Content() {
      if (failed) throw new TypeError('Invalid draft attachment shape')
      return <h1>页面已恢复</h1>
    }
    const view = renderWithProviders(<RouteErrorBoundary><Content /></RouteErrorBoundary>, {
      initialEntries: ['/w/test-workspace/chat/new'],
    })
    expect(screen.getByRole('alert')).toHaveTextContent('页面暂时无法显示')
    fireEvent.click(screen.getByText('查看错误详情'))
    expect(screen.getByText(/TypeError: Invalid draft attachment shape/)).toBeInTheDocument()
    expect(localStorage.getItem('route-boundary-draft')).toBe('未发送草稿')
    const invalidate = vi.spyOn(view.queryClient, 'invalidateQueries').mockResolvedValue(undefined)
    failed = false
    fireEvent.click(screen.getByRole('button', { name: '重新读取页面' }))
    expect(await screen.findByRole('heading', { name: '页面已恢复' })).toBeInTheDocument()
    expect(invalidate).toHaveBeenCalledOnce()
  })

  it('resets the failure and its details on workspace route navigation', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    function Content() {
      const location = useLocation()
      if (location.pathname.endsWith('/new')) throw new Error('Only the new-chat route failed')
      return <h1>图谱书架已打开</h1>
    }
    renderWithProviders(<RouteErrorBoundary><Content /></RouteErrorBoundary>, {
      initialEntries: ['/w/test-workspace/chat/new'],
    })
    const back = screen.getByRole('link', { name: '返回图谱书架' })
    expect(back).toHaveAttribute('href', '/w/test-workspace/graphs')
    fireEvent.click(back)
    expect(await screen.findByRole('heading', { name: '图谱书架已打开' })).toBeInTheDocument()
    expect(screen.queryByText('查看错误详情')).not.toBeInTheDocument()
  })
})
