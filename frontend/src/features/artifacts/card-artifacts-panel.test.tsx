import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CardArtifactsPanel } from '@/features/artifacts/card-artifacts-panel'
import { pauseAllPreviews, resumeAllPreviews } from '@/lib/preview-pause-controller'
import { renderWithProviders } from '@/test/render'
import type { ArtifactCard, ArtifactCardPreview } from '@/types/artifacts'

/**
 * The artifact wall owns playback: nothing runs until the user clicks a card, and
 * exactly one card runs at a time. Leaving the viewport suspends a preview instead
 * of throwing its iframe away, so scrolling back finds the same frame paused where
 * it stopped rather than restarting from zero.
 */

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

function cardFixture(index: number): ArtifactCard {
  return {
    id: `card-row-${index}`,
    card_id: `card_${index}`,
    card_instance_id: `instance-${index}`,
    card_type: 'magic_card',
    interactive: false,
    title: `卡片 ${index}`,
    status: 'draft',
    chat_session_id: `session-${index}`,
    message_id: `message-${index}`,
    version_count: 1,
    latest_version: 1,
    draft_dirty: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
  }
}

function previewFixture(index: number): ArtifactCardPreview {
  return {
    ...cardFixture(index),
    preview_snapshot: {
      card_id: `card_${index}`,
      title: `快照 ${index}`,
      preview_html: `<div id="anim-${index}">动画 ${index}</div>`,
    },
  }
}

/** Controllable IntersectionObserver so a test can scroll cards out of the band. */
class FakeIntersectionObserver {
  static instances: FakeIntersectionObserver[] = []
  private readonly callback: IntersectionObserverCallback
  private readonly targets: Element[] = []
  root = null
  rootMargin = ''
  thresholds: number[] = []

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
    FakeIntersectionObserver.instances.push(this)
  }

  observe(target: Element): void {
    this.targets.push(target)
  }
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): IntersectionObserverEntry[] {
    return []
  }
  emit(isIntersecting: boolean): void {
    this.callback(
      [{ isIntersecting, target: this.targets[0] } as unknown as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    )
  }
}

/** Preview wrappers in DOM order, which matches the card list order. */
function previewBoxes(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>('[data-preview-suspended]')]
}

function suspendedStates(): (string | undefined)[] {
  return previewBoxes().map((box) => box.dataset.previewSuspended)
}

async function renderWall(indexes: number[]) {
  listArtifactCards.mockResolvedValue(indexes.map(cardFixture))
  getArtifactCardPreview.mockImplementation((cardId: string) =>
    Promise.resolve(previewFixture(Number(cardId.replace('card_', '')))),
  )
  const result = renderWithProviders(<CardArtifactsPanel workspaceId="ws-1" />)
  await waitFor(() => expect(previewBoxes()).toHaveLength(indexes.length))
  return result
}

beforeEach(() => {
  FakeIntersectionObserver.instances = []
  listArtifactCards.mockReset()
  getArtifactCardPreview.mockReset()
  resumeAllPreviews()
})

afterEach(() => {
  vi.unstubAllGlobals()
  resumeAllPreviews()
})

