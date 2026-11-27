import { CloudUpload, RefreshCw, WifiOff } from 'lucide-react'

import { useNetworkQuality } from '@/lib/network-quality'
import { offlineOutbox, useOfflineOutbox } from '@/lib/offline-outbox'

/** Persistent global state; surfaces offline and pending outbox sync. */
export function NetworkStatus() {
  const { tier } = useNetworkQuality()
  const outbox = useOfflineOutbox()
  const hasOutbox = outbox.pendingCount > 0 || outbox.failedCount > 0

  if (tier === 'offline') {
    return (
      <div className="network-status network-status--offline" role="status" aria-live="polite">
        <WifiOff size={16} aria-hidden="true" />
        <span>
          网络已断开，已保留本地缓存
          {outbox.pendingCount > 0 ? `（${outbox.pendingCount} 项更改待恢复后自动同步）` : '；恢复后将自动同步。'}
        </span>
      </div>
    )
  }

  if (hasOutbox) {
    return (
      <div className="network-status network-status--sync" role="status" aria-live="polite">
        <CloudUpload size={16} aria-hidden="true" />
        <span>
          {outbox.syncing
            ? `正在同步 ${outbox.pendingCount} 项离线更改…`
            : outbox.failedCount > 0
              ? `有 ${outbox.failedCount} 项离线更改未能自动同步`
              : `${outbox.pendingCount} 项更改等待同步`}
        </span>
        {outbox.failedCount > 0 && !outbox.syncing ? (
          <span className="network-status__actions">
            <button
              type="button"
              className="network-status__action"
              onClick={() => offlineOutbox.retryAll()}
            >
              <RefreshCw size={12} aria-hidden="true" />
              重试
            </button>
            <button
              type="button"
              className="network-status__action"
              onClick={() => offlineOutbox.clearFailed()}
            >
              忽略
            </button>
          </span>
        ) : null}
      </div>
    )
  }

  return null
}
