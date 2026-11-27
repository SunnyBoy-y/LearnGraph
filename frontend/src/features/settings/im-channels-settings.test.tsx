import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import QRCode from 'qrcode'
import { ApiError, apiClient } from '@/api/client'
import { IMChannelsSettings } from './im-channels-settings'

vi.mock('qrcode', () => ({
  default: { toDataURL: vi.fn(async () => 'data:image/png;base64,ZmFrZQ==') },
}))

vi.mock('@/lib/notifications', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('@/features/auth/auth-context-value', () => ({
  useAuth: () => ({
    workspaceId: 'ws-personal-1',
    workspaceName: 'Personal',
    authenticated: true,
  }),
}))

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <IMChannelsSettings />
    </QueryClientProvider>,
  )
}

describe('IMChannelsSettings', () => {
  beforeEach(() => {
    vi.mocked(QRCode.toDataURL).mockResolvedValue('data:image/png;base64,ZmFrZQ==')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('starts Feishu QR login, renders QR code, and supports manual config toggle', async () => {
    const getSpy = vi.spyOn(apiClient, 'get').mockImplementation(async path => {
      if (path === '/im/feishu') {
        return {
          configured: false,
          personal_workspace: true,
          bound: false,
          session_id: null,
          config_source: null,
          app_id: null,
        }
      }
      if (path === '/im/wechat') {
        return {
          configured: false,
          personal_workspace: true,
          bound: false,
          session_id: null,
          bot_id: null,
          session_expired: false,
          last_error: null,
        }
      }
      if (path === '/im/feishu/qr/fs-sid-1') {
        return { status: 'pending', interval: 5 }
      }
      throw new Error(`unexpected GET ${path}`)
    })

    const postSpy = vi.spyOn(apiClient, 'post').mockImplementation(async path => {
      if (path === '/im/feishu/qr') {
        return {
          session_id: 'fs-sid-1',
          qr_url: 'https://open.feishu.cn/page/launcher?user_code=CODE-1',
          user_code: 'CODE-1',
          expires_in: 600,
          interval: 5,
        }
      }
      throw new Error(`unexpected POST ${path}`)
    })

    const deleteSpy = vi.spyOn(apiClient, 'delete').mockResolvedValue({ cancelled: true })

    renderPanel()
    const user = userEvent.setup()

    const qrBtn = await screen.findByRole('button', { name: /扫码一键接入/ })
    await user.click(qrBtn)

    expect(await screen.findByRole('img', { name: '飞书扫码一键接入二维码' })).toBeInTheDocument()
    expect(screen.getByText('CODE-1')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /在浏览器中打开授权页/ })).toHaveAttribute(
      'href',
      'https://open.feishu.cn/page/launcher?user_code=CODE-1',
    )
    expect(postSpy).toHaveBeenCalledWith('/im/feishu/qr', { domain: 'feishu' }, expect.anything())

    await user.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledWith('/im/feishu/qr/fs-sid-1', expect.anything())
    })

    await user.click(await screen.findByRole('button', { name: /手动配置 App ID \/ Secret/ }))
    expect(screen.getByLabelText('App ID')).toBeInTheDocument()
    expect(screen.getByLabelText('App Secret')).toBeInTheDocument()
    expect(getSpy).toHaveBeenCalledWith('/im/feishu', expect.anything())
  })

  it('starts WeChat QR login and opens viewable tutorials for both channels', async () => {
    vi.spyOn(apiClient, 'get').mockImplementation(async path => {
      if (path === '/im/feishu') {
        return {
          configured: false,
          personal_workspace: true,
          bound: false,
          session_id: null,
          config_source: null,
          app_id: null,
        }
      }
      if (path === '/im/wechat') {
        return {
          configured: false,
          personal_workspace: true,
          bound: false,
          session_id: null,
          bot_id: null,
          session_expired: false,
          last_error: null,
        }
      }
      throw new Error(`unexpected GET ${path}`)
    })

    vi.spyOn(apiClient, 'post').mockImplementation(async path => {
      if (path === '/im/wechat/qr') {
        return {
          session_id: 'wx-sid-1',
          qr_url: 'https://liteapp.weixin.qq.com/q/sample',
          expires_in: 300,
        }
      }
      throw new Error(`unexpected POST ${path}`)
    })
    vi.spyOn(apiClient, 'delete').mockResolvedValue({ cancelled: true })

    renderPanel()
    const user = userEvent.setup()

    const wechatQrBtn = await screen.findByRole('button', { name: /扫码登录微信/ })
    await user.click(wechatQrBtn)

    expect(await screen.findByRole('img', { name: '微信 ClawBot 扫码登录二维码' })).toBeInTheDocument()
    expect(screen.getByText(/请打开手机微信「扫一扫」扫描二维码/)).toBeInTheDocument()

    // Open WeChat tutorial dialog directly on WeChat tab
    await user.click(screen.getByRole('button', { name: /查看微信接入教程/ }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('消息渠道接入教程')).toBeInTheDocument()
    expect(screen.getByText('扫码授权接入步骤')).toBeInTheDocument()

    // Switch to Feishu tutorial tab inside dialog
    await user.click(screen.getByRole('tab', { name: '飞书接入教程' }))
    expect(await screen.findByText('方式一：扫码一键接入（推荐）')).toBeInTheDocument()
    expect(screen.getByText('方式二：手动配置企业自建应用')).toBeInTheDocument()
  })

  it('shows expired state and disconnect action for configured WeChat channel', async () => {
    vi.spyOn(apiClient, 'get').mockImplementation(async path => {
      if (path === '/im/feishu') {
        return {
          configured: false,
          personal_workspace: true,
          bound: false,
          session_id: null,
          config_source: null,
          app_id: null,
        }
      }
      if (path === '/im/wechat') {
        return {
          configured: true,
          personal_workspace: true,
          bound: true,
          session_id: 'sess-1',
          bot_id: 'bot_demo@im.bot',
          session_expired: true,
          last_error: '微信登录态已失效，请重新扫码登录。',
        }
      }
      throw new Error(`unexpected GET ${path}`)
    })
    const deleteSpy = vi.spyOn(apiClient, 'delete').mockResolvedValue({
      configured: false,
      personal_workspace: true,
      bound: false,
      session_id: null,
      bot_id: null,
      session_expired: false,
      last_error: null,
    })

    renderPanel()
    const user = userEvent.setup()

    expect(await screen.findByText('登录已失效')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /重新扫码登录/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '退出登录' }))
    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledWith('/im/wechat/config', expect.anything())
    })
  })

  it('shows specific Feishu QR error message when Feishu service is unavailable', async () => {
    vi.spyOn(apiClient, 'get').mockImplementation(async path => {
      if (path === '/im/feishu') {
        return {
          configured: false,
          personal_workspace: true,
          bound: false,
          session_id: null,
          config_source: null,
          app_id: null,
        }
      }
      if (path === '/im/wechat') {
        return {
          configured: false,
          personal_workspace: true,
          bound: false,
          session_id: null,
          bot_id: null,
          session_expired: false,
          last_error: null,
        }
      }
      throw new Error(`unexpected GET ${path}`)
    })

    vi.spyOn(apiClient, 'post').mockRejectedValueOnce(
      new ApiError({
        status: 503,
        code: 'feishu_unavailable',
        message: 'Unable to reach Feishu registration service',
        method: 'POST',
        url: '/api/v1/im/feishu/qr',
      }),
    )

    renderPanel()
    const user = userEvent.setup()

    await user.click(await screen.findByRole('button', { name: /扫码一键接入/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('无法连接飞书授权服务，请检查网络后重试或点击「手动配置」。')
  })
})
