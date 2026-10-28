import { shouldIgnoreDrawerSwipe } from '@/lib/mobile-shell'
import type { NativeRendererBridge } from './protocol'

/** Same 8 px / 1.2 direction lock as mobile-shell. */
export function drawerAxis(dx: number, dy: number): 'pending' | 'horizontal' | 'dead' {
  if (Math.abs(dy) >= 8 && Math.abs(dy) >= Math.abs(dx)) return 'dead'
  if (Math.abs(dx) < 8 || Math.abs(dx) < Math.abs(dy) * 1.2) return 'pending'
  return 'horizontal'
}

export function rendererDrawerSide(dx: number, surface: 'message' | 'graph'): 'left' | 'right' {
  return surface === 'graph' ? 'right' : dx > 0 ? 'left' : 'right'
}

export function installRendererDrawer(bridge: NativeRendererBridge, hasOverlay: () => boolean, surface: () => 'message' | 'graph' = () => 'message'): () => void {
  if (!bridge.drawer) return () => {}
  type Track = { identifier: number; x: number; y: number; target: Element; axis: 'pending' | 'horizontal' | 'dead'; side: 'left' | 'right'; dx: number; dy: number }
  let track: Track | null = null
  const find = (touches: TouchList, id: number) => Array.from(touches).find(touch => touch.identifier === id)
  const start = (event: TouchEvent) => {
    if (event.touches.length !== 1 || hasOverlay()) return
    if (track && find(event.touches, track.identifier)) return
    const touch = event.touches[0]
    if (!touch || !(event.target instanceof Element)) return
    track = { identifier: touch.identifier, x: touch.clientX, y: touch.clientY, target: event.target, axis: 'pending', side: 'left', dx: 0, dy: 0 }
  }
  const move = (event: TouchEvent) => {
    if (!track) return
    const touch = find(event.touches, track.identifier)
    if (!touch) return
    track.dx = touch.clientX - track.x
    track.dy = touch.clientY - track.y
    if (track.axis === 'pending') {
      const axis = drawerAxis(track.dx, track.dy)
      if (axis !== 'horizontal') { track.axis = axis; return }
      const selection = window.getSelection()
      if (hasOverlay() || document.body.hasAttribute('data-scroll-locked') || selection && !selection.isCollapsed
        || shouldIgnoreDrawerSwipe(track.target)
        || track.target.closest('iframe,canvas,[data-ui-component="chart"],[data-ui-component="graph"],.recharts-wrapper')) {
        track.axis = 'dead'
        return
      }
      track.axis = 'horizontal'
      track.side = rendererDrawerSide(track.dx, surface())
      bridge.drawer?.({ phase: 'start', side: track.side, dx: 0, dy: 0 })
    }
    if (track.axis !== 'horizontal') return
    bridge.drawer?.({ phase: 'move', side: track.side, dx: track.dx, dy: track.dy })
    if (event.cancelable) event.preventDefault()
  }
  const finish = (event: TouchEvent) => {
    if (!track || find(event.touches, track.identifier)) return
    const previous = track
    track = null
    if (previous.axis === 'horizontal') bridge.drawer?.({ phase: event.type === 'touchcancel' ? 'cancel' : 'end', side: previous.side, dx: previous.dx, dy: previous.dy })
  }
  window.addEventListener('touchstart', start, { capture: true, passive: true })
  window.addEventListener('touchmove', move, { capture: true, passive: false })
  window.addEventListener('touchend', finish, { capture: true, passive: true })
  window.addEventListener('touchcancel', finish, { capture: true, passive: true })
  return () => {
    window.removeEventListener('touchstart', start, true)
    window.removeEventListener('touchmove', move, true)
    window.removeEventListener('touchend', finish, true)
    window.removeEventListener('touchcancel', finish, true)
    if (track?.axis === 'horizontal') bridge.drawer?.({ phase: 'cancel', side: track.side, dx: track.dx, dy: track.dy })
  }
}
