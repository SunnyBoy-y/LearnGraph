import { useEffect, useState } from 'react'
import { Check, LoaderCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { Build } from './node-learning-api'

const stages = ['教学设计', '图文教材', '实验规则', '互动小剧场', '测评试卷', '插图素材', '校验发布']

export function LearningBuildProgress({build, busy, refreshing, error, onRefresh, onControl}: {
  build: Build; busy: boolean; refreshing: boolean; error?: string
  onRefresh: () => void; onControl: (action: 'cancel' | 'retry') => void
}) {
  const active = ['queued', 'running'].includes(build.status)
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    // Older servers omit updated_at. Start the waiting indicator locally then.
    const raw = build.updated_at
    const parsed = raw ? Date.parse(/Z$|[+-]\d{2}:?\d{2}$/.test(raw) ? raw : `${raw}Z`) : NaN
    const since = Number.isFinite(parsed) ? parsed : Date.now()
    const tick = () => setElapsed(Math.max(0, Math.floor((Date.now() - since) / 1000)))
    tick()
    if (!active) return
    const timer = setInterval(tick, 1000)
    return () => clearInterval(timer)
  }, [active, build.id, build.completed_stages, build.updated_at])
  const label = ({queued: '等待后台处理', running: `正在生成${build.stage}`, ready: '学习页已准备好', failed: '本阶段未能完成', cancelled: '准备已取消', skipped: '此任务已跳过'} as Record<string, string>)[build.status] ?? build.status
  return <div className="learning-build-progress">
    <div className="learning-section-heading" role="status">
      {active && <LoaderCircle className="size-5 animate-spin" aria-hidden="true" />}
      <strong>{label}</strong><span className="learning-muted">已完成 {build.completed_stages} / {build.total_stages} 个阶段</span>
    </div>
    <progress value={build.completed_stages} max={build.total_stages} aria-label="学习页构建阶段进度" />
    <ol className="learning-build-stages" aria-label="内容准备流程">{stages.map((stage, i) => <li key={stage} data-state={i < build.completed_stages ? 'done' : i === build.completed_stages ? 'current' : 'waiting'} aria-current={active && i === build.completed_stages ? 'step' : undefined}>
      {i < build.completed_stages ? <Check className="size-3" aria-hidden="true" /> : <span>{i + 1}</span>}{stage}
    </li>)}</ol>
    {active && <p className="learning-muted">{build.status === 'queued' ? '任务已保存，等待后台执行。' : '模型正在生成内容，阶段完成后会自动更新。'}可以返回图谱，稍后再来。</p>}
    {active && elapsed >= 90 && <p role="status" className="learning-feedback">本阶段已等待 {Math.floor(elapsed / 60)} 分 {elapsed % 60} 秒，尚未收到完成结果。可以刷新状态或取消；不会自动重复提交生成。</p>}
    {(build.error || error) && <p role="alert">{error ?? build.error}</p>}
    {build.status === 'failed' && build.advice && <p className="learning-muted">{build.advice}</p>}
    {build.status === 'failed' && (build.attempts ?? 0) > 1 && <p className="learning-muted">{build.failed_item ?? build.failed_stage ?? ''}{(build.failed_item ?? build.failed_stage) ? '：' : ''}已自动尝试 {build.attempts} 次（含本地修复与重试）。</p>}
    {build.status === 'failed' && <p className="learning-muted">重试保留已完成阶段；再次调用模型可能产生费用。</p>}
    <div className="learning-actions">
      <Button size="sm" variant="outline" disabled={refreshing} onClick={onRefresh}>{refreshing ? '正在刷新…' : '刷新状态'}</Button>
      {active && <Button size="sm" variant="ghost" disabled={busy} onClick={() => onControl('cancel')}>{busy ? '正在处理…' : '取消生成'}</Button>}
      {build.status === 'failed' && <Button size="sm" disabled={busy} onClick={() => onControl('retry')}>{busy ? '正在提交…' : '重试当前阶段'}</Button>}
    </div>
  </div>
}
