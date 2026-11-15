import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'

export function useWorkspaceRailResize(options: {
  width: number; sidebarWidth: number; disabled: boolean
  setWidth: (width: number) => void
  collapse: () => void
}) {
  const { width, sidebarWidth, disabled, setWidth, collapse } = options
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth)
  const minimum = 280
  const maximum = Math.max(minimum, viewportWidth - sidebarWidth - 360)
  const cleanupRef = useRef<(() => void) | null>(null)
  useEffect(() => {
    const resize = () => setViewportWidth(window.innerWidth)
    window.addEventListener('resize', resize)
    return () => { window.removeEventListener('resize', resize); cleanupRef.current?.() }
  }, [])
  useEffect(() => { if (disabled) cleanupRef.current?.() }, [disabled])
  const begin = useCallback((event: PointerEvent<HTMLDivElement>) => {
    if (disabled || (event.pointerType === 'mouse' && event.button !== 0)) return
    cleanupRef.current?.()
    event.preventDefault()
    const before = width
    const pointerId = event.pointerId
    let rawWidth = width
    const previousUserSelect = document.body.style.userSelect
    document.body.style.userSelect = 'none'
    document.body.classList.add('is-resizing')
    const cleanup = () => {
      document.body.style.userSelect = previousUserSelect
      document.body.classList.remove('is-resizing')
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', end)
      window.removeEventListener('pointercancel', cancel)
      window.removeEventListener('keydown', key)
      window.removeEventListener('blur', cancel)
      cleanupRef.current = null
    }
    const move = (native: globalThis.PointerEvent) => {
      if (native.pointerId !== pointerId) return
      rawWidth = window.innerWidth - native.clientX
      setWidth(Math.min(maximum, Math.max(minimum, rawWidth)))
    }
    const end = (native: globalThis.PointerEvent) => {
      if (native.pointerId !== pointerId) return
      cleanup()
      if (rawWidth < minimum * 0.65) { setWidth(before); collapse() }
    }
    const cancel = () => { cleanup(); setWidth(before) }
    const key = (native: globalThis.KeyboardEvent) => {
      if (native.key === 'Escape') { native.preventDefault(); native.stopImmediatePropagation(); cancel() }
    }
    cleanupRef.current = cleanup
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', end)
    window.addEventListener('pointercancel', cancel)
    window.addEventListener('keydown', key)
    window.addEventListener('blur', cancel)
  }, [disabled, width, maximum, setWidth, collapse])
  const onKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return
    let next = width
    const step = event.shiftKey ? 48 : 16
    if (event.key === 'ArrowLeft') next += step
    else if (event.key === 'ArrowRight') next -= step
    else if (event.key === 'Home') next = minimum
    else if (event.key === 'End') next = maximum
    else return
    event.preventDefault()
    setWidth(Math.max(minimum, Math.min(maximum, next)))
  }, [disabled, width, minimum, maximum, setWidth])
  return { begin, onKeyDown, minimum, maximum, value: Math.min(maximum, Math.max(minimum, width)) }
}
