import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Copy,
  ExternalLink,
  KeyRound,
  Loader2,
  QrCode,
  RefreshCw,
} from 'lucide-react'
import QRCode from 'qrcode'
import { ApiError, apiClient } from '@/api/client'
import { adaptivePollInterval } from '@/lib/network-quality'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useAuth } from '@/features/auth/auth-context-value'
import { toast } from '@/lib/notifications'
import { IMChannelTutorialDialog, type IMTutorialChannel } from './im-channel-tutorial-dialog'
import { SettingsSection } from './settings-primitives'

const feishuEndpoint = '/im/feishu'
const wechatEndpoint = '/im/wechat'

type FeishuStatus = {
  configured: boolean
  personal_workspace: boolean
  bound: boolean
  session_id: string | null
  config_source: 'user' | 'env' | null
  app_id: string | null
}

type FeishuQrStartResponse = {
  session_id: string
  qr_url: string
  user_code: string
  expires_in: number
  interval: number
}

type FeishuQrPollResponse = {
  status: 'pending' | 'confirmed' | 'denied' | 'expired' | 'error'
  interval?: number
  message?: string
  channel_status?: FeishuStatus
}

type WeChatStatus = {
  configured: boolean
  personal_workspace: boolean
  bound: boolean
  session_id: string | null
  bot_id: string | null
  session_expired: boolean
  last_error: string | null
}

type WeChatQrStartResponse = {
  session_id: string
  qr_url: string
  expires_in: number
}

type WeChatQrPollResponse = {
  status: 'wait' | 'scanned' | 'confirmed' | 'expired' | 'error'
  message?: string
  channel_status?: WeChatStatus
}

