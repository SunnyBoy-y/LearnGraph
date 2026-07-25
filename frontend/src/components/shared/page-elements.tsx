import type { ComponentProps, ReactNode } from 'react'
import { motion } from 'motion/react'
import { AlertCircle, CheckCircle2, CircleDashed, LoaderCircle } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

export function PageFrame({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <motion.div
      animate={{ opacity: 1, y: 0 }}
      className={cn('mx-auto flex w-full max-w-[1180px] flex-col gap-5 px-5 pb-32 pt-5 sm:px-7', className)}
      initial={{ opacity: 0, y: 8 }}
      transition={{ duration: 0.24, ease: [0.2, 0.8, 0.2, 1] }}
    >
      {children}
    </motion.div>
  )
}

export function PageIntro({
  title,
  description,
  eyebrow,
  actions,
}: {
  title: string
  description?: string
  eyebrow?: string
  actions?: ReactNode
}) {
  return (
    <header className="flex flex-col gap-4 border-b pb-5 lg:flex-row lg:items-end lg:justify-between">
      <div className="min-w-0">
        {eyebrow ? <p className="mb-1 text-xs font-semibold uppercase tracking-[0.18em] text-primary">{eyebrow}</p> : null}
        <h1 className="break-words text-balance text-2xl font-semibold tracking-tight sm:text-[1.7rem]">{title}</h1>
        {description ? <p className="mt-1.5 max-w-3xl text-sm leading-6 text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  )
}

export function Surface({ className, ...props }: ComponentProps<'section'>) {
  return <section className={cn('surface', className)} {...props} />
}

export function SectionHeading({
  title,
  description,
  action,
  className,
}: {
  title: string
  description?: string
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex items-start justify-between gap-4', className)}>
      <div>
        <h2 className="text-base font-semibold tracking-tight">{title}</h2>
        {description ? <p className="mt-1 text-sm leading-5 text-muted-foreground">{description}</p> : null}
      </div>
      {action}
    </div>
  )
}

export type Metric = {
  label: string
  value: string | number
  hint?: string
  tone?: 'default' | 'positive' | 'warning' | 'danger' | 'info'
}

const metricTone: Record<NonNullable<Metric['tone']>, string> = {
  default: 'text-foreground',
  positive: 'text-primary',
  warning: 'text-amber-600 dark:text-amber-400',
  danger: 'text-destructive',
  info: 'text-blue-600 dark:text-blue-400',
}

export function MetricStrip({ items }: { items: Metric[] }) {
  return (
    <div className="grid overflow-hidden rounded-2xl border bg-card sm:grid-cols-2 xl:grid-cols-4">
      {items.map((item, index) => (
        <div className={cn('min-w-0 px-5 py-4', index > 0 && 'border-t sm:border-l sm:border-t-0')} key={item.label}>
          <p className="text-xs font-medium text-muted-foreground">{item.label}</p>
          <p className={cn('mt-1 text-2xl font-semibold tabular-nums tracking-tight', metricTone[item.tone ?? 'default'])}>{item.value}</p>
          {item.hint ? <p className="mt-0.5 truncate text-xs text-muted-foreground">{item.hint}</p> : null}
        </div>
      ))}
    </div>
  )
}

const statusTone: Record<string, string> = {
  available: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300',
  enabled: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300',
  healthy: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300',
  approved: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300',
  fresh: 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300',
  due: 'border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300',
  failed: 'border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300',
  conflicted: 'border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300',
  pending: 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300',
  reviewing: 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300',
  degraded: 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300',
  unavailable: 'border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300',
  local_mock: 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300',
  local_rule_based: 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300',
}

const statusLabel: Record<string, string> = {
  available: '可用',
  healthy: '健康',
  healthy_local: '本地健康',
  enabled: '已启用',
  enabled_unverified: '已启用 · 未探测',
  configured_disabled: '已配置 · 未启用',
  unconfigured: '未配置',
  disabled: '已停用',
  approved: '已审核',
  fresh: '新鲜',
  pending: '待处理',
  reviewing: '审核中',
  failed: '失败',
  degraded: '降级',
  unavailable: '不可用',
  cancelled: '已取消',
  completed: '已完成',
}

export function StatePill({ status, label }: { status: string; label?: string }) {
  return (
    <Badge className={cn('font-mono text-[10px] font-medium', statusTone[status] ?? 'border-border bg-muted text-muted-foreground')} variant="outline">
      {label ?? statusLabel[status] ?? status}
    </Badge>
  )
}

export function GrowthStars({ value, max = 5, compact = false }: { value: number; max?: number; compact?: boolean }) {
  const safeValue = Math.max(0, Math.min(max, value))
  return (
    <span aria-label={`${safeValue}/${max} 星`} className={cn('font-mono tracking-[0.08em] text-primary', compact ? 'text-xs' : 'text-sm')}>
      {'★'.repeat(safeValue)}<span className="text-border">{'☆'.repeat(max - safeValue)}</span>
    </span>
  )
}

export function LoadingState({ label = '正在读取工作区数据…' }: { label?: string }) {
  return (
    <div className="surface flex min-h-48 flex-col items-center justify-center gap-3 p-8 text-sm text-muted-foreground" role="status">
      <LoaderCircle className="size-5 animate-spin text-primary" />
      <span>{label}</span>
      <div className="mt-2 grid w-full max-w-lg gap-2">
        <Skeleton className="h-4 w-3/4" /><Skeleton className="h-4 w-full" /><Skeleton className="h-4 w-1/2" />
      </div>
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="surface flex min-h-44 flex-col items-center justify-center gap-3 p-8 text-center" role="alert">
      <AlertCircle className="size-6 text-destructive" />
      <div><p className="font-medium">暂时无法加载</p><p className="mt-1 max-w-xl text-sm text-muted-foreground">{message}</p></div>
      {onRetry ? <Button onClick={onRetry} size="sm" variant="outline">重试</Button> : null}
    </div>
  )
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="flex min-h-44 flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-8 text-center">
      <CircleDashed className="size-6 text-muted-foreground" />
      <div><p className="font-medium">{title}</p><p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p></div>
      {action}
    </div>
  )
}

export function KeyValueGrid({ items }: { items: Array<{ label: string; value: ReactNode }> }) {
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
      {items.map((item) => (
        <div className="grid grid-cols-[7rem_1fr] gap-3 border-b py-2 text-sm last:border-b-0" key={item.label}>
          <dt className="text-muted-foreground">{item.label}</dt><dd className="min-w-0 font-medium">{item.value}</dd>
        </div>
      ))}
    </dl>
  )
}

export function Timeline({ items }: { items: Array<{ time?: string; title: string; detail?: string; status?: string }> }) {
  return (
    <ol className="relative ml-2 border-l">
      {items.map((item, index) => (
        <li className="relative pb-6 pl-6 last:pb-0" key={`${item.title}-${index}`}>
          <span className={cn('absolute -left-1.5 top-1.5 size-3 rounded-full border-2 border-card bg-muted-foreground', item.status === 'done' && 'bg-primary', item.status === 'pending' && 'bg-amber-500')} />
          <div className="flex items-baseline gap-2"><p className="text-sm font-medium">{item.title}</p>{item.time ? <time className="font-mono text-[11px] text-muted-foreground">{item.time}</time> : null}</div>
          {item.detail ? <p className="mt-1 text-xs leading-5 text-muted-foreground">{item.detail}</p> : null}
        </li>
      ))}
    </ol>
  )
}

export function SuccessNotice({ children }: { children: ReactNode }) {
  return <div className="flex items-start gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/35 dark:text-emerald-200"><CheckCircle2 className="mt-0.5 size-4 shrink-0" />{children}</div>
}
