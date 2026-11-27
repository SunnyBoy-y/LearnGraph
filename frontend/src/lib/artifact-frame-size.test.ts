import { describe, expect, it } from 'vitest'

import {
  artifactAutoSizeStyle,
  artifactSizeShim,
  readArtifactFrameSize,
  resolveArtifactFramePolicy,
} from './artifact-frame-size'
import { sandboxedHtmlPreviewDocument } from './sandboxed-html-preview'

describe('artifact frame sizing', () => {
  it('accepts bounded sizes only from the current opaque-origin frame', () => {
    const frame = document.createElement('iframe')
    document.body.append(frame)
    Object.defineProperty(frame, 'clientWidth', { value: 500 })
    const event = (data: unknown, source = frame.contentWindow, origin = 'null') =>
      new MessageEvent('message', { data, source, origin })
    expect(readArtifactFrameSize(event({ kind: 'lg.artifact-size', width: 500, height: 15000 }), frame)).toBe(8192)
    expect(readArtifactFrameSize(event({ kind: 'lg.artifact-size', width: 500, height: 15000 }), frame, { maxHeight: 560 })).toBe(560)
    expect(readArtifactFrameSize(event({ kind: 'lg.artifact-size', width: 500, height: 200 }, window), frame)).toBeNull()
    expect(readArtifactFrameSize(event({ kind: 'lg.artifact-size', width: 500, height: 200 }, frame.contentWindow, 'https://example.com'), frame)).toBeNull()
    expect(readArtifactFrameSize(event({ kind: 'lg.artifact-size', width: 400, height: 200 }), frame)).toBeNull()
    expect(readArtifactFrameSize(event({ kind: 'lg.artifact-size', width: 500, height: NaN }), frame)).toBeNull()
    frame.remove()
  })

  it('injects the sizing channel and auto-size override style only for explicitly measured executable previews', () => {
    const measured = sandboxedHtmlPreviewDocument('<style>body{min-height:100vh}</style><p>hello</p>', { measureArtifact: true })
    expect(measured).toContain('lg.artifact-size')
    expect(measured).toContain('data-lg-auto-size')
    expect(measured.indexOf('data-lg-auto-size')).toBeGreaterThan(measured.indexOf('min-height:100vh'))
    expect(artifactAutoSizeStyle).toContain('min-height:0!important')
    // Must not observe attributes on subtree, which would starve during 60fps SVG/DOM animations.
    expect(artifactSizeShim).not.toContain('attributes:true')

    expect(sandboxedHtmlPreviewDocument('<p>hello</p>', { scripts: false, measureArtifact: true })).not.toContain('lg.artifact-size')
    expect(sandboxedHtmlPreviewDocument('<p>hello</p>')).not.toContain('lg.artifact-size')
  })

  it('resolves unified HeightMode policies with auto default for interactive HTML', () => {
    // Legacy preferred_height + viewport.max_height still default to auto without clipping at 720px.
    const legacy = resolveArtifactFramePolicy({
      preferred_height: 480,
      viewport: { mode: 'inline', preferred_height: 480, max_height: 720 },
    })
    expect(legacy).toMatchObject({ mode: 'auto', initialHeight: 480, maxHeight: 8192 })

    const bounded = resolveArtifactFramePolicy({ height_mode: 'bounded', max_height: 420, preferred_height: 600 })
    expect(bounded).toMatchObject({ mode: 'bounded', initialHeight: 420, maxHeight: 420 })

    const fixed = resolveArtifactFramePolicy({ height_mode: 'fixed', preferred_height: 500 })
    expect(fixed).toMatchObject({ mode: 'fixed', initialHeight: 500, minHeight: 500, maxHeight: 500 })

    const ratio = resolveArtifactFramePolicy({ height_mode: 'aspect-ratio', aspect_ratio: '4 / 3' })
    expect(ratio).toMatchObject({ mode: 'aspect-ratio', aspectRatio: '4 / 3' })
  })
})
