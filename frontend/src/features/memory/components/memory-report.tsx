import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AnimatePresence, motion } from 'motion/react'
import {
  Clock3,
  Compass,
  Lightbulb,
  MonitorSmartphone,
  Pencil,
  Sparkles,
  Target,
  Trash2,
  TrendingUp,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { getMemoryPolicy, listContextManifests } from '@/api'
import { MessageResponse } from '@/components/ai-elements/message'
import { EmptyState } from '@/components/shared/page-elements'
import { Button } from '@/components/ui/button'
import type { MemoryProfile, MemoryProfileDimension } from '@/types/memory'
import { buildMemoryReportSections, memoryRefreshFailure, memoryUpdatedHint } from '../memory-display'

export type MemoryReportSelection = { text: string; atomIds: string[] }

const SECTION_ICONS: Record<string, LucideIcon> = {
  'stage-review': Clock3,
  direction: Compass,
  'how-you-learn': Lightbulb,
  strengths: Sparkles,
  growth: Target,
  environment: MonitorSmartphone,
  'recent-state': TrendingUp,
}

const PARAGRAPH_CLASS =
  'selection:bg-primary/20 text-[15px] leading-7 text-foreground/88'

const SECTION_GAP_CLASS = 'border-t pt-9'

/**
 * “记忆最近怎样帮助了你”：只统计真实写入的 Context Manifest 回执。
 * 任何一条数字都能在高级诊断里逐条核对；取不到真实数据时整块不渲染。
 */
function useMemoryUsageSummary() {
  const receipts = useQuery({
    queryKey: ['memory-context-manifests', ''],
    queryFn: () => listContextManifests({}),
    retry: false,
    staleTime: 60_000,
  })

  return useMemo(() => {
    const rows = receipts.data
    if (!rows?.length) return null
    const since = Date.now() - 7 * 24 * 60 * 60 * 1000
    const recent = rows.filter((row) => {
      const timestamp = new Date(row.created_at).getTime()
      return Number.isFinite(timestamp) && timestamp >= since && row.injected_ids.length > 0
    })
    if (!recent.length) return null
    return {
      conversations: recent.length,
      injected: recent.reduce((total, row) => total + row.injected_ids.length, 0),
    }
  }, [receipts.data])
}

function ReportBodyParagraphs({ dimensions }: { dimensions: MemoryProfileDimension[] }) {
  return (
    <div className="space-y-3.5">
      {dimensions.map((dimension, dimensionIndex) =>
        dimension.paragraphs.map((paragraph, paragraphIndex) => (
          <p
            className={PARAGRAPH_CLASS}
            data-memory-atom-ids={paragraph.atom_ids.join(',')}
            key={paragraph.id ?? `${dimension.key}-${dimensionIndex}-${paragraphIndex}`}
          >
            {paragraph.text}
          </p>
        )),
      )}
    </div>
  )
}

export function MemoryReport({
  busy,
  legacyCount,
  pendingCount,
  profile,
  onForgetSelection,
  onMigrate,
  onOpenPending,
  onRefresh,
  onRequestComposer,
  onSelectionChange,
}: {
  busy: boolean
  legacyCount: number
  pendingCount: number
  profile: MemoryProfile | undefined
  onForgetSelection: (selection: MemoryReportSelection) => void
  onMigrate: () => Promise<void>
  onOpenPending: () => void
  onRefresh: () => Promise<void>
  onRequestComposer: () => void
  onSelectionChange: (selection: MemoryReportSelection | null) => void
}) {
  const documentRef = useRef<HTMLDivElement>(null)
  const selectionCallback = useRef(onSelectionChange)
  // 只有报告正文自己发起的选区才会被自动收起：这样从「待确认」跳回报告页、
  // 或聚焦底部输入框导致的 selectionchange 不会清掉正在纠正的上下文。
  const ownsSelection = useRef(false)
  const [selection, setSelection] = useState<MemoryReportSelection | null>(null)
  const [anchor, setAnchor] = useState<{ top: number; left: number } | null>(null)

  useEffect(() => {
    selectionCallback.current = onSelectionChange
  }, [onSelectionChange])

  // 选区被取消（点击别处、Esc、滚动后 collapse）时同步收起纠正浮层。
  useEffect(() => {
    const handleSelectionChange = () => {
      const text = window.getSelection()?.toString().trim() ?? ''
      if (text || !ownsSelection.current) return
      ownsSelection.current = false
      setSelection(null)
      setAnchor(null)
      selectionCallback.current(null)
    }
    document.addEventListener('selectionchange', handleSelectionChange)
    return () => document.removeEventListener('selectionchange', handleSelectionChange)
  }, [])

  const policy = useQuery({
    queryKey: ['memory-policy'],
    queryFn: () => getMemoryPolicy(),
    retry: false,
    staleTime: 60_000,
  })
  const usage = useMemoryUsageSummary()

  const sections = useMemo(
    () => buildMemoryReportSections(profile?.dimensions ?? []),
    [profile?.dimensions],
  )
  const updatedHint = memoryUpdatedHint(profile?.generated_at ?? profile?.updated_at ?? null)
  const failure = memoryRefreshFailure(profile?.stale_reason)

  function clearSelection() {
    ownsSelection.current = false
    setSelection(null)
    setAnchor(null)
    selectionCallback.current(null)
  }

  /** 只收起浮层，保留交给底部输入框的“正在纠正”上下文。 */
  function hidePopover() {
    ownsSelection.current = false
    setSelection(null)
    setAnchor(null)
  }

  function captureSelection() {
    const current = window.getSelection()
    const text = current?.toString().trim() ?? ''
    const anchorNode = current?.anchorNode
    if (!text || !anchorNode || !documentRef.current?.contains(anchorNode)) {
      if (ownsSelection.current) clearSelection()
      return
    }
    const element = anchorNode instanceof Element ? anchorNode : anchorNode.parentElement
    const paragraph = element?.closest<HTMLElement>('[data-memory-atom-ids]')
    const atomIds = paragraph?.dataset.memoryAtomIds?.split(',').filter(Boolean) ?? []
    if (!atomIds.length) {
      if (ownsSelection.current) clearSelection()
      return
    }
    const range = current?.rangeCount ? current.getRangeAt(0) : null
    const host = documentRef.current.getBoundingClientRect()
    const rect = range?.getBoundingClientRect()
    const next: MemoryReportSelection = { text, atomIds }
    ownsSelection.current = true
    setSelection(next)
    if (rect && rect.width + rect.height > 0) {
      const hostWidth = host.width || 1
      const left = Math.min(Math.max(rect.left - host.left + rect.width / 2 - 132, 8), Math.max(hostWidth - 280, 8))
      // rect 与 host 同为视口坐标系，两者相减即得到浮层在报告正文内的相对位置；
      // 浮层放在选区下方，避免遮住正在阅读的那一句话。
      setAnchor({ top: Math.max(rect.bottom - host.top + 6, 0), left })
    } else {
      setAnchor(null)
    }
    selectionCallback.current(next)
  }

  const statusLabel = (() => {
    if (!profile) return null
    const enabled =
      policy.data?.effective_recall_enabled ?? policy.data?.effective_enabled ?? null
    const parts: string[] = []
    if (enabled !== null) parts.push(enabled ? '记忆已开启' : '记忆已暂停')
    if (profile.status === 'stale') parts.push('报告待更新')
    else if (profile.status === 'atomic_snapshot') parts.push('原子快照模式')
    else if (profile.status === 'ready') parts.push('报告为最新')
    else if (profile.status === 'building') parts.push('报告生成中')
    return parts.length ? parts.join(' · ') : null
  })()

  return (
    <section className="surface flex flex-col overflow-hidden p-0 shadow-sm">
      <header className="border-b px-6 pb-5 pt-6 sm:px-10">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-primary">
          <Sparkles className="size-3.5" />
          AI Mentor Report
        </div>
        <h2 className="mt-2 text-xl font-semibold tracking-tight sm:text-2xl">
          AI 导师学习报告
        </h2>
        <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <span>基于 {profile?.source_count ?? 0} 条当前有效记忆</span>
          {profile?.generated_at || profile?.updated_at ? (
            <>
              <span className="text-border">·</span>
              <span>最后更新 {new Date(profile.generated_at ?? profile.updated_at ?? '').toLocaleString('zh-CN', { hour12: false })}</span>
            </>
          ) : null}
          {updatedHint ? (
            <>
              <span className="text-border">·</span>
              <span>{updatedHint}</span>
            </>
          ) : null}
        </div>
        {statusLabel ? (
          <p className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-border/70 bg-muted/40 px-2.5 py-1 text-[11px] text-muted-foreground">
            <span className="size-1.5 rounded-full bg-primary/70" />
            {statusLabel}
          </p>
        ) : null}
      </header>

      <div className="px-6 py-7 sm:px-10 sm:py-9">
        <div className="w-full max-w-[800px]" onMouseUp={captureSelection} ref={documentRef}>
          <div className="relative">
            {selection && anchor ? (
              <div
                className="absolute z-20"
                style={{ top: anchor.top, left: anchor.left }}
              >
                <div className="flex items-center gap-0.5 rounded-full border bg-popover/95 px-2 py-1 shadow-md backdrop-blur">
                  <span className="pl-1 pr-1.5 text-[11px] text-muted-foreground">
                    这部分不准确？
                  </span>
                  <Button
                    onClick={() => {
                      hidePopover()
                      onRequestComposer()
                    }}
                    onMouseDown={(event) => event.preventDefault()}
                    size="xs"
                    variant="ghost"
                  >
                    <Pencil className="size-3" />修改
                  </Button>
                  <Button
                    onClick={() => {
                      const target = selection
                      clearSelection()
                      onForgetSelection(target)
                    }}
                    onMouseDown={(event) => event.preventDefault()}
                    size="xs"
                    variant="ghost"
                  >
                    <Trash2 className="size-3 text-destructive" />忘记这件事
                  </Button>
                </div>
              </div>
            ) : null}

            {profile?.status === 'stale' ? (
              <div className="mb-7 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/25 bg-amber-50/60 px-4 py-3 text-xs text-amber-800 dark:bg-amber-950/20 dark:text-amber-200">
                <span>
                  {failure
                    ? `上次刷新失败：${failure.message}${failure.at ? `（${failure.at}）` : ''}，已保留上一版报告。点右侧可再试一次。`
                    : '原子记忆已经有变化，旧报告不会注入对话。刷新后会基于当前证据重写。'}
                </span>
                <Button disabled={busy} onClick={() => void onRefresh()} size="xs" variant="outline">
                  立即刷新
                </Button>
              </div>
            ) : profile?.status === 'atomic_snapshot' ? (
              <div className="mb-7 flex items-start gap-2 rounded-xl border border-primary/20 bg-primary/[0.04] px-4 py-3 text-xs leading-5 text-muted-foreground">
                <Sparkles className="mt-0.5 size-3.5 shrink-0 text-primary" />
                <span>
                  当前为原子快照：还没有配置记忆摘要模型，先把每条记忆清晰列出。配置提取/摘要模型后，点击右上角刷新即可生成正式报告。
                </span>
              </div>
            ) : null}

            <AnimatePresence mode="wait">
              {sections.length || profile?.overview ? (
                <motion.article
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  initial={{ opacity: 0, y: 8 }}
                  key={profile?.version ?? 0}
                  transition={{ duration: 0.22, ease: 'easeOut' }}
                >
                  {profile?.overview ? (
                    <section className="rounded-2xl bg-muted/30 px-5 py-5 sm:px-6 sm:py-6">
                      <h3 className="text-[15px] font-semibold tracking-tight text-muted-foreground">
                        AI 导师眼中的你
                      </h3>
                      <blockquote className="mt-3 border-l-2 border-primary/35 pl-4 text-[16px] leading-8 text-foreground/90">
                        {profile.overview}
                      </blockquote>
                    </section>
                  ) : null}

                  <div className={profile?.overview ? 'mt-9 space-y-9' : 'space-y-9'}>
                    {sections.map((section, index) => {
                      const Icon = SECTION_ICONS[section.id]
                      return (
                        <section
                          className={index === 0 && !profile?.overview ? undefined : SECTION_GAP_CLASS}
                          key={section.id}
                        >
                          <div className="flex items-center gap-2">
                            {Icon ? <Icon className="size-4 text-muted-foreground" /> : null}
                            <h3 className="text-[17px] font-semibold tracking-tight">
                              {section.title}
                            </h3>
                          </div>
                          {section.description ? (
                            <p className="mt-1 pl-6 text-xs text-muted-foreground">
                              {section.description}
                            </p>
                          ) : null}
                          <div className="mt-3">
                            <ReportBodyParagraphs dimensions={section.dimensions} />
                          </div>
                        </section>
                      )
                    })}
                  </div>
                </motion.article>
              ) : profile?.structured_sections.length ? (
                <motion.article
                  animate={{ opacity: 1, y: 0 }}
                  className="space-y-9"
                  exit={{ opacity: 0, y: -6 }}
                  initial={{ opacity: 0, y: 8 }}
                  key={profile.version}
                  transition={{ duration: 0.22, ease: 'easeOut' }}
                >
                  {profile.structured_sections.map((section, sectionIndex) => (
                    <section
                      className={sectionIndex ? SECTION_GAP_CLASS : undefined}
                      key={`${section.heading}-${sectionIndex}`}
                    >
                      <h3 className="text-[17px] font-semibold tracking-tight">
                        {section.heading}
                      </h3>
                      <div className="mt-3 space-y-3.5">
                        {section.paragraphs.map((paragraph, paragraphIndex) => (
                          <p
                            className={PARAGRAPH_CLASS}
                            data-memory-atom-ids={paragraph.atom_ids.join(',')}
                            key={paragraph.id ?? `${sectionIndex}-${paragraphIndex}`}
                          >
                            {paragraph.text}
                          </p>
                        ))}
                      </div>
                    </section>
                  ))}
                </motion.article>
              ) : profile?.markdown ? (
                <motion.div animate={{ opacity: 1 }} initial={{ opacity: 0 }} key={profile.version}>
                  <MessageResponse className="text-[15px] leading-7 text-foreground/88">
                    {profile.markdown}
                  </MessageResponse>
                </motion.div>
              ) : (
                <motion.div animate={{ opacity: 1 }} initial={{ opacity: 0 }}>
                  <EmptyState
                    description="在下方输入框告诉 AI 导师值得长期记住的事实、偏好或变化。内容会先被整理为可追溯的原子，再写进这份报告。"
                    title="还没有可以生成报告的原子记忆"
                  />
                  {legacyCount ? (
                    <div className="mt-4 flex justify-center">
                      <Button
                        disabled={busy}
                        onClick={() => void onMigrate()}
                        size="sm"
                        variant="outline"
                      >
                        <Sparkles className="size-3.5" />
                        整理 {legacyCount} 条旧记忆
                      </Button>
                    </div>
                  ) : null}
                </motion.div>
              )}
            </AnimatePresence>

            {usage ? (
              <section className="mt-9 border-t pt-7">
                <h3 className="text-[15px] font-semibold tracking-tight">记忆最近怎样帮助了你</h3>
                <p className="mt-3 text-sm leading-7 text-muted-foreground">
                  最近 7 天，记忆参与了 {usage.conversations} 次回答，累计注入 {usage.injected}{' '}
                  条记忆条目。
                </p>
                <p className="mt-1.5 text-xs text-muted-foreground">
                  逐条回执可在右上角 ··· → 高级诊断中核对。
                </p>
              </section>
            ) : null}
          </div>
        </div>
      </div>

      {pendingCount ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t bg-muted/15 px-6 py-3.5 sm:px-10">
          <p className="text-sm text-muted-foreground">
            有 {pendingCount} 条新理解等待你的确认
          </p>
          <Button onClick={onOpenPending} size="sm" variant="ghost">
            查看 →
          </Button>
        </div>
      ) : null}
    </section>
  )
}
