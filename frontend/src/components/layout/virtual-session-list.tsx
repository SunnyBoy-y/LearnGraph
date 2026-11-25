import { Profiler, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { recordSessionListMetric } from '@/lib/session-list-performance'
import { defaultRangeExtractor, useVirtualizer } from '@tanstack/react-virtual'
import type { SessionScope } from '@/api/session-browser'
import { sessionScopeId } from '@/lib/session-queries'
import type { SessionNavigator } from './use-session-navigator'
import './virtual-session-list.css'

export function VirtualSessionList<T extends { key: string }>({ rows, renderRow, workspaceId, pinnedKeys = [] }: {
  rows: T[]; renderRow: (row: T, index: number) => ReactNode; workspaceId: string; pinnedKeys?: string[]
}) {
  const container = useRef<HTMLDivElement>(null)
  const [root, setRoot] = useState<HTMLElement | null>(null)
  const [margin, setMargin] = useState(0)
  const [focusedKey, setFocusedKey] = useState('')
  const firstVisible = useRef({ started: performance.now(), measured: false })
  useEffect(() => {
    if (typeof PerformanceObserver === 'undefined' || !PerformanceObserver.supportedEntryTypes?.includes('longtask')) return
    const observer = new PerformanceObserver(list => {
      for (const entry of list.getEntries()) recordSessionListMetric({ kind: 'page-longtask', duration: entry.duration })
    })
    observer.observe({ type: 'longtask', buffered: false })
    return () => observer.disconnect()
  }, [])
  useLayoutEffect(() => {
    if (firstVisible.current.measured || !rows.some(row => row.key.startsWith('session:'))) return
    firstVisible.current.measured = true
    recordSessionListMetric({ kind: 'first-visible', duration: performance.now() - firstVisible.current.started, count: rows.length })
  }, [rows])
  const anchor = useRef<{ key: string; offset: number; index: number } | null>(null)
  const restoration = useRef<{ desired: number; pending: boolean }>({ desired: 0, pending: false })
  useLayoutEffect(() => {
    const element = container.current?.closest<HTMLElement>('.sidebar-nav__scroll') ?? null
    setRoot(element)
    if (!element) return
    const update = () => {
      const top = container.current!.getBoundingClientRect().top - element.getBoundingClientRect().top + element.scrollTop
      setMargin(previous => Math.abs(previous - top) > 0.5 ? top : previous)
    }
    update()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update)
    observer?.observe(element)
    if (container.current?.parentElement) observer?.observe(container.current.parentElement)
    return () => observer?.disconnect()
  }, [])
  const rangeExtractor = useCallback((range: Parameters<typeof defaultRangeExtractor>[0]) => {
    const indexes = new Set(defaultRangeExtractor(range))
    for (const key of [...pinnedKeys, focusedKey]) {
      const index = rows.findIndex(row => row.key === key)
      if (index >= 0) indexes.add(index)
    }
    return [...indexes].sort((a, b) => a - b)
  }, [focusedKey, pinnedKeys, rows])
  const virtual = useVirtualizer({
    count: rows.length, getScrollElement: () => root, getItemKey: index => rows[index].key,
    estimateSize: () => 36, overscan: 8, scrollMargin: margin, rangeExtractor,
    initialRect: { width: 256, height: 640 },
    // Hidden drawers and jsdom can report zero. Zero-size rows would collapse
    // all 10,000 items into the viewport and trigger an update/measurement loop.
    measureElement: (element, entry) => entry?.borderBoxSize?.[0]?.blockSize || element.getBoundingClientRect().height || 36,
    observeElementRect: (instance, callback) => {
      const element = instance.scrollElement
      if (!element) return
      const update = () => callback({ width: element.clientWidth || 256, height: element.clientHeight || 640 })
      update()
      const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update)
      observer?.observe(element)
      return () => observer?.disconnect()
    },
  })
  // Keep a surviving visible row fixed when older pages are prepended/evicted,
  // or a mutation changes the order above the viewport.
  useLayoutEffect(() => {
    const previous = anchor.current
    if (previous && root) {
      const index = rows.findIndex(row => row.key === previous.key)
      if (index >= 0 && index !== previous.index) {
        virtual.scrollToIndex(index, { align: 'start' })
        root.scrollTop += previous.offset
      }
    }
  }, [rows, root, virtual])
  useLayoutEffect(() => {
    if (!root) return
    const remember = () => {
      const item = virtual.getVirtualItems().find(item => item.end > root.scrollTop && item.start >= margin)
      if (item) anchor.current = { key: String(item.key), index: item.index, offset: root.scrollTop - item.start }
    }
    remember()
    root.addEventListener('scroll', remember, { passive: true })
    return () => root.removeEventListener('scroll', remember)
  }, [margin, root, virtual, rows])
  useLayoutEffect(() => {
    if (!root) return
    const key = `learngraph:session-scroll:${workspaceId}`
    try {
      const desired = Number(sessionStorage.getItem(key)) || 0
      restoration.current = { desired, pending: desired > 0 }
      if (desired > 0 && root.scrollHeight - root.clientHeight >= desired) { root.scrollTop = desired; restoration.current.pending = false }
    } catch { /* private mode */ }
    let frame = 0
    const save = () => {
      if (frame || restoration.current.pending) return
      frame = requestAnimationFrame(() => { frame = 0; try { sessionStorage.setItem(key, String(root.scrollTop)) } catch { /* private mode */ } })
    }
    root.addEventListener('scroll', save, { passive: true })
    const userScroll = () => { restoration.current.pending = false }
    root.addEventListener('wheel', userScroll, { passive: true })
    root.addEventListener('touchstart', userScroll, { passive: true })
    return () => {
      root.removeEventListener('scroll', save); root.removeEventListener('wheel', userScroll); root.removeEventListener('touchstart', userScroll)
      if (frame) cancelAnimationFrame(frame)
      if (!restoration.current.pending) { try { sessionStorage.setItem(key, String(root.scrollTop)) } catch { /* private mode */ } }
    }
  }, [root, workspaceId])
  useLayoutEffect(() => {
    const state = restoration.current
    if (!root || !state.pending || root.scrollHeight - root.clientHeight < state.desired) return
    root.scrollTop = state.desired
    state.pending = false
  }, [root, rows])
  return <Profiler id="session-navigator" onRender={(_id, _phase, duration) => recordSessionListMetric({ kind: 'render', duration, count: rows.length })}><div ref={container} className="sidebar-virtual-list" data-session-virtual-list
    onFocusCapture={event => setFocusedKey(event.target.closest<HTMLElement>('[data-virtual-key]')?.dataset.virtualKey ?? '')}
    onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setFocusedKey('') }}
    onKeyDown={event => {
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key) || (event.target as HTMLElement).matches('input,textarea,[role="menuitem"]')) return
      const key = (event.target as HTMLElement).closest<HTMLElement>('[data-virtual-key]')?.dataset.virtualKey
      const current = rows.findIndex(row => row.key === key)
      if (current < 0) return
      event.preventDefault()
      const index = event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1 : Math.max(0, Math.min(rows.length - 1, current + (event.key === 'ArrowDown' ? 1 : -1)))
      virtual.scrollToIndex(index, { align: 'auto' })
      setFocusedKey(rows[index]?.key ?? '')
      requestAnimationFrame(() => container.current?.querySelector<HTMLElement>(`[data-virtual-key="${CSS.escape(rows[index].key)}"] button:not(:disabled)`)?.focus({ preventScroll: true }))
    }}>
    <div style={{ height: virtual.getTotalSize(), position: 'relative', width: '100%' }}>
      {virtual.getVirtualItems().map(item => <div key={item.key} ref={virtual.measureElement} data-index={item.index} data-virtual-key={String(item.key)}
        className="sidebar-virtual-row" style={{ transform: `translateY(${item.start - margin}px)` }}>
        {renderRow(rows[item.index], item.index)}
      </div>)}
    </div>
  </div></Profiler>
}

