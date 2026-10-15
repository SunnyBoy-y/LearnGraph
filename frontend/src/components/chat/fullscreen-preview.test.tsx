import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { FullscreenPreview } from '@/components/chat/fullscreen-preview'
import {
  PreviewPauseScopeContext,
  type PreviewPauseScope,
} from '@/lib/preview-pause-controller'

/**
 * The expand control and the play control are the two ways a user engages with a
 * card from inside it. Both must reach the owning group, otherwise "expand" opens a
 * frozen overlay and "play" looks dead on a wall that keeps every other card paused.
 */

function renderPreview(suspended: boolean) {
  const activate = vi.fn()
  const pin = vi.fn()
  const scope: PreviewPauseScope = { suspended, activate, pin }
  const result = render(
    <PreviewPauseScopeContext.Provider value={scope}>
      <FullscreenPreview label="卡片 1">
        <iframe title="preview" />
      </FullscreenPreview>
    </PreviewPauseScopeContext.Provider>,
  )
  return { ...result, activate, pin }
}

describe('FullscreenPreview playback scope', () => {
  it('claims playback and pins itself while expanded', () => {
    const { activate, pin, container } = renderPreview(true)
    const frame = container.querySelector('iframe')

    // Mounted inside a suspended group: held paused, nothing claimed yet.
    expect(pin).toHaveBeenLastCalledWith(false)
    expect(activate).not.toHaveBeenCalled()
    expect(frame?.dataset.previewPaused).toBe('true')

    fireEvent.click(screen.getByLabelText('放大预览 卡片 1'))
    expect(pin).toHaveBeenLastCalledWith(true)
    expect(activate).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByLabelText('退出 卡片 1 放大预览'))
    expect(pin).toHaveBeenLastCalledWith(false)
  })

  it('starts a paused card through its own play control', () => {
    const { activate } = renderPreview(true)

    fireEvent.click(screen.getByLabelText('继续播放 卡片 1'))

    expect(activate).toHaveBeenCalledTimes(1)
  })

  it('pauses a running card without claiming playback for it', () => {
    const { activate, container } = renderPreview(false)
    const frame = container.querySelector('iframe')
    // Never posted a pause: an unsuspended preview simply runs.
    expect(frame?.dataset.previewPaused).toBeUndefined()

    fireEvent.click(screen.getByLabelText('暂停播放 卡片 1'))

    expect(frame?.dataset.previewPaused).toBe('true')
    expect(screen.getByLabelText('继续播放 卡片 1')).toBeTruthy()
    expect(activate).not.toHaveBeenCalled()
  })
})
