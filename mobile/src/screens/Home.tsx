import { useEffect, useRef, useState } from 'react'
import { formatRelativeTime, useAppStore, useToastStore } from '../store'
import type { Session } from '../types'

export default function HomeScreen() {
  const {
    sessions,
    sessionsLoading,
    sessionsError,
    baseUrl,
    displayName,
    username,
    loadSessions,
    createNewSession,
    deleteSession,
    openSession,
    logout,
  } = useAppStore()
  const showToast = useToastStore((s) => s.showToast)

  const [menuOpen, setMenuOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    void loadSessions()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [])

  const handleNew = async () => {
    try {
      const session = await createNewSession()
      if (session) openSession(session)
    } catch (err) {
      showToast('error', err instanceof Error ? err.message : String(err))
    }
  }

  const handleDelete = async (session: Session) => {
    if (deleting) return
    setDeleting(true)
    const ok = window.confirm(`删除会话「${session.title}」？该操作不可恢复。`)
    setDeleting(false)
    if (!ok) return
    await deleteSession(session.id)
  }

  const handleLogout = async () => {
    setMenuOpen(false)
    await logout()
  }

  return (
    <div className="screen home">
      <header className="topbar">
        <div className="topbar-title">
          <div className="connect-logo small">LG</div>
          <div>
            <div className="topbar-name">LearnGraph</div>
            <div className="topbar-sub dim">{baseUrl || '开发代理'}</div>
          </div>
        </div>
        <div className="topbar-actions">
          <button type="button" className="icon-btn" aria-label="新建会话" onClick={handleNew}>
            <span className="icon-plus">＋</span>
          </button>
          <div className="menu-wrap" ref={menuRef}>
            <button
              type="button"
              className="icon-btn"
              aria-label="菜单"
              onClick={() => setMenuOpen((v) => !v)}
            >
              <span className="icon-dots">⋮</span>
            </button>
            {menuOpen ? (
              <div className="menu">
                <div className="menu-user dim">
                  {displayName || username || '未登录'}
                </div>
                <button type="button" className="menu-item" onClick={handleNew}>
                  新建会话
                </button>
                <button type="button" className="menu-item" onClick={() => void loadSessions()}>
                  刷新会话
                </button>
                <button
                  type="button"
                  className="menu-item"
                  onClick={() => useAppStore.setState({ screen: 'connect' })}
                >
                  切换服务器
                </button>
                <button type="button" className="menu-item danger" onClick={() => void handleLogout()}>
                  退出登录
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </header>

      <div className="session-list">
        {sessionsLoading && sessions.length === 0 ? (
          <div className="list-state dim">加载中…</div>
        ) : null}
        {!sessionsLoading && sessionsError ? (
          <div className="list-state">
            <div className="test-result fail">{sessionsError}</div>
            <button type="button" className="btn ghost" onClick={() => void loadSessions()}>
              重试
            </button>
          </div>
        ) : null}
        {!sessionsLoading && !sessionsError && sessions.length === 0 ? (
          <div className="list-state dim">
            <p>还没有会话</p>
            <button type="button" className="btn primary" onClick={handleNew}>
              开始第一个会话
            </button>
          </div>
        ) : null}

        {sessions.map((session) => (
          <button
            type="button"
            key={session.id}
            className="session-item"
            onClick={() => openSession(session)}
            onContextMenu={(e) => {
              e.preventDefault()
              void handleDelete(session)
            }}
          >
            <div className="session-item-main">
              <div className="session-item-title">{session.title}</div>
              {session.activity_summary ? (
                <div className="session-item-summary">{session.activity_summary}</div>
              ) : null}
            </div>
            <div className="session-item-meta">
              <span className="session-item-time dim">
                {formatRelativeTime(session.updated_at)}
              </span>
              <button
                type="button"
                className="session-item-del"
                aria-label="删除"
                onClick={(e) => {
                  e.stopPropagation()
                  void handleDelete(session)
                }}
              >
                🗑
              </button>
            </div>
          </button>
        ))}
      </div>

      <p className="connect-foot dim">长按会话可删除 · 手机端不提供沙箱，重活都在电脑上</p>
    </div>
  )
}
