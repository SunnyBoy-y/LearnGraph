import { describe, expect, it, vi } from 'vitest'

import {
  postRendererUnlock,
  rendererUnlockMessage,
  trustedRendererEligible,
  trustedRendererEnvelope,
  trustedRendererReason,
} from './trusted-renderer'

const eligibleData = {
  preview_html: '<html></html>',
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
      payload: { token: 'raw-token', component_id: 'card', render_ref: 'data-hash' },
    },
  },
}

describe('trusted renderer consumer helpers', () => {
  it('requires both the eligibility flag and the sealed envelope', () => {
    expect(trustedRendererEligible(eligibleData)).toBe(true)

    // Flag without envelope is never trusted.
    expect(trustedRendererEligible({ ...eligibleData, trusted_renderer: null })).toBe(false)
    expect(trustedRendererEligible({ ...eligibleData, trusted_renderer: undefined })).toBe(false)
    // Envelope without the flag is not trusted either.
    expect(
      trustedRendererEligible({ ...eligibleData, trusted_renderer_eligible: false }),
    ).toBe(false)
  })

  it('extracts the sealed envelope and the server-provided reason', () => {
    expect(trustedRendererEnvelope(eligibleData)?.channel).toBe('trusted_renderer')
    expect(trustedRendererReason(eligibleData)).toBe('')
    expect(trustedRendererReason({ ...eligibleData, trusted_renderer_reason: 'issuer_revoked' })).toBe(
      'issuer_revoked',
    )
    expect(trustedRendererReason({})).toBe('')
  })

  it('returns the unlock handshake only when fully eligible', () => {
    const message = rendererUnlockMessage(eligibleData)
    expect(message).not.toBeNull()
    expect(message?.event_type).toBe('renderer.unlock')
    expect(message?.payload).toMatchObject({ token: 'raw-token', component_id: 'card' })
    expect(rendererUnlockMessage({ ...eligibleData, trusted_renderer: null })).toBeNull()
  })

  it('posts the unlock message to the inert iframe with targetOrigin *', () => {
    const postMessage = vi.fn()
    const iframe = { contentWindow: { postMessage } } as unknown as HTMLIFrameElement
    const message = rendererUnlockMessage(eligibleData)

    postRendererUnlock(iframe, message)
    expect(postMessage).toHaveBeenCalledWith(message, '*')

    // No-ops: missing iframe or missing message.
    postRendererUnlock(null, message)
    postRendererUnlock(iframe, null)
    expect(postMessage).toHaveBeenCalledTimes(1)
  })

  it('swallows post errors from an inert/no-receiver iframe', () => {
    const throwing = {
      contentWindow: {
        postMessage: () => {
          throw new Error('no receiver')
        },
      },
    } as unknown as HTMLIFrameElement
    expect(() => postRendererUnlock(throwing, { event_type: 'renderer.unlock' })).not.toThrow()
  })
})
