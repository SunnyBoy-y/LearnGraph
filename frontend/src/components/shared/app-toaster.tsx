import { useEffect, useRef, useSyncExternalStore, type CSSProperties } from 'react'
import { mountNotificationSurface, setNotificationFocus } from '@/lib/notifications'
import { Check, CircleAlert, Info, X } from 'lucide-react'
import { Toaster } from 'sonner'
import { NetworkStatus } from './network-status'
import '@/styles/notifications.css'

function subscribeTheme(onChange: () => void) {
  const observer = new MutationObserver(onChange)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'dir', 'data-color-mode'] })
  return () => observer.disconnect()
}

function getTheme(): 'dark' | 'light' {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
}

/** One notification surface shared by the web app and native web renderer. */
export function AppToaster() {
  const theme = useSyncExternalStore(subscribeTheme, getTheme, () => 'light' as const)
  useEffect(mountNotificationSurface, [])
  const surface = useRef<HTMLElement>(null)
  useEffect(() => {
    const element = surface.current
    if (!element) return
    let returnFocus: HTMLElement | null = null
    const focus = (event: FocusEvent) => {
      if (event.relatedTarget instanceof HTMLElement && !element.contains(event.relatedTarget)) returnFocus = event.relatedTarget
      setNotificationFocus(true)
    }
    const blur = (event: FocusEvent) => {
      if (!(event.relatedTarget instanceof Node) || !element.contains(event.relatedTarget)) setNotificationFocus(false)
    }
    const keydown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.isComposing) return
      const target = event.target instanceof Element ? event.target : null
      const notice = target?.closest('[data-sonner-toast]') ?? element.querySelector('[data-sonner-toast]')
      const close = notice?.querySelector<HTMLButtonElement>('[data-close-button]')
      event.preventDefault()
      event.stopPropagation() // Never close an unrelated dialog underneath the notification.
      close?.click()
      if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true })
    }
    element.addEventListener('focusin', focus)
    element.addEventListener('focusout', blur)
    element.addEventListener('keydown', keydown)
    return () => {
      element.removeEventListener('focusin', focus)
      element.removeEventListener('focusout', blur)
      element.removeEventListener('keydown', keydown)
      setNotificationFocus(false)
    }
  }, [])
  return (
    <>
    <NetworkStatus />
    <Toaster
      ref={surface}
      className="lg-toaster"
      closeButton
      expand
      visibleToasts={2}
      position="top-right"
      theme={theme}
      richColors={false}
      gap={12}
      offset={{ top: 'var(--notification-top, 72px)', right: 'max(24px, env(safe-area-inset-right))' }}
      mobileOffset={{ top: 'max(16px, env(safe-area-inset-top))', right: 16, left: 16 }}
      style={{ '--width': '380px' } as CSSProperties}
      swipeDirections={[]}
      containerAriaLabel="通知"
      icons={{ success: <Check size={18} />, info: <Info size={18} />, warning: <CircleAlert size={18} />, error: <CircleAlert size={18} />, close: <X size={14} /> }}
      toastOptions={{ closeButtonAriaLabel: '关闭通知', duration: 3000 }}
    />
    </>
  )
}