describe('artifact card wall playback', () => {
  it('uses one outer card frame and keeps the action row below the preview', async () => {
    await renderWall([1])

    const viewport = document.querySelector<HTMLElement>('[data-artifact-preview-viewport]')
    const outerCard = viewport?.closest('article')

    expect(viewport).toBeTruthy()
    expect(viewport?.className).not.toContain('border')
    expect(viewport?.className).not.toContain('rounded')
    expect(outerCard?.className).toContain('border')
    expect(outerCard).toHaveClass('p-0')
    expect(outerCard).not.toHaveClass('p-3')
    expect(within(outerCard as HTMLElement).getByRole('button', { name: '会话' })).toBeTruthy()
    expect(
      within(outerCard as HTMLElement).getByRole('button', { name: '版本与分享 卡片 1' }),
    ).toBeTruthy()
    expect(within(outerCard as HTMLElement).getByRole('button', { name: '删除 卡片 1' })).toBeTruthy()
  })

  it('starts every card paused with a click-to-play affordance', async () => {
    await renderWall([1, 2])

    await waitFor(() => expect(document.querySelectorAll('iframe')).toHaveLength(2))
    expect(suspendedStates()).toEqual(['true', 'true'])
    expect(screen.getAllByText('点击播放')).toHaveLength(2)
    document.querySelectorAll('iframe').forEach((frame) => {
      expect(frame.dataset.previewPaused).toBe('true')
    })
  })

  it('plays the clicked card and keeps the others paused', async () => {
    await renderWall([1, 2])
    await waitFor(() => expect(document.querySelectorAll('iframe')).toHaveLength(2))
    const boxes = previewBoxes()

    // Clicking the card itself — not just the curtain — starts it.
    fireEvent.click(screen.getByText('卡片 1'))

    await waitFor(() => expect(suspendedStates()).toEqual(['false', 'true']))
    expect(boxes[0]!.querySelector('iframe')?.dataset.previewPaused).toBe('false')
    expect(boxes[1]!.querySelector('iframe')?.dataset.previewPaused).toBe('true')
    expect(within(boxes[0]!).queryByText('点击播放')).toBeNull()
    expect(within(boxes[1]!).getByText('点击播放')).toBeTruthy()

    // A card's own control keeps its own job: this must not claim playback.
    fireEvent.click(screen.getByLabelText('删除 卡片 2'))
    await waitFor(() => expect(suspendedStates()).toEqual(['false', 'true']))
  })

  it('hands playback over to the next clicked card', async () => {
    await renderWall([1, 2])
    await waitFor(() => expect(document.querySelectorAll('iframe')).toHaveLength(2))
    const boxes = previewBoxes()

    fireEvent.click(within(boxes[0]!).getByTitle('播放 卡片 1'))
    await waitFor(() => expect(suspendedStates()).toEqual(['false', 'true']))

    fireEvent.click(previewBoxes()[1]!.querySelector('button[title]') as HTMLElement)

    await waitFor(() => expect(suspendedStates()).toEqual(['true', 'false']))
    expect(boxes[0]!.querySelector('iframe')?.dataset.previewPaused).toBe('true')
    expect(boxes[1]!.querySelector('iframe')?.dataset.previewPaused).toBe('false')
  })

  it('lets a click override the page-level pause switch', async () => {
    await renderWall([1, 2])
    await waitFor(() => expect(document.querySelectorAll('iframe')).toHaveLength(2))
    const boxes = previewBoxes()

    act(() => pauseAllPreviews())
    await waitFor(() => expect(suspendedStates()).toEqual(['true', 'true']))

    // The page switch was on: clicking a card is an explicit request to play it.
    fireEvent.click(within(boxes[0]!).getByTitle('播放 卡片 1'))

    await waitFor(() => expect(suspendedStates()).toEqual(['false', 'true']))
    expect(boxes[0]!.querySelector('iframe')?.dataset.previewPaused).toBe('false')
  })

  it('keeps the playing card under the page-level switch and restores it', async () => {
    await renderWall([1, 2])
    await waitFor(() => expect(document.querySelectorAll('iframe')).toHaveLength(2))
    const boxes = previewBoxes()

    fireEvent.click(within(boxes[0]!).getByTitle('播放 卡片 1'))
    await waitFor(() => expect(suspendedStates()).toEqual(['false', 'true']))

    act(() => pauseAllPreviews())
    await waitFor(() => expect(boxes[0]!.querySelector('iframe')?.dataset.previewPaused).toBe('true'))

    // Releasing the switch brings back exactly the card that was playing.
    act(() => resumeAllPreviews())
    await waitFor(() => expect(suspendedStates()).toEqual(['false', 'true']))
    expect(boxes[0]!.querySelector('iframe')?.dataset.previewPaused).toBe('false')
    expect(boxes[1]!.querySelector('iframe')?.dataset.previewPaused).toBe('true')
  })

  it('pauses a card that leaves the viewport instead of restarting it', async () => {
    vi.stubGlobal(
      'IntersectionObserver',
      FakeIntersectionObserver as unknown as typeof IntersectionObserver,
    )
    await renderWall([1, 2])
    const observers = FakeIntersectionObserver.instances
    expect(observers).toHaveLength(2)
    act(() => {
      observers.forEach((observer) => observer.emit(true))
    })
    await waitFor(() => expect(document.querySelectorAll('iframe')).toHaveLength(2))
    const boxes = previewBoxes()
    const activeFrame = boxes[0]!.querySelector('iframe')

    fireEvent.click(within(boxes[0]!).getByTitle('播放 卡片 1'))
    await waitFor(() => expect(suspendedStates()).toEqual(['false', 'true']))

    // The playing card scrolls out of the band: still mounted, now paused.
    act(() => observers[0]!.emit(false))
    await waitFor(() => expect(suspendedStates()[0]).toBe('true'))
    expect(boxes[0]!.querySelector('iframe')).toBe(activeFrame)
    expect(activeFrame?.dataset.previewPaused).toBe('true')
    expect(within(boxes[0]!).getByText('点击播放')).toBeTruthy()

    // An untouched card that leaves the band is still reclaimed.
    act(() => observers[1]!.emit(false))
    await waitFor(() => expect(boxes[1]!.querySelector('iframe')).toBeNull())

    // Scrolling the playing card back resumes the very same frame.
    act(() => observers[0]!.emit(true))
    await waitFor(() => expect(suspendedStates()[0]).toBe('false'))
    expect(boxes[0]!.querySelector('iframe')).toBe(activeFrame)
    expect(activeFrame?.dataset.previewPaused).toBe('false')
  })
})