export function SessionPageSentinel({ scope, navigator, direction = 'next' }: {
  scope: SessionScope; navigator: SessionNavigator; direction?: 'next' | 'previous'
}) {
  const element = useRef<HTMLDivElement>(null)
  const { activate, load } = navigator
  const lastAutomatic = useRef<number | null>(null)
  const state = navigator.scopes.get(sessionScopeId(scope))
  const cursor = direction === 'next' ? state?.data?.pages.at(-1)?.next_cursor : state?.data?.pages[0]?.previous_cursor
  const hasMore = direction === 'next' ? state?.hasNextPage : state?.hasPreviousPage
  useEffect(() => {
    const target = element.current, root = target?.closest<HTMLElement>('.sidebar-nav__scroll')
    if (!target || !root) return
    const maybeLoad = () => {
      if (!state) { activate(scope); return }
      if (!hasMore || state.isFetching || state.isError) return
      if (lastAutomatic.current === root.scrollTop && root.scrollHeight > root.clientHeight + 1) return
      lastAutomatic.current = root.scrollTop
      void load(scope, direction)
    }
    if (typeof IntersectionObserver === 'undefined') {
      // Test/older webview fallback remains event-driven and frame-coalesced.
      let frame = 0
      const check = () => { if (frame) return; frame = requestAnimationFrame(() => {
        frame = 0; const a = target.getBoundingClientRect(), b = root.getBoundingClientRect()
        if (a.bottom >= b.top - 120 && a.top <= b.bottom + 120) maybeLoad()
      }) }
      check(); root.addEventListener('scroll', check, { passive: true })
      return () => { root.removeEventListener('scroll', check); if (frame) cancelAnimationFrame(frame) }
    }
    const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) maybeLoad() }, { root, rootMargin: '120px 0px' })
    observer.observe(target)
    return () => observer.disconnect()
  }, [activate, load, state, cursor, hasMore, direction, scope])
  const failed = state?.isError
  const pending = !state || state.isPending || state.isFetching
  if (direction === 'previous' && !hasMore && !failed) return null
  return <div ref={element} className="sidebar-page-sentinel" aria-live="polite" aria-busy={pending}>
    {failed ? <button type="button" onClick={() => void navigator.load(scope, direction, true)}>加载失败，点击重试</button>
      : pending ? <span role="status">加载中…</span>
      : hasMore ? <button type="button" onClick={() => void load(scope, direction)}>{direction === 'previous' ? '加载较新的会话' : '加载更多'}</button>
      : state?.data?.pages.every(page => page.items.length === 0) ? <span>{scope.archived_only ? '暂无已归档会话' : '暂无会话'}</span> : null}
  </div>
}
