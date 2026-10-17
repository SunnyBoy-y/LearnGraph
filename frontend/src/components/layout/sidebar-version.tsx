import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowDownToLine, Check, ExternalLink, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { ApiError } from '@/api/client'
import { checkUpdates, getAppVersion, getUpdateStatus, isUpdateRunning, startUpdate } from '@/api/updates'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { AlertDialog, AlertDialogContent, AlertDialogTitle, AlertDialogDescription, AlertDialogFooter, AlertDialogCancel, AlertDialogAction } from '@/components/ui/alert-dialog'
import './sidebar-version.css'

export function SidebarVersion({ isSystemAdmin }: { isSystemAdmin: boolean }) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [target, setTarget] = useState<string | null>(null)
  const app = useQuery({ queryKey: ['application-version'], queryFn: getAppVersion, staleTime: 60_000, retry: false })
  const docker = app.data?.runtime_mode === 'docker'
  const status = useQuery({
    queryKey: ['deployment-updates'], queryFn: getUpdateStatus,
    enabled: isSystemAdmin && docker, staleTime: 30_000, retry: false,
    refetchInterval: query => query.state.error instanceof ApiError && query.state.error.status === 404
      ? false : query.state.data?.jobs?.some(isUpdateRunning) ? 3000 : 30_000,
  })
  const data = isSystemAdmin && docker ? status.data : undefined
  const active = data?.jobs?.find(isUpdateRunning)
  const available = Boolean(data?.enabled && data.has_update && data.latest)
  const version = app.data?.version ?? '版本信息'
  const check = useMutation({
    mutationFn: checkUpdates,
    onSuccess: value => client.setQueryData(['deployment-updates'], value),
    onError: (error: Error) => toast.error(error.message),
  })
  const install = useMutation({
    mutationFn: startUpdate,
    onSuccess: () => { toast.success('更新任务已提交，服务切换期间会自动重连'); setOpen(true) },
    onError: (error: Error) => toast.error(error.message),
    onSettled: () => { void client.invalidateQueries({ queryKey: ['deployment-updates'] }) },
  })
  const busy = Boolean(active) || install.isPending
  const blocked = busy || check.isPending || status.isError || Boolean(data?.check_error || data?.deployment_error)

  return <>
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button type="button" className="sidebar-version" data-update={available || undefined} aria-label={`版本与更新：${version}${available ? '，有新版本可用' : ''}`}>
          {version}<span className="sidebar-version__dot" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" sideOffset={10} className="sidebar-version-panel" aria-label="版本与更新">
        <div className="sidebar-version-panel__header"><span>当前版本</span>
          {data?.enabled ? <button type="button" aria-label="检查更新" title="检查更新" disabled={busy || check.isPending} onClick={() => check.mutate()}>
            <RefreshCw className={check.isPending ? 'animate-spin' : undefined} size={15} />
          </button> : null}
        </div>
        <div className="sidebar-version-panel__identity">
          <strong>{version}</strong>
          <span>{data?.latest ? `最新版本：${data.latest.version}` : docker ? 'Docker 部署' : app.data?.runtime_mode === 'source' ? '源码部署' : '版本信息暂不可用'}</span>
        </div>
        <div className="sidebar-version-panel__body">
          {available ? <div className="sidebar-version-panel__notice"><span className="sidebar-version-panel__notice-icon"><ArrowDownToLine size={18} /></span><div>有新版本可用！<small>{data?.latest?.version}</small></div></div>
            : data?.enabled && !status.isError && !data.check_error && !data.deployment_error ? <p className="sidebar-version-panel__message"><Check size={16} />{data.latest ? '已是最新版本' : '尚未检查到正式版本'}</p> : null}
          {available ? <button type="button" className="sidebar-version-panel__install" disabled={blocked} onClick={() => { setTarget(data!.latest!.version); setOpen(false) }}>
            <ArrowDownToLine size={16} />{busy ? '正在更新…' : '立即更新'}
          </button> : null}
          {active ? <p role="status" className="sidebar-version-panel__message">{active.message}</p> : null}
          {status.isError && isSystemAdmin && docker ? <p role="status" className="sidebar-version-panel__message">暂时无法获取更新状态，请稍后重试。</p> : null}
          {data?.check_error || data?.deployment_error ? <p role="alert" className="sidebar-version-panel__message">{data.check_error || data.deployment_error}</p> : null}
          {docker && isSystemAdmin && data && !data.enabled ? <p className="sidebar-version-panel__message">配置独立更新器后可在此升级。</p> : null}
          {data?.latest?.url ? <a className="sidebar-version-panel__changelog" href={data.latest.url} target="_blank" rel="noopener noreferrer">查看更新日志<ExternalLink size={12} /></a> : null}
        </div>
      </PopoverContent>
    </Popover>
    <AlertDialog open={Boolean(target)} onOpenChange={value => { if (!value) setTarget(null) }}>
      <AlertDialogContent>
        <AlertDialogTitle>更新至 {target}？</AlertDialogTitle>
        <AlertDialogDescription>更新期间服务会短暂中断，正在进行的对话和语音可能断开。切换前会备份数据库，健康检查失败时恢复原镜像。</AlertDialogDescription>
        <AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction disabled={blocked || !available} onClick={() => { if (target && !blocked && available) install.mutate({ version: target }); setTarget(null) }}>确认更新</AlertDialogAction></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  </>
}