function QrLoginBox({
  qrUrl,
  alt,
  statusText,
  statusTone,
  userCode,
  openUrl,
  busy,
  onRefresh,
  onCancel,
}: {
  qrUrl: string
  alt: string
  statusText: string
  statusTone: 'waiting' | 'scanned' | 'expired' | 'error'
  userCode?: string
  openUrl?: string
  busy: boolean
  onRefresh: () => void
  onCancel: () => void
}) {
  const [image, setImage] = useState<{ target: string; url: string; failed?: boolean }>()

  useEffect(() => {
    if (!qrUrl) return
    let cancelled = false
    QRCode.toDataURL(qrUrl, { width: 320, margin: 1, errorCorrectionLevel: 'M' })
      .then(url => {
        if (!cancelled) setImage({ target: qrUrl, url })
      })
      .catch(() => {
        if (!cancelled) setImage({ target: qrUrl, url: '', failed: true })
      })
    return () => {
      cancelled = true
    }
  }, [qrUrl])

  const inactive = statusTone === 'expired' || statusTone === 'error'

  return (
    <div className="w-full max-w-md rounded-xl border border-border/70 bg-muted/20 p-4">
      <div className="flex flex-col items-center gap-3 text-center">
        <div className={`rounded-xl border border-border/60 bg-white p-2.5 shadow-xs ${inactive ? 'opacity-45' : ''}`}>
          {image?.target === qrUrl && image.url ? (
            <img alt={alt} src={image.url} width={176} height={176} className="block size-44 rounded-md" />
          ) : (
            <div className="flex size-44 items-center justify-center text-xs text-neutral-500">
              {image?.target === qrUrl && image.failed ? '二维码生成失败' : '正在生成二维码…'}
            </div>
          )}
        </div>

        <div
          role="status"
          className={`flex items-center justify-center gap-1.5 text-xs font-medium ${
            statusTone === 'error' || statusTone === 'expired'
              ? 'text-destructive'
              : statusTone === 'scanned'
                ? 'text-primary'
                : 'text-foreground'
          }`}
        >
          {statusTone === 'waiting' || statusTone === 'scanned' ? (
            <Loader2 className="size-3.5 shrink-0 animate-spin" />
          ) : (
            <AlertTriangle className="size-3.5 shrink-0" />
          )}
          <span>{statusText}</span>
        </div>

        {userCode ? (
          <p className="text-xs text-muted-foreground">
            确认码：<code className="rounded bg-muted px-1.5 py-0.5 font-mono text-foreground">{userCode}</code>
          </p>
        ) : null}

        {openUrl && !inactive ? (
          <a
            href={openUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs text-primary underline-offset-4 hover:underline"
          >
            无法扫码？在浏览器中打开授权页
            <ExternalLink className="size-3" />
          </a>
        ) : null}

        <div className="flex flex-wrap items-center justify-center gap-2 pt-1">
          <Button type="button" variant="outline" size="sm" disabled={busy} onClick={onRefresh}>
            <RefreshCw className="size-3.5" />
            刷新二维码
          </Button>
          <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={onCancel}>
            取消
          </Button>
        </div>
      </div>
    </div>
  )
}

export function IMChannelsSettings() {
  const { workspaceId } = useAuth()
  const [tutorialOpen, setTutorialOpen] = useState(false)
  const [tutorialTab, setTutorialTab] = useState<IMTutorialChannel>('feishu')

  function openTutorial(channel: IMTutorialChannel) {
    setTutorialTab(channel)
    setTutorialOpen(true)
  }

  return (
    <>
      <FeishuChannelSection key={`feishu-${workspaceId}`} workspaceId={workspaceId} onOpenTutorial={() => openTutorial('feishu')} />
      <WeChatChannelSection key={`wechat-${workspaceId}`} workspaceId={workspaceId} onOpenTutorial={() => openTutorial('wechat')} />
      <IMChannelTutorialDialog open={tutorialOpen} initialTab={tutorialTab} onOpenChange={setTutorialOpen} />
    </>
  )
}

function FeishuChannelSection({
  workspaceId,
  onOpenTutorial,
}: {
  workspaceId: string
  onOpenTutorial: () => void
}) {
  const queryClient = useQueryClient()
  const [command, setCommand] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [editingConfig, setEditingConfig] = useState(false)
  const [appId, setAppId] = useState('')
  const [appSecret, setAppSecret] = useState('')
  const [qrSession, setQrSession] = useState<{
    sessionId: string
    qrUrl: string
    userCode: string
    interval: number
    status: 'pending' | 'denied' | 'expired' | 'error'
    errorText: string
  } | null>(null)
  const activeQrSessionIdRef = useRef<string | null>(null)

  const options = { headers: { 'X-Workspace-ID': workspaceId } }
  const status = useQuery({
    queryKey: ['im-feishu', workspaceId],
    queryFn: () => apiClient.get<FeishuStatus>(feishuEndpoint, options),
    refetchInterval: () => adaptivePollInterval(command ? 3000 : false),
  })

  useEffect(() => {
    activeQrSessionIdRef.current = qrSession?.sessionId ?? null
  }, [qrSession?.sessionId])

  useEffect(() => {
    return () => {
      const sid = activeQrSessionIdRef.current
      if (sid) {
        void apiClient.delete(`${feishuEndpoint}/qr/${encodeURIComponent(sid)}`, options).catch(() => {})
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId])

  useEffect(() => {
    if (!qrSession || qrSession.status !== 'pending') return
    let cancelled = false
    const timer = window.setTimeout(async () => {
      try {
        const res = await apiClient.get<FeishuQrPollResponse>(
          `${feishuEndpoint}/qr/${encodeURIComponent(qrSession.sessionId)}`,
          options,
        )
        if (cancelled) return
        if (res.status === 'confirmed') {
          setQrSession(null)
          setEditingConfig(false)
          setError('')
          if (res.channel_status) {
            queryClient.setQueryData(['im-feishu', workspaceId], res.channel_status)
          } else {
            await status.refetch()
          }
          toast.success(
            res.channel_status?.bound
              ? '飞书已扫码接入并自动绑定'
              : '飞书应用已创建，请生成绑定码完成账号绑定',
          )
          return
        }
        if (res.status === 'pending') {
          const nextInterval = typeof res.interval === 'number' && res.interval >= 2 ? res.interval : qrSession.interval
          setQrSession(prev => (prev && prev.sessionId === qrSession.sessionId ? { ...prev, interval: nextInterval } : prev))
          return
        }
        if (res.status === 'denied') {
          setQrSession(prev =>
            prev && prev.sessionId === qrSession.sessionId
              ? { ...prev, status: 'denied', errorText: '你已取消飞书授权，可刷新二维码重试。' }
              : prev,
          )
          return
        }
        if (res.status === 'expired') {
          setQrSession(prev =>
            prev && prev.sessionId === qrSession.sessionId
              ? { ...prev, status: 'expired', errorText: '二维码已过期，请点击刷新重新获取。' }
              : prev,
          )
          return
        }
        setQrSession(prev =>
          prev && prev.sessionId === qrSession.sessionId
            ? { ...prev, status: 'error', errorText: res.message || '飞书授权失败，请刷新重试或改用手动配置。' }
            : prev,
        )
      } catch (err) {
        if (cancelled) return
        const msg = err instanceof Error ? err.message : ''
        if (msg.includes('already configured')) {
          setQrSession(prev =>
            prev && prev.sessionId === qrSession.sessionId
              ? { ...prev, status: 'error', errorText: '该飞书应用已被其他用户配置，请使用你自己的飞书应用。' }
              : prev,
          )
          return
        }
        // Keep polling on transient network blips.
        setQrSession(prev => (prev && prev.sessionId === qrSession.sessionId ? { ...prev } : prev))
      }
    }, Math.max(2, qrSession.interval) * 1000)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qrSession, workspaceId])

  async function startQrLogin() {
    setBusy(true)
    setError('')
    setCommand('')
    setEditingConfig(false)
    if (qrSession?.sessionId) {
      void apiClient.delete(`${feishuEndpoint}/qr/${encodeURIComponent(qrSession.sessionId)}`, options).catch(() => {})
    }
    try {
      const res = await apiClient.post<FeishuQrStartResponse, { domain: string }>(
        `${feishuEndpoint}/qr`,
        { domain: 'feishu' },
        options,
      )
      setQrSession({
        sessionId: res.session_id,
        qrUrl: res.qr_url,
        userCode: res.user_code,
        interval: res.interval || 5,
        status: 'pending',
        errorText: '',
      })
    } catch (err) {
      setQrSession(null)
      if (err instanceof ApiError) {
        if (err.code === 'im_personal_workspace_required') {
          setError('请切换到你自己的个人工作区再扫码接入飞书。')
        } else if (err.code === 'durable_queue_disabled') {
          setError('后台任务队列未启用，暂无法使用飞书扫码接入，请先检查服务端配置。')
        } else if (err.code === 'feishu_unavailable') {
          setError('无法连接飞书授权服务，请检查网络后重试或点击「手动配置」。')
        } else if (err.code === 'feishu_qr_unsupported' || err.code === 'feishu_qr_failed') {
          setError('飞书开放平台暂未返回有效的扫码授权码，请稍后重试或点击「手动配置」。')
        } else {
          setError('获取飞书扫码二维码失败，请稍后重试或点击「手动配置」。')
        }
      } else {
        setError('获取飞书扫码二维码失败，请稍后重试或点击「手动配置」。')
      }
    } finally {
      setBusy(false)
    }
  }

  function cancelQrLogin() {
    const sid = qrSession?.sessionId
    setQrSession(null)
    if (sid) {
      void apiClient.delete(`${feishuEndpoint}/qr/${encodeURIComponent(sid)}`, options).catch(() => {})
    }
  }

  function openConfigEditor(prefillAppId: string | null) {
    cancelQrLogin()
    setAppId(prefillAppId ?? '')
    setAppSecret('')
    setError('')
    setEditingConfig(true)
  }

  async function saveConfig(event: FormEvent) {
    event.preventDefault()
    const trimmedId = appId.trim()
    const trimmedSecret = appSecret.trim()
    if (!trimmedId) {
      setError('请输入飞书 App ID。')
      return
    }
    if (!status.data?.configured && !trimmedSecret) {
      setError('请输入飞书 App Secret。')
      return
    }
    setBusy(true)
    setError('')
    setCommand('')
    try {
      await apiClient.put<FeishuStatus, { app_id: string; app_secret?: string }>(
        `${feishuEndpoint}/config`,
        {
          app_id: trimmedId,
          ...(trimmedSecret ? { app_secret: trimmedSecret } : {}),
        },
        options,
      )
      setEditingConfig(false)
      setAppSecret('')
      await status.refetch()
      toast.success('飞书配置已保存')
    } catch (err) {
      const msg = err instanceof Error ? err.message : ''
      if (msg.includes('already configured')) {
        setError('该飞书 App ID 已被其他用户配置，请使用你自己的飞书应用。')
      } else {
        setError('保存飞书配置失败，请检查 App ID 与 App Secret 格式后重试。')
      }
    } finally {
      setBusy(false)
    }
  }

  async function removeConfig() {
    cancelQrLogin()
    setBusy(true)
    setError('')
    setCommand('')
    try {
      await apiClient.delete(`${feishuEndpoint}/config`, options)
      setEditingConfig(false)
      setAppId('')
      setAppSecret('')
      await status.refetch()
      toast.success('已移除飞书配置')
    } catch {
      setError('移除配置失败，请重试。')
    } finally {
      setBusy(false)
    }
  }

  async function act(unbind: boolean) {
    setBusy(true)
    setError('')
    setCommand('')
    try {
      if (unbind) {
        await apiClient.delete(`${feishuEndpoint}/binding`, options)
        await status.refetch()
        toast.success('已解除飞书绑定')
      } else {
        const { code } = await apiClient.post<{ code: string }, Record<string, never>>(
          `${feishuEndpoint}/binding-code`,
          {},
          options,
        )
        setCommand(`/bind ${code}`)
      }
    } catch {
      setError(unbind ? '解绑失败，请重试。' : '无法生成绑定码，请检查 Bot 配置或当前工作区权限。')
    } finally {
      setBusy(false)
    }
  }

  const data = status.data

  return (
    <SettingsSection title="飞书" description="通过飞书机器人私聊提问，并在 LearnGraph 继续同一条会话。">
      <div className="space-y-3 pt-1">
        <div className="flex flex-wrap items-center gap-2">
          {data?.personal_workspace && data.configured ? (
            <Badge variant={data.bound ? 'secondary' : 'outline'}>
              {data.bound ? '已绑定' : '待绑定'}
            </Badge>
          ) : null}
          <Button type="button" variant="outline" size="sm" onClick={onOpenTutorial}>
            <BookOpen className="size-3.5" />
            查看飞书接入教程
          </Button>
        </div>

        {status.isPending ? (
          <p className="text-sm text-muted-foreground">正在读取连接状态…</p>
        ) : status.isError ? (
          <p className="text-sm text-destructive">无法读取连接状态。</p>
        ) : !data?.personal_workspace ? (
          <p className="text-sm text-muted-foreground">请切换到你自己的个人工作区再配置并绑定。</p>
        ) : (
          <>
            {data.configured && !editingConfig && !qrSession && (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-muted-foreground">
                  {data.config_source === 'user' ? '已配置个人飞书机器人' : '当前使用系统默认飞书机器人'}
                  {data.app_id ? `（App ID: ${data.app_id}）` : ''}
                </span>
                <Button variant="outline" size="sm" disabled={busy} onClick={() => void startQrLogin()}>
                  <QrCode className="size-3.5" />
                  重新扫码接入
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => openConfigEditor(data.config_source === 'user' ? data.app_id : '')}
                >
                  {data.config_source === 'user' ? '修改手动配置' : '手动配置自己的机器人'}
                </Button>
                {data.config_source === 'user' && (
                  <Button variant="ghost" size="sm" disabled={busy} onClick={() => void removeConfig()}>
                    移除配置
                  </Button>
                )}
              </div>
            )}

            {!data.configured && !editingConfig && !qrSession && (
              <div className="w-full max-w-md space-y-3 rounded-xl border border-border/70 p-4">
                <p className="text-xs leading-5 text-muted-foreground">
                  使用飞书扫码即可一键创建个人助手应用并自动完成机器人与长连接配置，也支持手动填入飞书开放平台应用凭据。
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <Button type="button" size="sm" disabled={busy} onClick={() => void startQrLogin()}>
                    <QrCode className="size-4" />
                    扫码一键接入
                  </Button>
                  <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => openConfigEditor('')}>
                    <KeyRound className="size-3.5" />
                    手动配置 App ID / Secret
                  </Button>
                </div>
              </div>
            )}

            {qrSession && (
              <QrLoginBox
                qrUrl={qrSession.qrUrl}
                alt="飞书扫码一键接入二维码"
                statusText={
                  qrSession.status === 'pending'
                    ? '请使用手机飞书扫描二维码并确认授权'
                    : qrSession.errorText
                }
                statusTone={
                  qrSession.status === 'pending'
                    ? 'waiting'
                    : qrSession.status === 'expired'
                      ? 'expired'
                      : 'error'
                }
                userCode={qrSession.userCode}
                openUrl={qrSession.qrUrl}
                busy={busy}
                onRefresh={() => void startQrLogin()}
                onCancel={cancelQrLogin}
              />
            )}

            {editingConfig && (
              <form onSubmit={e => void saveConfig(e)} className="w-full max-w-md space-y-3 rounded-xl border border-border/70 p-4">
                <p className="text-xs leading-5 text-muted-foreground">
                  在飞书开放平台创建企业自建应用，启用机器人并在「事件与回调」选择长连接添加{' '}
                  <code className="text-foreground">im.message.receive_v1</code> 后，填入你的应用凭据：
                </p>
                <div className="space-y-1.5">
                  <Label htmlFor="feishu-app-id">App ID</Label>
                  <Input
                    id="feishu-app-id"
                    value={appId}
                    onChange={e => setAppId(e.target.value)}
                    placeholder="cli_xxxxxxxxxxxxxxxx"
                    autoComplete="off"
                    disabled={busy}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="feishu-app-secret">App Secret</Label>
                  <Input
                    id="feishu-app-secret"
                    type="password"
                    value={appSecret}
                    onChange={e => setAppSecret(e.target.value)}
                    placeholder={data.config_source === 'user' ? '留空则保持当前 Secret 不变' : '输入飞书应用 App Secret'}
                    autoComplete="new-password"
                    disabled={busy}
                  />
                </div>
                <div className="flex items-center gap-2 pt-1">
                  <Button type="submit" size="sm" disabled={busy}>
                    保存配置
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => {
                      setEditingConfig(false)
                      setError('')
                    }}
                  >
                    取消
                  </Button>
                </div>
              </form>
            )}

            {data.configured && !qrSession && (
              data.bound ? (
                <div className="flex flex-col items-start gap-2">
                  <p className="flex items-center gap-1.5 text-sm text-foreground">
                    <CheckCircle2 className="size-4 text-emerald-600 dark:text-emerald-400" />
                    已绑定。问题发送后，会话会出现在 LearnGraph「飞书 · 学习助手」。
                  </p>
                  <Button variant="outline" size="sm" disabled={busy} onClick={() => void act(true)}>
                    解除绑定
                  </Button>
                </div>
              ) : (
                <div className="flex flex-col items-start gap-2">
                  <Button size="sm" disabled={busy} onClick={() => void act(false)}>
                    {command ? '重新生成绑定码' : '生成绑定码'}
                  </Button>
                  {command && (
                    <div className="space-y-1.5 text-sm">
                      <p className="text-muted-foreground">10 分钟内，把这条命令发给飞书机器人的私聊。请勿分享绑定码。</p>
                      <div className="flex flex-wrap items-center gap-2">
                        <code className="select-all break-all rounded bg-muted px-2 py-1 text-xs">{command}</code>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            navigator.clipboard
                              .writeText(command)
                              .then(
                                () => toast.success('绑定命令已复制'),
                                () => toast.error('复制失败，请手动复制'),
                              )
                          }}
                        >
                          <Copy className="size-3.5" />
                          复制命令
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              )
            )}
          </>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </div>
    </SettingsSection>
  )
}

function WeChatChannelSection({
  workspaceId,
  onOpenTutorial,
}: {
  workspaceId: string
  onOpenTutorial: () => void
}) {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [qrSession, setQrSession] = useState<{
    sessionId: string
    qrUrl: string
    status: 'wait' | 'scanned' | 'expired' | 'error'
    errorText: string
  } | null>(null)
  const activeQrSessionIdRef = useRef<string | null>(null)

  const options = { headers: { 'X-Workspace-ID': workspaceId } }
  const status = useQuery({
    queryKey: ['im-wechat', workspaceId],
    queryFn: () => apiClient.get<WeChatStatus>(wechatEndpoint, options),
  })

  useEffect(() => {
    activeQrSessionIdRef.current = qrSession?.sessionId ?? null
  }, [qrSession?.sessionId])

  useEffect(() => {
    return () => {
      const sid = activeQrSessionIdRef.current
      if (sid) {
        void apiClient.delete(`${wechatEndpoint}/qr/${encodeURIComponent(sid)}`, options).catch(() => {})
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId])

  useEffect(() => {
    if (!qrSession || (qrSession.status !== 'wait' && qrSession.status !== 'scanned')) return
    let cancelled = false
    const timer = window.setTimeout(async () => {
      try {
        const res = await apiClient.get<WeChatQrPollResponse>(
          `${wechatEndpoint}/qr/${encodeURIComponent(qrSession.sessionId)}`,
          options,
        )
        if (cancelled) return
        if (res.status === 'confirmed') {
          setQrSession(null)
          setError('')
          if (res.channel_status) {
            queryClient.setQueryData(['im-wechat', workspaceId], res.channel_status)
          } else {
            await status.refetch()
          }
          toast.success('微信已扫码登录并绑定')
          return
        }
        if (res.status === 'wait' || res.status === 'scanned') {
          setQrSession(prev =>
            prev && prev.sessionId === qrSession.sessionId
              ? { ...prev, status: res.status as 'wait' | 'scanned' }
              : prev,
          )
          return
        }
        if (res.status === 'expired') {
          setQrSession(prev =>
            prev && prev.sessionId === qrSession.sessionId
              ? { ...prev, status: 'expired', errorText: '二维码已过期，请点击刷新重新获取。' }
              : prev,
          )
          return
        }
        setQrSession(prev =>
          prev && prev.sessionId === qrSession.sessionId
            ? { ...prev, status: 'error', errorText: res.message || '微信扫码登录失败，请刷新二维码重试。' }
            : prev,
        )
      } catch (err) {
        if (cancelled) return
        const msg = err instanceof Error ? err.message : ''
        if (msg.includes('already bound')) {
          setQrSession(prev =>
            prev && prev.sessionId === qrSession.sessionId
              ? { ...prev, status: 'error', errorText: '该微信机器人已绑定到其他账号，请使用你自己的微信扫码。' }
              : prev,
          )
          return
        }
        setQrSession(prev => (prev && prev.sessionId === qrSession.sessionId ? { ...prev } : prev))
      }
    }, 2000)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qrSession, workspaceId])

  async function startQrLogin() {
    setBusy(true)
    setError('')
    if (qrSession?.sessionId) {
      void apiClient.delete(`${wechatEndpoint}/qr/${encodeURIComponent(qrSession.sessionId)}`, options).catch(() => {})
    }
    try {
      const res = await apiClient.post<WeChatQrStartResponse, Record<string, never>>(
        `${wechatEndpoint}/qr`,
        {},
        options,
      )
      setQrSession({
        sessionId: res.session_id,
        qrUrl: res.qr_url,
        status: 'wait',
        errorText: '',
      })
    } catch {
      setError('获取微信登录二维码失败，请检查网络连接后重试。')
    } finally {
      setBusy(false)
    }
  }

  function cancelQrLogin() {
    const sid = qrSession?.sessionId
    setQrSession(null)
    if (sid) {
      void apiClient.delete(`${wechatEndpoint}/qr/${encodeURIComponent(sid)}`, options).catch(() => {})
    }
  }

  async function disconnect() {
    cancelQrLogin()
    setBusy(true)
    setError('')
    try {
      await apiClient.delete(`${wechatEndpoint}/config`, options)
      await status.refetch()
      toast.success('已断开微信连接')
    } catch {
      setError('断开微信连接失败，请重试。')
    } finally {
      setBusy(false)
    }
  }

  const data = status.data

  return (
    <SettingsSection
      title="个人微信"
      description="通过微信 ClawBot（iLink）扫码授权接入私聊问答，并在 LearnGraph 继续同一条会话。"
    >
      <div className="space-y-3 pt-1">
        <div className="flex flex-wrap items-center gap-2">
          {data?.personal_workspace && data.configured ? (
            <Badge variant={data.session_expired ? 'destructive' : 'secondary'}>
              {data.session_expired ? '登录已失效' : '已连接'}
            </Badge>
          ) : null}
          <Button type="button" variant="outline" size="sm" onClick={onOpenTutorial}>
            <BookOpen className="size-3.5" />
            查看微信接入教程
          </Button>
        </div>

        {status.isPending ? (
          <p className="text-sm text-muted-foreground">正在读取微信连接状态…</p>
        ) : status.isError ? (
          <p className="text-sm text-destructive">无法读取微信连接状态。</p>
        ) : !data?.personal_workspace ? (
          <p className="text-sm text-muted-foreground">请切换到你自己的个人工作区再扫码接入微信。</p>
        ) : (
          <>
            {!data.configured && !qrSession && (
              <div className="w-full max-w-md space-y-3 rounded-xl border border-border/70 p-4">
                <p className="text-xs leading-5 text-muted-foreground">
                  打开手机微信扫描二维码并确认授权，即可自动绑定你的个人工作区并收发学习问答，无需手动配置密钥。
                </p>
                <Button type="button" size="sm" disabled={busy} onClick={() => void startQrLogin()}>
                  <QrCode className="size-4" />
                  扫码登录微信
                </Button>
              </div>
            )}

            {qrSession && (
              <QrLoginBox
                qrUrl={qrSession.qrUrl}
                alt="微信 ClawBot 扫码登录二维码"
                statusText={
                  qrSession.status === 'wait'
                    ? '请打开手机微信「扫一扫」扫描二维码'
                    : qrSession.status === 'scanned'
                      ? '已扫码，请在手机微信中点击确认授权'
                      : qrSession.errorText
                }
                statusTone={
                  qrSession.status === 'wait'
                    ? 'waiting'
                    : qrSession.status === 'scanned'
                      ? 'scanned'
                      : qrSession.status === 'expired'
                        ? 'expired'
                        : 'error'
                }
                busy={busy}
                onRefresh={() => void startQrLogin()}
                onCancel={cancelQrLogin}
              />
            )}

            {data.configured && !qrSession && (
              <div className="flex flex-col items-start gap-3">
                {data.session_expired ? (
                  <p role="alert" className="flex items-center gap-1.5 text-sm text-destructive">
                    <AlertTriangle className="size-4 shrink-0" />
                    {data.last_error || '微信登录态已失效，请重新扫码登录。'}
                  </p>
                ) : (
                  <p className="flex items-center gap-1.5 text-sm text-foreground">
                    <CheckCircle2 className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
                    已连接微信机器人{data.bot_id ? `（${data.bot_id}）` : ''}。发送私聊问题后，会话会自动同步到 LearnGraph「微信 · 学习助手」。
                  </p>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant={data.session_expired ? 'default' : 'outline'}
                    size="sm"
                    disabled={busy}
                    onClick={() => void startQrLogin()}
                  >
                    <QrCode className="size-3.5" />
                    {data.session_expired ? '重新扫码登录' : '重新扫码'}
                  </Button>
                  <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void disconnect()}>
                    退出登录
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </div>
    </SettingsSection>
  )
}
