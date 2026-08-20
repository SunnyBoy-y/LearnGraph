import { useState } from 'react'
import { isNativePlatform, normalizeBaseUrl, useAppStore } from '../store'
import { useToastStore } from '../store'

/**
 * 连接配置（单一通道）：Docker 服务器默认 18000。
 * 产品概念已取消「桌面版」渠道——APK 只连 Docker 部署的服务，网页版内含全部功能。
 */
export default function ConnectScreen() {
  const { baseUrl, setConnection } = useAppStore()
  const showToast = useToastStore((s) => s.showToast)

  const [address, setAddress] = useState(baseUrl || '')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null)

  const native = isNativePlatform()

  const handleTest = async () => {
    if (native && !normalizeBaseUrl(address)) {
      showToast('error', '请填写服务器地址')
      return
    }
    setTesting(true)
    setTestResult(null)
    const result = await setConnection(address)
    setTesting(false)
    if (result.ok) {
      const info = result.info
      const extra = info
        ? ` · ${info.deployment_profile}${info.single_user ? ' · 单用户' : ''}${info.sandbox_enabled ? '' : ' · 沙箱关闭'}`
        : ''
      setTestResult({ ok: true, text: `✓ 已连接${extra}` })
    } else {
      setTestResult({ ok: false, text: `✕ ${result.error ?? '连接失败'}` })
    }
  }

  const handleContinue = async () => {
    if (native && !normalizeBaseUrl(address)) {
      showToast('error', '请填写服务器地址')
      return
    }
    const result = await setConnection(address)
    if (result.ok) {
      useAppStore.setState({ screen: 'webview' })
    } else {
      showToast('error', result.error ?? '连接失败')
    }
  }

  return (
    <div className="screen connect">
      <div className="connect-hero">
        <div className="connect-logo">LG</div>
        <h1>LearnGraph</h1>
        <p className="dim">手机端 · 连接你的服务器 · 共享历史记录</p>
      </div>

      <div className="connect-card">
        <div className="field">
          <label className="field-label">服务器地址</label>
          <input
            className="input"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="http://192.168.1.100:18000"
            inputMode="url"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
          />
          <p className="field-hint">
            Docker 服务入口（默认端口 18000，含网页版全部功能）
          </p>
          {!native && !address ? (
            <p className="field-hint dim">浏览器开发态可留空（走 Vite 代理到本机 18000）</p>
          ) : null}
        </div>

        {testResult ? (
          <div className={`test-result ${testResult.ok ? 'ok' : 'fail'}`}>{testResult.text}</div>
        ) : null}

        <div className="connect-actions">
          <button type="button" className="btn ghost" onClick={handleTest} disabled={testing}>
            {testing ? '测试中…' : '测试连接'}
          </button>
          <button type="button" className="btn primary" onClick={handleContinue}>
            打开网页版
          </button>
        </div>
      </div>

      <p className="connect-foot dim">
        网页版包含全部功能（聊天 / 图谱 / 目标 / 记忆 / 设置等），在网页版内用账号密码登录；
        左侧浮钮可设置「新回复通知 + 震动提醒」或切换服务器
      </p>
    </div>
  )
}
