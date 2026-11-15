import { useEffect, type RefObject } from 'react'

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])'
const PORTAL = '[data-radix-popper-content-wrapper],[data-slot="dialog-content"],[data-slot="alert-dialog-content"],[data-slot="select-content"]'

/** Keep the preheated, draggable drawer mounted while enforcing real modal semantics. */
export function useModalDrawerFocus(panelRef: RefObject<HTMLDivElement | null>, open: boolean, close: () => void) {
  useEffect(() => {
    const panel = panelRef.current
    if (!open || !panel) return
    const previous = document.activeElement
    const restored = new Map<HTMLElement, boolean>()
    // Only inert sibling branches, not the body: nested Radix portals stay usable.
    let branch: HTMLElement = panel
    while (branch.parentElement) {
      const parent = branch.parentElement
      for (const sibling of parent.children) {
        if (!(sibling instanceof HTMLElement) || sibling === branch || sibling.matches('[data-drawer-surface], [data-radix-popper-content-wrapper], [data-radix-portal]') || sibling.querySelector(PORTAL)) continue
        if (sibling.tagName === 'SCRIPT' || sibling.tagName === 'STYLE') continue
        restored.set(sibling, Boolean(sibling.inert))
        sibling.inert = true
      }
      if (parent === document.body) break
      branch = parent
    }
    const visible = (node: HTMLElement) => {
      if (node.closest('[inert]')) return false
      for (let ancestor: HTMLElement | null = node; ancestor && ancestor !== panel; ancestor = ancestor.parentElement) {
        const style = getComputedStyle(ancestor)
        if (ancestor.hidden || style.visibility === 'hidden' || style.display === 'none') return false
      }
      return true
    }
    const focusables = () => Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(visible)
    const focusFirst = () => (focusables()[0] ?? panel).focus({ preventScroll: true })
    focusFirst()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing) return
      const active = document.activeElement as HTMLElement | null
      // A nested menu/dialog owns its keyboard lifecycle until it closes.
      if (active?.closest(PORTAL) && !panel.contains(active)) return
      if (event.key === 'Escape') { event.preventDefault(); close(); return }
      if (event.key !== 'Tab') return
      const nodes = focusables()
      const first = nodes[0]
      const last = nodes[nodes.length - 1]
      if (!first || !last) { event.preventDefault(); panel.focus(); return }
      if (event.shiftKey && (active === first || active === panel || !panel.contains(active))) {
        event.preventDefault(); last.focus()
      } else if (!event.shiftKey && (active === last || !panel.contains(active))) {
        event.preventDefault(); first.focus()
      }
    }
    const onFocus = (event: FocusEvent) => {
      const target = event.target
      if (target instanceof HTMLElement && !panel.contains(target) && !target.closest(PORTAL)) focusFirst()
    }
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('focusin', onFocus)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('focusin', onFocus)
      for (const [element, inert] of restored) element.inert = inert
      if (previous instanceof HTMLElement && previous.isConnected && !previous.closest('[inert]')) previous.focus({ preventScroll: true })
    }
  }, [panelRef, open, close])
}
