import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDownToLine, History, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { getCurrentUser } from '@/api/control'
import { checkUpdates, getAppVersion, getUpdateStatus, isUpdateRunning, setUpdatePolicy, startUpdate, type UpdateJob } from '@/api/updates'
import { ApiError } from '@/api/client'
import { LoadingState, PageFrame, PageIntro } from '@/components/shared/page-elements'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { AlertDialog, AlertDialogContent, AlertDialogTitle, AlertDialogDescription, AlertDialogFooter, AlertDialogCancel, AlertDialogAction } from '@/components/ui/alert-dialog'
import { SettingRow, SettingsSection } from './settings-primitives'

const labels: Record<string, string> = {
  queued: '已排队', preparing: '准备中', pulling: '下载镜像', backing_up: '备份数据',
  applying: '切换版本', rolling_back: '恢复原版本', succeeded: '已完成', failed: '未安装',
  rolled_back: '已自动回滚', rollback_failed: '需要人工处理',
}

function formatTime(value?: string | null) {
  return value ? new Date(value).toLocaleString() : '尚未检查'
}

export function UpdatesPage() {
  const client = useQueryClient()
  const user = useQuery({ queryKey: ['auth-me'], queryFn: getCurrentUser })
  const admin = Boolean(user.data?.is_system_admin)
  const appVersion = useQuery({ queryKey: ['application-version'], queryFn: getAppVersion, retry: false })
  const status = useQuery({
    queryKey: ['deployment-updates'], queryFn: getUpdateStatus, enabled: admin,
    // Keep polling through the short connection gap, including when a mutation
    // response was lost or this page was reloaded during deployment.
    refetchInterval: query => query.state.error instanceof ApiError && query.state.error.status === 404
      ? false : query.state.data?.jobs?.some(isUpdateRunning) || query.state.error ? 3000 : 30000,
    refetchIntervalInBackground: true,
    retry: false,
  })
  const [confirmation, setConfirmation] = useState<{ version: string } | { rollback_id: string; version: string } | null>(null)
  const [enableAuto, setEnableAuto] = useState(false)
  const reportError = (error: Error) => toast.error(error.message)
  const check = useMutation({
    mutationFn: checkUpdates,
    onSuccess: data => client.setQueryData(['deployment-updates'], data), onError: reportError,
  })
  const install = useMutation({
    mutationFn: startUpdate,
    onSuccess: () => { toast.success('任务已提交，服务切换期间会自动重连'); void client.invalidateQueries({ queryKey: ['deployment-updates'] }) },
    onError: error => { reportError(error); void client.invalidateQueries({ queryKey: ['deployment-updates'] }) },
  })
  const policy = useMutation({
    mutationFn: setUpdatePolicy,
    onSuccess: () => { void client.invalidateQueries({ queryKey: ['deployment-updates'] }) }, onError: reportError,
  })
  const data = status.data
  const displayedVersion = data?.current.version ?? appVersion.data?.version ?? (appVersion.isPending ? '读取中' : '无法读取版本')
  const missingEndpoint = status.error instanceof ApiError && status.error.status === 404
  const active = data?.jobs?.find(isUpdateRunning)
  const busy = Boolean(active) || install.isPending
  const rollbackJobs = data?.jobs?.filter(job => job.status === 'succeeded' && job.previous_version && job.previous_version !== data.current.version) ?? []

  if (user.isPending) return <LoadingState />
  return <PageFrame>
    <PageIntro title="版本与更新" description="检测 GitHub 正式版本，管理应用升级与历史版本回退。" />
    {!admin ? <p className="text-sm text-muted-foreground">仅系统管理员可管理应用版本。</p> : <>
      <SettingsSection title="当前部署" description="更新由宿主机上的独立更新器执行。单实例切换时会短暂断开，页面自动重连。">
        <SettingRow title="当前版本" description={data?.repository}>
          <Badge variant="outline">{displayedVersion}</Badge>
          <Button variant="outline" size="sm" disabled={!data?.enabled || check.isPending || busy} onClick={() => check.mutate()}>
            <RefreshCw className="size-3.5" />{check.isPending ? '检查中' : '检查更新'}
          </Button>
        </SettingRow>
        <p className="p-3 text-xs text-muted-foreground">最近检查：{formatTime(data?.checked_at)}</p>
        {!data?.enabled && data ? <p role="status" className="p-3 text-sm">{data.message}</p> : null}
        {status.isError ? <p role="status" className="p-3 text-sm text-muted-foreground">{missingEndpoint ? '当前后端尚未部署版本管理接口，请更新应用容器。' : `暂时无法连接更新器，正在重连。${status.error.message}`}</p> : null}
        {data?.check_error || data?.deployment_error ? <p role="alert" className="p-3 text-sm text-destructive">{data.check_error ?? data.deployment_error}</p> : null}
      </SettingsSection>

      {data?.enabled ? <>
        <SettingsSection title="新版本" description="安装前会验证镜像、数据库兼容能力，并备份当前 SQLite 或 PostgreSQL 主库；迁移后更新与版本回退均保持 PostgreSQL。镜像尚未发布时任务安全终止。">
          <SettingRow title={data.latest?.version ?? '尚无正式版本'} description={data.has_update ? '发现可升级版本' : '未发现更高版本'}>
            <Button disabled={!data.has_update || busy || Boolean(data.deployment_error) || Boolean(data.check_error)} size="sm" onClick={() => data.latest && setConfirmation({ version: data.latest.version })}>
              <ArrowDownToLine className="size-3.5" />立即更新
            </Button>
          </SettingRow>
          {data.latest ? <details className="p-3 text-sm"><summary className="cursor-pointer">更新日志</summary>
            <p className="mt-3 whitespace-pre-wrap break-words text-muted-foreground">{data.latest.notes || '该版本未提供更新日志。'}</p>
            <a className="mt-3 inline-block underline" href={data.latest.url} target="_blank" rel="noreferrer noopener">在 GitHub 查看</a>
          </details> : null}
          <SettingRow title="自动安装正式版本" description="更新器定期检测；开启后可在无人值守时安装，失败版本不会反复自动重试。">
            <Switch aria-label="自动安装正式版本" checked={Boolean(data.auto_install)} disabled={policy.isPending} onCheckedChange={value => value ? setEnableAuto(true) : policy.mutate(false)} />
          </SettingRow>
        </SettingsSection>

        {active ? <SettingsSection title="更新进度"><div role="status" aria-live="polite" className="space-y-2 p-3">
          <Badge>{labels[active.status] ?? active.status}</Badge><p className="text-sm">{active.message}</p>
        </div></SettingsSection> : null}

        <SettingsSection title="部署历史" description="回退使用已保留的本地镜像。数据库保持当前数据，不自动恢复旧快照。">
          {!data.jobs?.length ? <p className="p-3 text-sm text-muted-foreground">尚无部署记录</p> : data.jobs.map((job: UpdateJob) => <div key={job.id} className="space-y-2 border-b p-3 last:border-0">
            <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm font-medium">{job.kind === 'rollback' ? '回退至' : '升级至'} {job.target_version}</span>
              <Badge variant={job.status === 'rollback_failed' || job.status === 'failed' ? 'destructive' : 'outline'}>{labels[job.status] ?? job.status}</Badge></div>
            <p className="text-xs text-muted-foreground">{formatTime(job.created_at)} · {job.message}</p>
            {job.error ? <p className="text-xs text-destructive">{job.error}</p> : null}
            {rollbackJobs.some(item => item.id === job.id) ? <Button variant="outline" size="sm" disabled={busy} onClick={() => setConfirmation({ rollback_id: job.id, version: job.previous_version! })}>
              <History className="size-3.5" />回退到 {job.previous_version}
            </Button> : null}
          </div>)}
        </SettingsSection>
      </> : null}
    </>}

    <AlertDialog open={Boolean(confirmation)} onOpenChange={open => { if (!open) setConfirmation(null) }}>
      <AlertDialogContent><AlertDialogTitle>切换到 {confirmation?.version}？</AlertDialogTitle>
        <AlertDialogDescription>服务会短暂中断，正在进行的对话和语音可能断开。切换前会备份数据库；健康检查失败时恢复原镜像，保留当前数据。</AlertDialogDescription>
        <AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction disabled={busy} onClick={() => {
          if (confirmation) install.mutate('rollback_id' in confirmation ? { rollback_id: confirmation.rollback_id } : { version: confirmation.version })
          setConfirmation(null)
        }}>确认切换</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
    <AlertDialog open={enableAuto} onOpenChange={setEnableAuto}>
      <AlertDialogContent><AlertDialogTitle>启用自动安装？</AlertDialogTitle>
        <AlertDialogDescription>发现正式新版本后将自动执行备份和更新，可能中断正在进行的对话。失败版本需管理员检查后手动重试。</AlertDialogDescription>
        <AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction onClick={() => { policy.mutate(true); setEnableAuto(false) }}>启用自动安装</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </PageFrame>
}
