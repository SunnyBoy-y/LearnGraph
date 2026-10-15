import { fireEvent, render, screen } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  PreviewPauseScopeContext,
  createPreviewPauseController,
  pauseAllPreviews,
  resumeAllPreviews,
  usePreviewPause,
  type PreviewPauseController,
  type PreviewPauseScope,
} from './preview-pause-controller'

/**
 * A preview group (the artifact card wall) and the page-level switch each own a
 * channel. These tests pin the separation: releasing one channel must never start
 * a preview another channel still holds, which is what used to make the wall and
 * the top-bar switch fight each other.
 */

const controllers: PreviewPauseController[] = []

function mountPreview(): { root: HTMLDivElement; frame: HTMLIFrameElement } {
  const root = document.createElement('div')
  const frame = document.createElement('iframe')
  root.appendChild(frame)
  document.body.appendChild(root)
  return { root, frame }
}

function createController(root: HTMLElement): PreviewPauseController {
  const controller = createPreviewPauseController(root)
  controllers.push(controller)
  return controller
}

function Preview() {
  const rootRef = useRef<HTMLDivElement>(null)
  const { paused, pause, resume } = usePreviewPause(rootRef)
  return (
    <div ref={rootRef}>
      <iframe title="preview" />
      <span data-testid="state">{paused ? 'paused' : 'running'}</span>
      <button onClick={pause} type="button">
        pause
      </button>
      <button onClick={resume} type="button">
        resume
      </button>
    </div>
  )
}

function Harness({ scope }: { scope: PreviewPauseScope | null }) {
  // The consumer must sit *inside* the provider: a preview reads its group
  // through context, exactly as a card does on the artifact wall.
  return scope ? (
    <PreviewPauseScopeContext.Provider value={scope}>
      <Preview />
    </PreviewPauseScopeContext.Provider>
  ) : (
    <Preview />
  )
}

beforeEach(() => {
  resumeAllPreviews()
})

afterEach(() => {
  controllers.splice(0).forEach((controller) => controller.destroy())
  resumeAllPreviews()
  document.body.innerHTML = ''
})

describe('preview pause channels', () => {
  it('stays paused while any channel still holds the preview', () => {
    const { root, frame } = mountPreview()
    const controller = createController(root)
    expect(controller.paused).toBe(false)

    controller.suspend('scope')
    expect(controller.paused).toBe(true)
    expect(frame.dataset.previewPaused).toBe('true')

    controller.suspend('manual')
    controller.release('scope')
    expect(controller.paused).toBe(true)

    controller.release('manual')
    expect(controller.paused).toBe(false)
    expect(frame.dataset.previewPaused).toBe('false')
  })

  it('cannot be started by the page-level switch while its group holds it', () => {
    const { root, frame } = mountPreview()
    const controller = createController(root)
    controller.suspend('scope')

    pauseAllPreviews()
    resumeAllPreviews()

    expect(controller.paused).toBe(true)
    expect(frame.dataset.previewPaused).toBe('true')
  })

  it('keeps holding previews that were already paused for another reason', () => {
    const { root, frame } = mountPreview()
    const controller = createController(root)
    controller.suspend('scope')

    pauseAllPreviews()
    // The group lets go, but the page-level switch is still on.
    controller.release('scope')
    expect(controller.paused).toBe(true)

    resumeAllPreviews()
    expect(controller.paused).toBe(false)
    expect(frame.dataset.previewPaused).toBe('false')
  })

  it('starts a preview mounted while the page-level switch is on', () => {
    pauseAllPreviews()
    const { root } = mountPreview()
    const controller = createController(root)
    expect(controller.paused).toBe(true)

    resumeAllPreviews()
    expect(controller.paused).toBe(false)
  })
})

describe('usePreviewPause with a playback group', () => {
  it('runs a preview that belongs to no group', () => {
    const { container } = render(<Harness scope={null} />)
    expect(screen.getByTestId('state')).toHaveTextContent('running')
    expect(container.querySelector('iframe')?.dataset.previewPaused).toBeUndefined()
  })

  it('mounts a preview inside a suspended group already paused', () => {
    const scope: PreviewPauseScope = { suspended: true, activate: vi.fn(), pin: vi.fn() }
    const { container } = render(<Harness scope={scope} />)
    expect(screen.getByTestId('state')).toHaveTextContent('paused')
    expect(container.querySelector('iframe')?.dataset.previewPaused).toBe('true')
  })

  it('follows the group suspension and keeps manual control working', () => {
    const scope: PreviewPauseScope = { suspended: true, activate: vi.fn(), pin: vi.fn() }
    const { container, rerender } = render(<Harness scope={scope} />)

    rerender(<Harness scope={{ ...scope, suspended: false }} />)
    expect(screen.getByTestId('state')).toHaveTextContent('running')
    expect(container.querySelector('iframe')?.dataset.previewPaused).toBe('false')

    fireEvent.click(screen.getByText('pause'))
    expect(screen.getByTestId('state')).toHaveTextContent('paused')
    expect(container.querySelector('iframe')?.dataset.previewPaused).toBe('true')

    fireEvent.click(screen.getByText('resume'))
    expect(screen.getByTestId('state')).toHaveTextContent('running')

    rerender(<Harness scope={{ ...scope, suspended: true }} />)
    expect(screen.getByTestId('state')).toHaveTextContent('paused')
  })
})
