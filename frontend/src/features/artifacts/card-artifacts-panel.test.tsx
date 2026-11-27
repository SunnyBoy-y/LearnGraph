import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CardArtifactsPanel } from '@/features/artifacts/card-artifacts-panel'
import { renderWithProviders } from '@/test/render'
import type { ArtifactCard, ArtifactCardPreview } from '@/types/artifacts'

const listArtifactCards = vi.fn()
const getArtifactCardPreview = vi.fn()

vi.mock('@/api/artifacts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/artifacts')>()
  return {
    ...actual,
    listArtifactCards: (...args: unknown[]) => listArtifactCards(...args),
    getArtifactCardPreview: (...args: unknown[]) => getArtifactCardPreview(...args),
  }
})

class ImmediateIntersectionObserver {
  private readonly callback: IntersectionObserverCallback
  root = null
  rootMargin = ''
  thresholds: number[] = []

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
  }

  observe(target: Element): void {
    this.callback(
      [{ isIntersecting: true, target } as unknown as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    )
  }
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): IntersectionObserverEntry[] {
    return []
  }
}

function cardFixture(
  index: number,
  overrides: Partial<ArtifactCard> = {},
): ArtifactCard {
  return {
    id: `card-row-${index}`,
    card_id: `card_${index}`,
    card_instance_id: `instance-${index}`,
    card_type: 'magic_card',
    interactive: false,
    title: `产物 ${index}`,
    status: 'draft',
    chat_session_id: `session-${index}`,
    chat_session_title: `学习会话 ${index}`,
    message_id: `message-${index}`,
    version_count: 1,
    latest_version: 1,
    draft_dirty: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    ...overrides,
  }
}

function previewFixture(
  card: ArtifactCard,
  snapshot: Record<string, unknown>,
): ArtifactCardPreview {
  return {
    ...card,
    preview_snapshot: {
      card_id: card.card_id,
      title: card.title,
      ...snapshot,
    },
  }
}

