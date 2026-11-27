import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MagicCardHost } from '@/components/chat/magic-card-host'
import { UnifiedArtifactCard } from '@/components/chat/unified-artifact-card'

afterEach(() => {
  vi.useRealTimers()
})

describe('MagicCardHost adaptive sizing and runtime controls', () => {
  it('supports bidirectional grow and shrink and separates header runtime pause semantics', () => {
    vi.useFakeTimers()
    render(
      <MagicCardHost
        data={{
          card_id: 'pelican-bike',
          title: '鹈鹕骑自行车',
          preferred_height: 520,
          preview_html: '<div class="app"><svg viewBox="0 0 400 240"></svg><button>暂停动画</button></div>',
        }}
      />,
    )

    const section = screen.getByRole('region', { name: '鹈鹕骑自行车' })
    expect(section).toHaveAttribute('data-height-mode', 'auto')
    expect(screen.getByText('暂停运行')).toBeInTheDocument()

    const iframe = screen.getByTitle('鹈鹕骑自行车 动态预览') as HTMLIFrameElement
    expect(iframe.getAttribute('srcdoc')).toContain('lg.artifact-size')
    expect(iframe.style.height).toBe('520px')

    Object.defineProperty(iframe, 'clientWidth', { configurable: true, value: 420 })
    const fakeWin = { postMessage: vi.fn() } as unknown as Window
    Object.defineProperty(iframe, 'contentWindow', { configurable: true, value: fakeWin })

    // Shrinks when narrow viewport reduces SVG height.
    act(() => {
      window.dispatchEvent(new MessageEvent('message', {
        data: { kind: 'lg.artifact-size', width: 420, height: 280 },
        source: fakeWin,
        origin: 'null',
      }))
      vi.advanceTimersByTime(80)
    })
    expect(iframe.style.height).toBe('280px')

    // Grows again when content expands (e.g., accordion opens or window widens).
    act(() => {
      window.dispatchEvent(new MessageEvent('message', {
        data: { kind: 'lg.artifact-size', width: 420, height: 410 },
        source: fakeWin,
        origin: 'null',
      }))
      vi.advanceTimersByTime(80)
    })
    expect(iframe.style.height).toBe('410px')

    // Oscillation guard stops A -> B -> A feedback loops at the same width.
    act(() => {
      window.dispatchEvent(new MessageEvent('message', {
        data: { kind: 'lg.artifact-size', width: 420, height: 280 },
        source: fakeWin,
        origin: 'null',
      }))
      vi.advanceTimersByTime(80)
    })
    expect(iframe.style.height).toBe('410px')

    // Header runtime toggle clearly flips between 暂停运行 and 恢复运行.
    fireEvent.click(screen.getByLabelText('暂停播放 鹈鹕骑自行车'))
    expect(screen.getByText('恢复运行')).toBeInTheDocument()
  })

  it('respects bounded, fixed, and aspect-ratio height modes on MagicCardHost', () => {
    vi.useFakeTimers()
    const { rerender } = render(
      <MagicCardHost
        data={{
          card_id: 'data-table',
          title: '数据面板',
          height_mode: 'bounded',
          max_height: 400,
          preview_html: '<table><tr><td>Row</td></tr></table>',
        }}
      />,
    )
    const boundedFrame = screen.getByTitle('数据面板 动态预览') as HTMLIFrameElement
    Object.defineProperty(boundedFrame, 'clientWidth', { configurable: true, value: 600 })
    const fakeWin = { postMessage: vi.fn() } as unknown as Window
    Object.defineProperty(boundedFrame, 'contentWindow', { configurable: true, value: fakeWin })

    act(() => {
      window.dispatchEvent(new MessageEvent('message', {
        data: { kind: 'lg.artifact-size', width: 600, height: 950 },
        source: fakeWin,
        origin: 'null',
      }))
      vi.advanceTimersByTime(80)
    })
    expect(boundedFrame.style.height).toBe('400px')

    rerender(
      <MagicCardHost
        data={{
          card_id: 'scene-3d',
          title: '3D 场景',
          height_mode: 'fixed',
          preferred_height: 500,
          preview_html: '<canvas></canvas>',
        }}
      />,
    )
    const fixedFrame = screen.getByTitle('3D 场景 动态预览') as HTMLIFrameElement
    expect(fixedFrame.getAttribute('srcdoc')).not.toContain('lg.artifact-size')
    expect(fixedFrame.style.height).toBe('500px')

    rerender(
      <MagicCardHost
        data={{
          card_id: 'ratio-box',
          title: '比例视图',
          height_mode: 'aspect-ratio',
          aspect_ratio: '16 / 9',
          preview_html: '<div>ratio</div>',
        }}
      />,
    )
    const ratioFrame = screen.getByTitle('比例视图 动态预览') as HTMLIFrameElement
    expect(ratioFrame.style.aspectRatio).toBe('16 / 9')
    expect(ratioFrame.style.height).toBe('auto')
  })

  it('auto-sizes interactive HTML inside UnifiedArtifactCard as well', () => {
    vi.useFakeTimers()
    render(
      <UnifiedArtifactCard
        part={{
          id: 'part-html-1',
          type: 'sandbox_artifact',
          status: 'completed',
          content: 'demo.html',
          data: {
            title: '交互示例',
            path: 'demo.html',
            mime_type: 'text/html',
            preview_html: '<main><button>点我</button></main>',
          },
        }}
      />,
    )

    const iframe = screen.getByTitle('交互示例') as HTMLIFrameElement
    expect(iframe.getAttribute('srcdoc')).toContain('lg.artifact-size')
    Object.defineProperty(iframe, 'clientWidth', { configurable: true, value: 380 })
    const fakeWin = { postMessage: vi.fn() } as unknown as Window
    Object.defineProperty(iframe, 'contentWindow', { configurable: true, value: fakeWin })

    act(() => {
      window.dispatchEvent(new MessageEvent('message', {
        data: { kind: 'lg.artifact-size', width: 380, height: 220 },
        source: fakeWin,
        origin: 'null',
      }))
      vi.advanceTimersByTime(80)
    })
    expect(iframe.style.height).toBe('220px')
  })
})
