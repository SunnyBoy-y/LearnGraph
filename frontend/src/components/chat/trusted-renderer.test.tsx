import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { SandboxArtifact } from '@/components/chat/sandbox-artifact'
import { TrustedComponentRenderer } from '@/components/chat/trusted-component-renderer'

// Keep SandboxArtifact renderable in jsdom without pulling the full Shiki
// pipeline. CodeBlock only mounts when `source_code` is present; the mock makes
// the import cheap regardless.
vi.mock('@/components/ai-elements/code-block', () => ({
  CodeBlock: ({ children }: { children?: ReactNode }) => <div data-testid="code-block">{children}</div>,
  CodeBlockHeader: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  CodeBlockCopyButton: () => <button type="button">复制源码</button>,
}))

const previewHtml = '<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src &#x27;none&#x27;"></head><body>inert</body></html>'

const trustedArtifact = {
  preview_html: previewHtml,
  trusted_renderer_eligible: true,
  trusted_renderer_reason: null,
  trusted_renderer: {
    channel: 'trusted_renderer',
    protocol_version: '1',
    sealed: true,
    token: 'raw-token',
    unlock_message: {
      version: '1',
      event_type: 'renderer.unlock',
      payload: { token: 'raw-token', component_id: 'third_party_card', render_ref: 'data-hash' },
    },
  },
}

const downgradedArtifact = {
  preview_html: previewHtml,
  trusted_renderer_eligible: false,
  trusted_renderer_reason: 'issuer_not_registered',
  trusted_renderer: null,
}

describe('SandboxArtifact trusted-renderer states', () => {
  it('shows the 可信发行者 badge only when the server sealed an eligible envelope', () => {
    render(<SandboxArtifact data={{ ...trustedArtifact, title: '第三方卡片' }} />)
    expect(screen.getByText('可信发行者')).toBeInTheDocument()
    expect(screen.getByTitle('第三方卡片动态沙箱预览')).toBeInTheDocument()
  })

  it('does not show the badge and surfaces the downgrade reason otherwise', () => {
    render(<SandboxArtifact data={{ ...downgradedArtifact, title: '未验证卡片' }} />)
    expect(screen.queryByText('可信发行者')).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('issuer_not_registered')
  })
})

describe('TrustedComponentRenderer third-party delegation', () => {
  it('delegates sandbox_artifact delivery to SandboxArtifact instead of the JSON dump', () => {
    render(
      <TrustedComponentRenderer
        data={{
          component_type: 'third_party_card',
          delivery_mode: 'sandbox_artifact',
          runtime_status: 'rendered',
          props: { title: '第三方卡片' },
          sandbox_artifact: trustedArtifact,
        }}
        fallbackId="part-1"
      />,
    )
    expect(screen.getByText('可信发行者')).toBeInTheDocument()
    expect(screen.queryByText('组件已安全降级')).not.toBeInTheDocument()
  })

  it('keeps built-in declarative components on the native trusted path', () => {
    render(
      <TrustedComponentRenderer
        data={{
          component_type: 'weather_card',
          schema_version: '1.0',
          props: { location: '上海', condition: '晴', temperature_c: 22 },
          allowed_events: [],
        }}
        fallbackId="part-2"
      />,
    )
    expect(screen.getByLabelText('上海 天气')).toBeInTheDocument()
  })
})
