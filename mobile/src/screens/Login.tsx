import { useState } from 'react'
import { useAppStore, useToastStore } from '../store'

export default function LoginScreen() {
  const { baseUrl, login } = useAppStore()
  const showToast = useToastStore((s) => s.showToast)

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canSubmit = username.trim().length > 0 && password.length > 0 && !busy

  const handleSubmit = async () => {
    if (!canSubmit) return
    setBusy(true)
    setError(null)
    try {
      await login(username.trim(), password)
      // 成功后由 store 导航到 home
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setError(message)
      showToast('error', message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="screen login">
      <div className="connect-hero compact">
        <h1>登录</h1>
        <p className="dim">
          {baseUrl ? `服务器：${baseUrl || '开发代理'}` : ''}
        </p>
      </div>

      <div className="connect-card">
        <div className="field">
          <label className="field-label">账号</label>
          <input
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="用户名或邮箱"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            autoComplete="username"
          />
        </div>
        <div className="field">
          <label className="field-label">密码</label>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="密码"
            autoComplete="current-password"
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleSubmit()
            }}
          />
        </div>

        {error ? <div className="test-result fail">{error}</div> : null}

        <button
          type="button"
          className="btn primary block"
          disabled={!canSubmit}
          onClick={handleSubmit}
        >
          {busy ? '登录中…' : '登录'}
        </button>

        <button
          type="button"
          className="btn ghost block"
          onClick={() => useAppStore.setState({ screen: 'connect' })}
        >
          ← 返回连接设置
        </button>
      </div>
    </div>
  )
}
