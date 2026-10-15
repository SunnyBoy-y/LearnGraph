import { render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { SandboxArtifact } from '@/components/chat/sandbox-artifact'

const { post, get } = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }))

vi.mock('@/api/client', () => ({
  apiClient: { post, get },
}))

// Keep SandboxArtifact renderable in jsdom without pulling the full Shiki
// pipeline (mirrors trusted-renderer.test.tsx).
vi.mock('@/components/ai-elements/code-block', () => ({
  CodeBlock: ({ children }: { children?: ReactNode }) => <div data-testid="code-block">{children}</div>,
  CodeBlockHeader: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  CodeBlockCopyButton: () => <button type="button">复制源码</button>,
}))

const previewHtml = '<!doctype html><html><head><meta charset="utf-8"></head><body>interactive</body></html>'

describe('SandboxArtifact subapp mode (T2.6)', () => {
  it('instantiates a session and renders the subapp iframe when the marker is present', async () => {
    post.mockResolvedValue({
      session_id: 'sess-1',
      status: 'active',
      state_version: 0,
      state_sha256: null,
      token: 'token-0',
      token_prefix: 'token-0',
      component_id: 'planner',
      render_ref: 'sess-1',
      artifact_version_id: 'av-1',
      chat_session_id: null,
      event_schema: {},
      state_schema: {},
      unlock_message: {
        version: '1',
        event_type: 'renderer.unlock',
        payload: { token: 'token-0', component_id: 'planner', render_ref: 'sess-1' },
      },
    })
    get.mockResolvedValue({ items: [], offset: 0, limit: 1, total: 0 })

    render(
      <SandboxArtifact
        data={{
          title: '旅游规划器',
          artifact_version_id: 'av-1',
          subapp_mode: true,
          preview_html: previewHtml,
        }}
      />,
    )

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith('/subapps/sessions', {
        artifact_version_id: 'av-1',
        chat_session_id: null,
      }),
    )

    expect(screen.getByText('双向交互子应用')).toBeInTheDocument()
    expect(screen.getByTitle('旅游规划器子应用')).toBeInTheDocument()
    // Sandbox boundary is unchanged: scripts only, no same-origin.
    const frame = screen.getByTitle('旅游规划器子应用')
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
  })

  it('keeps the static preview path when no subapp marker is present', () => {
    render(
      <SandboxArtifact
        data={{ title: '静态预览', preview_html: previewHtml }}
      />,
    )
    expect(screen.queryByText('双向交互子应用')).not.toBeInTheDocument()
    expect(screen.getByTitle('静态预览动态沙箱预览')).toBeInTheDocument()
    expect(post).not.toHaveBeenCalled()
  })
})