beforeEach(() => {
  vi.stubGlobal(
    'IntersectionObserver',
    ImmediateIntersectionObserver as unknown as typeof IntersectionObserver,
  )
  listArtifactCards.mockReset()
  getArtifactCardPreview.mockReset()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('Artifact Gallery CardArtifactsPanel', () => {
  it('renders clean 16:9 card shell without white overlay and tucks secondary actions into more menu', async () => {
    const user = userEvent.setup()
    const card = cardFixture(1, { artifact_kind: 'html', title: '卷积网络笔记' })
    listArtifactCards.mockResolvedValue([card])
    getArtifactCardPreview.mockResolvedValue(
      previewFixture(card, { preview_html: '<article><h1>CNN</h1><p>静态页面</p></article>' }),
    )

    renderWithProviders(<CardArtifactsPanel workspaceId="ws-1" />)

    const viewport = await waitFor(() => {
      const el = document.querySelector<HTMLElement>('[data-artifact-preview-viewport]')
      expect(el).toBeTruthy()
      return el!
    })
    const outerCard = viewport.closest('article') as HTMLElement

    expect(viewport).toHaveClass('aspect-video')
    expect(viewport).toHaveClass('rounded-[16px]')
    expect(outerCard).toHaveClass('rounded-[24px]', 'p-3')
    expect(screen.queryByText('点击播放')).toBeNull()

    // Title + source below preview, primary action + share in footer
    expect(within(outerCard).getByText('卷积网络笔记')).toBeInTheDocument()
    expect(within(outerCard).getByText('学习会话 1')).toBeInTheDocument()
    expect(within(outerCard).getByRole('button', { name: '打开页面' })).toBeInTheDocument()
    expect(within(outerCard).getByRole('button', { name: '分享 卷积网络笔记' })).toBeInTheDocument()

    // Low-frequency actions are tucked inside the more menu
    expect(screen.queryByRole('button', { name: '删除' })).toBeNull()
    await user.click(within(outerCard).getByRole('button', { name: '更多操作 卷积网络笔记' }))
    expect(await screen.findByRole('menuitem', { name: '详情与版本' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: '重命名' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: '跳转到来源会话' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: '删除' })).toBeInTheDocument()
  })

  it('renders pure SVG directly as img and opens zoomable viewer on click', async () => {
    const card = cardFixture(1, { artifact_kind: 'svg', title: '注意力架构图' })
    listArtifactCards.mockResolvedValue([card])
    getArtifactCardPreview.mockResolvedValue(
      previewFixture(card, {
        preview_html: '<svg viewBox="0 0 120 60"><rect width="120" height="60"/></svg>',
      }),
    )

    renderWithProviders(<CardArtifactsPanel workspaceId="ws-1" />)

    const img = await screen.findByAltText('注意力架构图')
    expect(img.getAttribute('src')).toContain('data:image/svg+xml')
    expect(document.querySelector('iframe')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '查看预览' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('button', { name: '下载 SVG' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '源码' })).toBeInTheDocument()
    expect(within(dialog).getByText('100%')).toBeInTheDocument()

    fireEvent.click(within(dialog).getByTitle('放大'))
    expect(within(dialog).getByText('125%')).toBeInTheDocument()
  })

  it('launches MagicCardHost fill runtime when clicking an interactive app card', async () => {
    const card = cardFixture(2, {
      artifact_kind: 'interactive',
      interactive: true,
      title: '梯度下降模拟器',
    })
    listArtifactCards.mockResolvedValue([card])
    getArtifactCardPreview.mockResolvedValue(
      previewFixture(card, {
        runtime: 'opaque-origin-subapp-v1',
        subapp_mode: true,
        preview_html: '<div><button onclick="step()">Step</button></div>',
      }),
    )

    renderWithProviders(<CardArtifactsPanel workspaceId="ws-1" />)

    const launchBtn = await screen.findByRole('button', { name: '运行应用' })
    fireEvent.click(launchBtn)

    const dialog = await screen.findByRole('dialog')
    const section = dialog.querySelector('section.magic-card--fill')
    expect(section).toBeTruthy()
  })

  it('keeps animation covers static and puts the type outside the preview', async () => {
    const card = cardFixture(3, { artifact_kind: 'animation', title: '波动传播动画' })
    listArtifactCards.mockResolvedValue([card])
    getArtifactCardPreview.mockResolvedValue(
      previewFixture(card, {
        preview_html: '<style>@keyframes k{from{opacity:0}to{opacity:1}}</style><div style="animation:k 1s"></div>',
      }),
    )

    renderWithProviders(<CardArtifactsPanel workspaceId="ws-1" />)

    const viewport = await waitFor(() => {
      const el = document.querySelector<HTMLElement>('[data-artifact-preview-viewport]')
      expect(el).toBeTruthy()
      return el!
    })
    const iframe = await waitFor(() => {
      const f = viewport.querySelector('iframe')
      expect(f).toBeTruthy()
      return f!
    })

    expect(iframe.getAttribute('sandbox')).toBe('')
    expect(iframe.srcdoc).toContain('animation-play-state:paused')
    expect(viewport.querySelector('[data-artifact-kind-badge]')).toBeNull()
    expect(viewport.closest('article')?.querySelector('[data-artifact-kind-badge]')).toBeTruthy()
  })

  it('preserves selection across pages and view modes', async () => {
    const user = userEvent.setup()
    const cards = Array.from({ length: 15 }, (_, i) => cardFixture(i + 1, { artifact_kind: 'html' }))
    listArtifactCards.mockResolvedValue(cards)
    getArtifactCardPreview.mockImplementation((id: string) => {
      const card = cards.find((item) => item.card_id === id)!
      return Promise.resolve(previewFixture(card, { preview_html: '<p>预览</p>' }))
    })
    renderWithProviders(<CardArtifactsPanel workspaceId="ws-1" />)
    await screen.findByText('产物 1')
    await user.click(screen.getByRole('button', { name: '多选' }))
    await user.click(screen.getByRole('checkbox', { name: '选择 产物 1' }))
    await user.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByText('已选择 1 项 · 本页 0 项')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: '选择 产物 13' }))
    await user.click(screen.getByRole('button', { name: '列表视图' }))
    expect(screen.getByRole('checkbox', { name: '选择 产物 13' })).toBeChecked()
    expect(screen.getByText('已选择 2 项 · 本页 1 项')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '批量删除' }))
    expect(screen.getByText(/范围包括所有已选项/)).toBeInTheDocument()
    expect(screen.getByRole('alertdialog')).toHaveTextContent('2 项')
  })

  it('filters cards by search query and resets filters', async () => {
    const user = userEvent.setup()
    const c1 = cardFixture(1, { artifact_kind: 'svg', title: '矩阵乘法图解' })
    const c2 = cardFixture(2, { artifact_kind: 'html', title: 'Transformer 论文总结' })
    listArtifactCards.mockResolvedValue([c1, c2])
    getArtifactCardPreview.mockImplementation((id: string) =>
      Promise.resolve(
        id === 'card_1'
          ? previewFixture(c1, { preview_html: '<svg viewBox="0 0 10 10"/>' })
          : previewFixture(c2, { preview_html: '<p>Transformer</p>' }),
      ),
    )

    renderWithProviders(<CardArtifactsPanel workspaceId="ws-1" />)

    await screen.findByText('矩阵乘法图解')
    await screen.findByText('Transformer 论文总结')

    await user.type(screen.getByLabelText('搜索产物'), 'Transformer')

    expect(screen.queryByText('矩阵乘法图解')).toBeNull()
    expect(screen.getByText('Transformer 论文总结')).toBeInTheDocument()
    expect(screen.getByText('匹配产物 · 1 / 2')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '重置筛选' }))
    expect(screen.getByText('矩阵乘法图解')).toBeInTheDocument()
    expect(screen.queryByText(/匹配产物/)).not.toBeInTheDocument()
  })
})
