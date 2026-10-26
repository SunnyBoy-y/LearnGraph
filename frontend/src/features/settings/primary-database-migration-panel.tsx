import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Circle, Database, LoaderCircle, RefreshCcw, ShieldCheck } from 'lucide-react'
import { toast } from 'sonner'
import { activatePrimaryDatabaseMigration, cancelPrimaryDatabaseMigration, getPrimaryDatabaseMigration, preflightPrimaryDatabaseMigration, resumePrimaryDatabaseMigration, startPrimaryDatabaseMigration } from '@/api/migrations'
import { SectionHeading, Surface } from '@/components/shared/page-elements'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useAuth } from '@/features/auth/auth-context-value'
import { DatabaseConfigurationSheet } from './database-configuration-sheet'
import type { PrimaryDatabaseMigration } from '@/types/migrations'

const labels: Record<string, string> = {
  QUEUED: '等待执行', BACKING_UP: '备份主业务库', VERIFYING_BACKUP: '验证备份与恢复', BACKUP_VERIFIED: '备份验证通过',
  STARTING_AGENT: '启动数据库迁移服务', AGENT_READY: '数据库迁移服务已就绪',
  PULLING_IMAGE: '拉取 PostgreSQL 镜像', STARTING_POSTGRESQL: '启动 PostgreSQL', TARGET_RUNNING: 'PostgreSQL 已就绪',
  PREPARING_TARGET: '准备目标结构', SNAPSHOTTING: '创建一致性快照', COPYING: '复制全量数据', VERIFYING_DATA: '逐表核对数据',
  BASELINE_VERIFIED: '全量数据一致', VERIFYING_TARGET: '检查目标兼容与持久性', CATCHING_UP: '同步新增事务',
  READY_TO_ACTIVATE: '全部检查通过，等待切换', ACTIVATING: '协调事务并切换主库', COMPLETED: '迁移成功',
  RESUMING: '继续迁移任务',
  FAILED: '迁移受阻，尚未切换', CANCELLED: '已取消', RECOVERING: '正在恢复切换状态',
}
const checkLabels: Record<string, string> = {
  source_integrity: 'SQLite 完整性与外键', source_wal: 'WAL 在线快照能力', source_catalogue: '所有主业务表均有映射', source_columns: '主业务字段映射完整', json_capture: '事务增量捕获能力',
  backup_required: '强制备份及恢复验证', deployment_key: '部署加密密钥可用',
  source_custom_objects: '触发器与视图均已识别', maintenance_conflicts: '没有其他迁移维护锁', updater_interlock: '旧更新器不会并发切换服务',
  source_indexes: '索引与唯一约束均可保留',
}

function CounterProgress({ label, current = 0, total = 0 }: { label: string; current?: number; total?: number }) {
  return <div className="space-y-1.5"><div className="flex justify-between gap-3 text-xs"><span>{label}</span><span className="tabular-nums text-muted-foreground">{current.toLocaleString()} / {total.toLocaleString()}</span></div>
    <div aria-label={label} role="progressbar" aria-valuemin={0} aria-valuemax={total || 1} aria-valuenow={current} className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary transition-[width]" style={{ width: `${total ? Math.min(100, current / total * 100) : 0}%` }} /></div></div>
}

