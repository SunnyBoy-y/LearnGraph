import { describe, expect, it } from 'vitest'

import {
  rewriteAllCitations,
  rewriteWebCitations,
  parseWebCitationHref,
} from '@/lib/document-citations'

/**
 * 网页引用角标改写：覆盖 DashScope 原生联网（enable_search +
 * enable_citation）在正文中直接插入的 [ref_N] 角标，以及
 * （网页引用：N）/ [N] 两种既有标记。
 */
describe('rewriteWebCitations', () => {
  const known = new Set([1, 5])

  it('rewrites DashScope [ref_N] markers into citation links', () => {
    const text = '杭州明天阴转多云，最高 28℃[ref_5]。'
    const { markdown, citations } = rewriteWebCitations(text, known)
    expect(markdown).toContain('[5](/__lgwebcite__/5)')
    expect(markdown).not.toContain('[ref_5]')
    expect(citations).toEqual([{ index: 5, raw: '[ref_5]', known: true }])
  })

  it('keeps [ref_N] untouched when no source list exists', () => {
    const text = '杭州明天阴转多云，最高 28℃[ref_5]。'
    const { markdown, citations } = rewriteWebCitations(text)
    expect(markdown).toBe(text)
    expect(citations).toEqual([])
  })

  it('marks unknown [ref_N] as missing when a source list exists', () => {
    const text = '详见[ref_9]。'
    const { markdown, citations } = rewriteWebCitations(text, known)
    expect(markdown).toContain('[9](/__lgwebcite__/9?missing=1)')
    expect(citations).toEqual([{ index: 9, raw: '[ref_9]', known: false }])
  })

  it('still rewrites （网页引用：N） and bare [N] markers', () => {
    const text = 'A（网页引用：1）B[5]C'
    const { markdown } = rewriteWebCitations(text, known)
    expect(markdown).toContain('[1](/__lgwebcite__/1)')
    expect(markdown).toContain('[5](/__lgwebcite__/5)')
  })

  it('does not rewrite markdown link syntax like [5](url)', () => {
    const text = '参考 [5](https://example.com)'
    const { markdown } = rewriteWebCitations(text, known)
    expect(markdown).toBe(text)
  })

  it('groups only adjacent references and preserves every original index', () => {
    const { markdown } = rewriteWebCitations('结论[1][5]。另一句[1]，另一个来源[5]。', known)
    expect(markdown).toBe('结论[1,5](/__lgwebcite__/1?refs=1,5)。另一句[1](/__lgwebcite__/1)，另一个来源[5](/__lgwebcite__/5)。')
    expect(parseWebCitationHref('/__lgwebcite__/1?refs=1,5')?.indexes).toEqual([1, 5])
  })

  it('does not combine different lines or unresolved sources', () => {
    const { markdown } = rewriteWebCitations('[1]\n[5] 以及 [1][9]', known)
    expect(markdown).not.toContain('refs=')
    expect(markdown).toContain('/9?missing=1')
  })

  it('deduplicates a repeated reference within one evidence group', () => {
    expect(rewriteWebCitations('结论[1][1][5]', known).markdown)
      .toBe('结论[1,5](/__lgwebcite__/1?refs=1,5)')
  })

  it('leaves code, real links, images and defined reference links intact', () => {
    const text = '`array[1]`\n\n```js\narray[5]\n```\n\n[标题 [1]](https://example.com) ![图 [5]](image.png)\n\n[1][ref]\n\n[ref]: https://example.com'
    expect(rewriteWebCitations(text, known).markdown).toBe(text)
  })

  it('keeps streaming partial markers as text until they are complete', () => {
    expect(rewriteWebCitations('结论[ref_', known).markdown).toBe('结论[ref_')
    expect(rewriteWebCitations('结论[ref_1][ref_5]', known).markdown)
      .toContain('/1?refs=1,5')
  })

  it('keeps escaped literal citation-like text intact', () => {
    expect(rewriteWebCitations('保留 \\[1] 和 \\[ref_5]', known).markdown)
      .toBe('保留 \\[1] 和 \\[ref_5]')
  })
})

describe('rewriteAllCitations with [ref_N]', () => {
  it('rewrites [ref_N] after document citation pass', () => {
    const text = '结论[ref_3]。'
    const result = rewriteAllCitations(text, [3])
    expect(result.markdown).toContain('/__lgwebcite__/3')
    expect(result.webCitations).toEqual([
      { index: 3, raw: '[ref_3]', known: true },
    ])
  })
})
