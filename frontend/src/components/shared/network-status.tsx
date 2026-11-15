import { useSyncExternalStore } from 'react'
import { WifiOff } from 'lucide-react'

function subscribe(onChange: () => void) {
  window.addEventListener('online', onChange)
  window.addEventListener('offline', onChange)
  return () => { window.removeEventListener('online', onChange); window.removeEventListener('offline', onChange) }
}

/** Persistent global state; no fake reconnect progress or unsafe automatic replay. */
export function NetworkStatus() {
  const online = useSyncExternalStore(subscribe, () => navigator.onLine, () => true)
  return online ? null : (
    <div className="network-status" role="status" aria-live="polite">
      <WifiOff size={16} aria-hidden="true" />
      <span>网络已断开，请检查连接。</span>
    </div>
  )
}
