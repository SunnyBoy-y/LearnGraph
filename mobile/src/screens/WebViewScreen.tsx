import { useEffect } from 'react'
import { useAppStore } from '../store'

/**
 * 网页版承载屏：把主 WebView 导航到配置的服务器地址（网页版 = 全功能 UI）。
 * 之后由原生浮钮（MainActivity）负责「返回手机助手 / 刷新 / 通知设置」。
 */
export default function WebViewScreen() {
  const baseUrl = useAppStore((s) => s.baseUrl)

  useEffect(() => {
    const target = baseUrl.trim()
    if (!target) {
      useAppStore.setState({ screen: 'connect' })
      return
    }
    window.location.assign(target)
  }, [baseUrl])

  return (
    <div className="screen splash">
      <div className="connect-logo">LG</div>
      <p className="dim">正在打开网页版（{baseUrl || ''}）…</p>
    </div>
  )
}
