import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/features/auth/auth-context-value'
import { SettingsSection } from './settings-primitives'

const endpoint = '/im/feishu'
type Status = { configured: boolean; personal_workspace: boolean; bound: boolean; session_id: string | null }

export function IMChannelsSettings() {
  const { workspaceId } = useAuth()
  return <ChannelPanel key={workspaceId} workspaceId={workspaceId} />
}

function ChannelPanel({ workspaceId }: { workspaceId: string }) {
  const [command, setCommand] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const options = { headers: { 'X-Workspace-ID': workspaceId } }
  const status = useQuery({
    queryKey: ['im-feishu', workspaceId],
    queryFn: () => apiClient.get<Status>(endpoint, options),
    refetchInterval: command ? 3000 : false,
  })

  async function act(unbind: boolean) {
    setBusy(true)
    setError('')
    setCommand('')
    try {
      if (unbind) {
        await apiClient.delete(`${endpoint}/binding`, options)
        await status.refetch()
      } else {
        const { code } = await apiClient.post<{ code: string }, Record<string, never>>(`${endpoint}/binding-code`, {}, options)
        setCommand(`/bind ${code}`)
      }
    } catch {
      setError(unbind ? '解绑失败，请重试。' : '无法生成绑定码，请检查 Bot 配置或当前工作区权限。')
    } finally {
      setBusy(false)
    }
  }

  return <>
    <SettingsSection title="飞书" description="通过飞书机器人私聊提问，并在 LearnGraph 继续同一条会话。">
      <div className="flex flex-col items-start gap-3">
        {status.isPending ? <p>正在读取连接状态…</p> : status.isError ? <p>无法读取连接状态。</p> :
          !status.data.personal_workspace ? <p>请切换到你自己的个人工作区再绑定。</p> :
          !status.data.configured ? <p>尚未配置，请联系部署管理员。</p> : status.data.bound ?
          <><p>已绑定。问题发送后，会话会出现在 LearnGraph 会话列表。</p><Button variant="outline" disabled={busy} onClick={() => void act(true)}>解除绑定</Button></> :
          <><Button disabled={busy} onClick={() => void act(false)}>{command ? '重新生成绑定码' : '生成绑定码'}</Button>
            {command && <div><p>10 分钟内，把这条命令发给飞书机器人的私聊。请勿分享绑定码。</p><code className="select-all break-all">{command}</code></div>}</>}
        {error && <p role="alert">{error}</p>}
      </div>
    </SettingsSection>
    <SettingsSection title="个人微信" description="计划通过 ClawBot 扫码授权接入，当前尚未开放。">{null}</SettingsSection>
  </>
}
