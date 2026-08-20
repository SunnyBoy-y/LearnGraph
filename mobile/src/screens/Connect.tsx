import { useState } from 'react'
import { CHANNEL_PRESETS, isNativePlatform, normalizeBaseUrl, useAppStore } from '../store'
import type { ChannelProfile } from '../store'
import { useToastStore } from '../store'

type Tab = Exclude<ChannelProfile, 'custom'> | 'custom'

const TABS: Array<{ id: Tab; label: string }> = [
  { id: 'docker', label: 'Docker 服务器' },
  { id: 'custom', label: '自定义' },
]

export default function ConnectScreen() {
  const { baseUrl, profile, setConnection } = useAppStore()
  const showToast = useToastStore((s) => s.showToast)

  const initialTab: Tab = profile === 'custom' ? 'custom' : (profile as Tab)
  const [tab, setTab] = useState<Tab>(initialTab)
  const [address, setAddress] = useState(baseUrl || '')
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null)

  const native = isNativePlatform()

  const placeholder = (() => {
    if (tab === 'docker') return 'http://192.168.1.100:18000'
    return 'https://example.com:18000'
  })()

  const presetAddress = (): string => {
    if (tab === 'docker') return `http://192.168.1.100:${CHANNEL_PRESETS.docker.port}`
    return address
  }

  const ensureAddress = (): string => {
    if (tab === 'custom') return address
    // 预设渠道：地址为空时先自动填入示例地址（可编辑），真机不允许空地址
    return address || presetAddress()
  }

  const handleTest = async () => {
    if (native && !normalizeBaseUrl(address)) {
      showToast('error', '真机上必须填写服务器地址')
      return
    }
    setTesting(true)
    setTestResult(null)
    const value = ensureAddress()
    const result = await setConnection(value, tab === 'custom' ? 'custom' : tab)
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
      showToast('error', '真机上必须填写服务器地址')
      return
    }
    const value = ensureAddress()
    const result = await setConnection(value, tab === 'custom' ? 'custom' : tab)
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
        <p className="dim">手机端 · 连接你的电脑服务 · 共享历史记录</p>
      </div>

      <div className="connect-card">
        <div className="tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`tab ${tab === t.id ? 'active' : ''}`}
              onClick={() => {
                setTab(t.id)
                setTestResult(null)
                // 切到预设渠道且地址为空时，自动填入示例地址方便修改
                if (t.id !== 'custom' && !address) {
                  setAddress(`http://192.168.1.100:${CHANNEL_PRESETS[t.id].port}`)
                }
              }}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="field">
          <label className="field-label">服务器地址</label>
          <input
            className="input"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder={placeholder}
            inputMode="url"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
          />
          {tab !== 'custom' && !address ? (
            <p className="field-hint">{CHANNEL_PRESETS[tab].hint}</p>
          ) : null}
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
