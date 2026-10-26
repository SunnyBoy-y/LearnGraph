import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { downloadFullBackup, restoreFullBackup } from '@/api/migrations'
import { useAuth } from '@/features/auth/auth-context-value'
import { SectionHeading, Surface } from '@/components/shared/page-elements'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { BackupRestoreResult } from '@/types/migrations'

export function SiteBackupPanel() {
  const { isSystemAdmin } = useAuth()
  const [operation, setOperation] = useState<'backup' | 'restore' | null>(null)
  const [currentPassword, setCurrentPassword] = useState('')
  const [backupPassword, setBackupPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<BackupRestoreResult | null>(null)
  const close = () => {
    setOperation(null)
    setCurrentPassword('')
    setBackupPassword('')
    setConfirmation('')
  }
  const backup = useMutation({
    mutationFn: async () => {
      await downloadFullBackup(currentPassword, backupPassword)
    },
    onSuccess: () => { close(); toast.success('加密全站备份已交给浏览器下载') },
    onError: (error) => toast.error(error.message),
    onSettled: () => setCurrentPassword(''),
  })
  const restore = useMutation({
    mutationFn: () => {
      if (!file) throw new Error('请选择备份文件')
      return restoreFullBackup(file, currentPassword, backupPassword)
    },
    onSuccess: (value) => { setResult(value); close(); toast.success('全站备份校验通过并已暂存，尚未覆盖数据') },
    onError: (error) => toast.error(error.message),
    onSettled: () => setCurrentPassword(''),
  })
  if (!isSystemAdmin) return <p className="text-sm text-muted-foreground">全站备份与恢复仅系统管理员可操作。</p>
  const busy = backup.isPending || restore.isPending
  return <Surface className="p-5">
    <SectionHeading title="全站备份与数据恢复" description="仅系统管理员可操作，每次都需验证当前密码。包含所有用户、工作区、模型 API Key、实际生效配置、上传文件与 Memory，整个备份使用独立备份密码加密。" />
    <p className="mt-3 text-xs leading-5 text-muted-foreground">支持 SQLite、PostgreSQL 与默认本地文件存储。迁移后会自动备份实际生效的 PostgreSQL，恢复也保持 PostgreSQL，禁止回退 SQLite。MinIO、运行中的沙箱磁盘、程序源码与镜像须单独备份；存在活动外部文件存储时会拒绝生成不完整备份。全站恢复先校验并暂存，停服后用恢复工具应用。</p>
    <p className="mt-2 text-xs leading-5 text-muted-foreground">大备份会直接写入所选文件，或交给浏览器、手机 App 下载。下载授权仅当前管理员会话可用，十分钟内有效；服务器会先检查临时空间，下载结束后清理暂存文件。</p>
    <div className="mt-4 flex flex-wrap gap-3">
      <Button onClick={() => setOperation('backup')} disabled={busy}>下载加密全站备份</Button>
      <Button variant="outline" onClick={() => setOperation('restore')} disabled={busy}>校验全站备份并暂存恢复</Button>
    </div>
    {result && <div role="status" className="mt-4 space-y-2 rounded-xl border p-4 text-sm">
      <p>已校验并暂存：{result.tables} 张表、{result.records} 条记录、{result.files} 个文件、{result.memory_files} 个 Memory 文件。当前数据尚未覆盖。</p>
      <p>停止全部 API、worker 与调度器后，在相同应用镜像或 backend 环境运行以下命令并输入备份密码。恢复将覆盖全站数据与配置，保留恢复前副本；PostgreSQL 恢复在事务内逐表校验后提交，完成后重启并重新登录。容器环境变量需与恢复配置一致。</p>
      <code className="block break-all text-xs">python -m app.services.site_restore --backup &quot;{result.restore_path}&quot; --server-stopped</code>
    </div>}
    <Dialog open={operation !== null} onOpenChange={(open) => { if (!open && !busy) close() }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{operation === 'backup' ? '验证管理员密码并备份全站' : '验证管理员密码并校验全站备份'}</DialogTitle>
          <DialogDescription>{operation === 'backup' ? '设置至少 12 位的独立备份密码。API Key 和实际配置只存在于加密内容内；恢复时必须提供此密码。' : '当前管理员密码用于授权；备份密码用于解密。此步骤只校验和暂存，全站覆盖恢复须停服执行。'}</DialogDescription>
        </DialogHeader>
        <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); if (busy) return; if (operation === 'backup') { if (backupPassword !== confirmation) return; backup.mutate() } else restore.mutate() }}>
          {operation === 'restore' && <label className="block text-sm">全站备份 ZIP<Input required type="file" accept=".zip,application/zip" disabled={busy} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setResult(null) }} /></label>}
          <label className="block text-sm">当前管理员密码<Input required autoComplete="current-password" type="password" value={currentPassword} disabled={busy} onChange={(event) => setCurrentPassword(event.target.value)} /></label>
          <label className="block text-sm">备份密码<Input required autoComplete={operation === 'backup' ? 'new-password' : 'off'} type="password" minLength={operation === 'backup' ? 12 : 1} value={backupPassword} disabled={busy} onChange={(event) => setBackupPassword(event.target.value)} /></label>
          {operation === 'backup' && <label className="block text-sm">再次输入备份密码<Input required autoComplete="new-password" type="password" value={confirmation} disabled={busy} onChange={(event) => setConfirmation(event.target.value)} /></label>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" disabled={busy} onClick={close}>取消</Button>
            <Button type="submit" disabled={busy || !currentPassword || !backupPassword || (operation === 'backup' ? backupPassword.length < 12 || backupPassword !== confirmation : !file)}>{busy ? '处理中…' : operation === 'backup' ? '验证并下载' : '验证并暂存'}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  </Surface>
}