export function PrimaryDatabaseMigrationPanel() {
  const { isSystemAdmin, userId } = useAuth()
  const queryClient = useQueryClient()
  const key = ['primary-database-migration', userId]
  const [managed, setManaged] = useState(true)
  const [autoActivate, setAutoActivate] = useState(true)
  const [acknowledged, setAcknowledged] = useState(false)
  const [password, setPassword] = useState('')
  const [configurationOpen, setConfigurationOpen] = useState(false)
  useEffect(() => { setPassword(''); setAcknowledged(false); setConfigurationOpen(false) }, [userId, isSystemAdmin])
  const status = useQuery({ queryKey: key, queryFn: getPrimaryDatabaseMigration, enabled: isSystemAdmin,
    refetchInterval: query => query.state.data?.active_database === 'postgresql' ? false : 1500 })
  const accept = (value: PrimaryDatabaseMigration) => { queryClient.setQueryData(key, value); void queryClient.invalidateQueries({ queryKey: key }) }
  const preflight = useMutation({ mutationFn: preflightPrimaryDatabaseMigration, onError: error => toast.error(error.message) })
  const start = useMutation({ mutationFn: () => startPrimaryDatabaseMigration({ confirm: true, current_password: password, managed, auto_activate: autoActivate }),
    onSuccess: value => { accept(value); setPassword('') }, onError: error => toast.error(error.message) })
  const activate = useMutation({ mutationFn: () => activatePrimaryDatabaseMigration(password), onSuccess: value => { accept(value); setPassword('') }, onError: error => toast.error(error.message) })
  const cancel = useMutation({ mutationFn: cancelPrimaryDatabaseMigration, onSuccess: accept, onError: error => toast.error(error.message) })
  const resume = useMutation({ mutationFn: resumePrimaryDatabaseMigration, onSuccess: accept, onError: error => toast.error(error.message) })
  if (!isSystemAdmin) return null
  const job = status.data
  const complete = job?.active_database === 'postgresql' && job.state !== 'RECOVERING'
  const terminal = !job?.id || ['FAILED', 'CANCELLED'].includes(job.state ?? '')
  const canStart = !!preflight.data?.ready && !!password && acknowledged && !start.isPending && !job?.running && !complete && terminal && (!managed || !!job?.agent_configured)
  const busy = start.isPending || activate.isPending || cancel.isPending || resume.isPending
  const gates = [['备份与恢复验证', job?.backup_verified], ['全量逐表校验', job?.baseline_verified], ['检索兼容检查', job?.search_verified], ['目标读写与持久性', job?.target_verified], ['切换前检查', job?.all_checks_passed]] as const
  return <Surface className="space-y-5 p-5" data-testid="primary-database-migration">
    <SectionHeading title="主业务数据库" description="仅系统管理员可将 SQLite 单向迁移至 PostgreSQL。新安装继续默认使用 SQLite。" action={<Button aria-label="刷新主库迁移状态" variant="ghost" size="icon-sm" onClick={() => void status.refetch()}><RefreshCcw className={status.isFetching ? 'size-4 animate-spin' : 'size-4'} /></Button>} />
    <div className="flex flex-wrap items-center gap-3 rounded-xl border bg-muted/20 p-4"><Database className="size-5 text-primary" /><span className="font-medium">当前主库：{complete ? 'PostgreSQL' : 'SQLite'}</span><span className="ml-auto text-sm text-muted-foreground" aria-live="polite">{job?.running && <LoaderCircle className="mr-1 inline size-4 animate-spin" />}{labels[job?.state ?? ''] ?? '尚未发起迁移'}</span></div>
    {status.isError && <p role="alert" className="text-sm text-destructive">状态读取失败：{status.error.message}</p>}
    {complete ? <div className="rounded-xl border border-primary/30 bg-primary/5 p-4 text-sm leading-6"><ShieldCheck className="mr-2 inline size-4 text-primary" />主业务读写已全部指向 PostgreSQL，SQLite 已永久封存，禁止回退。sandboxd 独立数据库保持原状。</div> : <>
      <div className="flex flex-wrap items-center gap-3"><Label htmlFor="primary-db-target">PostgreSQL 部署方式</Label><Select value={managed ? 'docker' : 'external'} onValueChange={value => setManaged(value === 'docker')} disabled={!terminal || job?.running}><SelectTrigger id="primary-db-target" className="w-64"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="docker">Docker 自动部署</SelectItem><SelectItem value="external">已有 PostgreSQL 空库</SelectItem></SelectContent></Select>{!managed && <Button variant="outline" onClick={() => setConfigurationOpen(true)} disabled={!terminal}>配置 PostgreSQL 连接</Button>}</div>
      {managed && !job?.agent_configured && <p className="text-sm text-amber-700">此部署尚未启用数据库迁移代理。请更新 Docker Compose 部署，或使用已有 PostgreSQL 空库。</p>}
      {managed && job?.agent_auto_start && <p className="text-sm leading-6 text-muted-foreground">迁移服务将在需要时自动启动，PostgreSQL 准备就绪后自动停止；数据库继续独立运行。</p>}
      <p className="text-sm leading-6 text-muted-foreground">迁移期间服务继续运行，后台持续同步新增事务。系统先备份并验证恢复，再复制、逐表核对和检查检索能力；全部通过后协调现有事务，切换期间新请求短暂等待。</p>
      <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => preflight.mutate()} disabled={preflight.isPending || !terminal || !!job?.running}>{preflight.isPending ? '预检中…' : '执行迁移预检'}</Button>{job?.id && !job.irreversible && job.state !== 'CANCELLED' && <Button variant="outline" disabled={busy} onClick={() => cancel.mutate()}>取消本次迁移</Button>}{job?.id && job.state !== 'CANCELLED' && !job.running && !job.irreversible && <Button variant="outline" disabled={busy} onClick={() => resume.mutate()}>继续本次迁移</Button>}</div>
      {preflight.data && <ul className="grid gap-2 text-sm sm:grid-cols-2">{preflight.data.checks.map(check => <li key={check.key} className="flex items-center gap-2">{check.passed ? <Check className="size-4 text-primary" /> : <Circle className="size-4 text-destructive" />}<span>{checkLabels[check.key] ?? check.key}：{check.passed ? '通过' : '未通过'}</span></li>)}</ul>}
      {(terminal || job?.state === 'READY_TO_ACTIVATE') && <div className="space-y-3 rounded-xl border p-4"><div className="flex items-start gap-2"><Checkbox id="primary-db-irreversible" checked={acknowledged} onCheckedChange={value => setAcknowledged(value === true)} /><Label htmlFor="primary-db-irreversible" className="leading-5">我确认仅迁移主业务库，成功后永久使用 PostgreSQL，不支持迁回 SQLite。</Label></div><div className="flex items-center gap-2"><Checkbox id="primary-db-auto" checked={autoActivate} onCheckedChange={value => setAutoActivate(value === true)} disabled={!terminal} /><Label htmlFor="primary-db-auto">全部检查通过后自动切换</Label></div><div className="flex flex-wrap items-end gap-3"><div className="space-y-1.5"><Label htmlFor="primary-db-password">当前管理员密码</Label><Input id="primary-db-password" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} className="w-64" /></div>{terminal ? <Button disabled={!canStart} onClick={() => start.mutate()}>{start.isPending ? '提交中…' : '强制备份并开始迁移'}</Button> : <Button disabled={!job?.all_checks_passed || !acknowledged || !password || busy} onClick={() => activate.mutate()}>确认切换至 PostgreSQL</Button>}</div></div>}
    </>}
    {job?.id && <div className="space-y-4 border-t pt-4"><div className="grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-3">{gates.map(([label, passed]) => <div key={label} className="flex items-center gap-2">{passed ? <Check className="size-4 text-primary" /> : <Circle className="size-4 text-muted-foreground" />}{label}<span className="text-xs text-muted-foreground">{passed ? '通过' : '待完成'}</span></div>)}</div>
      {job.agent_state === 'stopped' && <p className="text-xs text-muted-foreground">临时迁移服务已停止，不影响 PostgreSQL 运行。</p>}
      {job.agent_state === 'cleanup_pending' && <p role="status" className="text-sm text-amber-700">临时迁移服务正在等待自动回收，数据库不受影响。</p>}
      {!!job.backup_pages_total && !job.backup_verified && <CounterProgress label="备份页数" current={job.backup_pages_copied} total={job.backup_pages_total} />}
      {!!job.tables_total && <CounterProgress label="全量复制表数" current={job.tables_copied} total={job.tables_total} />}
      {!!job.tables_total && <CounterProgress label="内容校验表数" current={job.tables_verified} total={job.tables_total} />}
      <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-muted-foreground">{job.rows_copied !== undefined && <span>已复制 {job.rows_copied.toLocaleString()} 行</span>}{job.backup_bytes !== undefined && <span>备份 {(job.backup_bytes / 1048576).toFixed(1)} MiB</span>}{job.pending_transactions !== undefined && <span>待同步 {job.pending_transactions} 个事务</span>}{job.current_table && !complete && <span>当前表 {job.current_table}</span>}</div>
      {job.state === 'ACTIVATING' && <p className="text-sm text-muted-foreground">等待现有事务结束，服务继续运行，不会强制中断请求。</p>}
      {job.image_progress?.layers && <details><summary className="cursor-pointer text-sm">Docker 镜像拉取进度</summary><ul className="mt-2 space-y-1 text-xs text-muted-foreground">{Object.entries(job.image_progress.layers).map(([id, layer]) => <li key={id}>{id} · {layer.status}{layer.total ? ` · ${Math.round((layer.current ?? 0) / layer.total * 100)}%` : ''}</li>)}</ul></details>}
      {job.error && <p role="alert" className="rounded-lg bg-destructive/5 p-3 text-sm text-destructive">{job.state === 'RECOVERING' ? '切换状态需要恢复，系统禁止回退 SQLite。' : '检查未通过，尚未更改主库指向。'} {job.error}</p>}
      <details><summary className="cursor-pointer text-sm">查看执行记录</summary><ol className="mt-3 space-y-2 text-xs text-muted-foreground">{job.events?.map(event => <li key={event.sequence}>{new Date(event.at).toLocaleTimeString()} · {labels[event.state] ?? event.state}</li>)}</ol></details>
    </div>}
    <DatabaseConfigurationSheet open={configurationOpen} onOpenChange={setConfigurationOpen} providerKind="postgresql" onSaved={() => void status.refetch()} />
  </Surface>
}
