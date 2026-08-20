import { useEffect, useState } from 'react'
import { useAppStore, useToastStore } from './store'
import ConnectScreen from './screens/Connect'
import WebViewScreen from './screens/WebViewScreen'

/**
 * 应用流程：连接配置 → WebView 打开网页版（全功能）。
 * 原生浮钮（MainActivity）可导航回本页（?from=webapp）以切换服务器。
 */
export default function App() {
  const hydrated = useAppStore((s) => s.hydrated)
  const screen = useAppStore((s) => s.screen)
  const baseUrl = useAppStore((s) => s.baseUrl)
  const hydrate = useAppStore((s) => s.hydrate)
  const toast = useToastStore((s) => s.toast)
  const clearToast = useToastStore((s) => s.clearToast)

  const [justReturned] = useState(
    () => new URLSearchParams(window.location.search).get('from') === 'webapp',
  )

  useEffect(() => {
    void hydrate()
  }, [hydrate])

  // 首次启动（非从网页版返回）且已有连接地址 → 直接进入网页版
  useEffect(() => {
    if (hydrated && !justReturned && baseUrl && screen === 'connect') {
      useAppStore.setState({ screen: 'webview' })
    }
  }, [hydrated, justReturned, baseUrl, screen])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(clearToast, 3200)
    return () => window.clearTimeout(timer)
  }, [toast, clearToast])

  if (!hydrated) {
    return (
      <div className="screen splash">
        <div className="connect-logo">LG</div>
        <p className="dim">加载中…</p>
      </div>
    )
  }

  return (
    <>
      {screen === 'connect' ? <ConnectScreen /> : null}
      {screen === 'webview' ? <WebViewScreen /> : null}

      {toast ? (
        <div className={`toast ${toast.kind}`} role="status">
          {toast.text}
        </div>
      ) : null}
    </>
  )
}
