import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'

const probe = readFileSync('../mobile/native/app/src/main/assets/native-text-metrics.js', 'utf8')

describe('read-only Web font metrics probe', () => {
  it('reports computed CSS and primary-font half-leading without exposing text or changing DOM', () => {
    document.body.innerHTML = '<section class="chat-empty-state"><h2 style="font-size:26px;line-height:31.2px;font-weight:650;font-family:Geist">不记录这个文本</h2></section>'
    const title = document.querySelector('h2')!
    vi.spyOn(title, 'getBoundingClientRect').mockReturnValue({ x: 30, y: 100, width: 260, height: 31.2, left: 30, right: 290, top: 100, bottom: 131.2, toJSON: () => ({}) })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ font: '', measureText: () => ({ fontBoundingBoxAscent: 26.13, fontBoundingBoxDescent: 7.67, actualBoundingBoxAscent: 20, actualBoundingBoxDescent: 2, width: 180 }) } as never)
    const before = document.body.innerHTML
    const raw = window.eval(probe) as string
    const report = JSON.parse(raw)
    expect(report.rows).toHaveLength(1)
    expect(report.rows[0]).toMatchObject({ fontSize: 26, fontWeight: '650', lineHeight: 31.2 })
    expect(report.rows[0].primary.cssBaseline).toBeCloseTo(24.83)
    expect(raw).not.toContain('不记录这个文本')
    expect(document.body.innerHTML).toBe(before)
  })
})
