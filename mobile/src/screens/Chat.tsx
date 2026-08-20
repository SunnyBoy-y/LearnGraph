import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useChatStore } from '../chat-store'
import { useAppStore } from '../store'
import { LiveBubble, MessageBubble } from '../components/messages'

export default function ChatScreen() {
  const { activeSession, clearActiveSession, deleteSession } = useAppStore()
  const chat = useChatStore()

  const sessionId = activeSession?.id ?? null
  const listRef = useRef<HTMLDivElement>(null)
  const nearBottomRef = useRef(true)
  const [input, setInput] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)

  // 打开会话时加载历史
  useEffect(() => {
    if (sessionId) void chat.open(sessionId)
    return () => {
      // 离开会话不中断服务端生成；仅本地清理由 reset 完成
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  // 内容变化时保持贴底
  const contentVersion = `${chat.messages.length}:${chat.liveParts.length}:${chat.liveStatus}`
  useLayoutEffect(() => {
    const el = listRef.current
    if (el && nearBottomRef.current) {
      el.scrollTop = el.scrollHeight
    }
  }, [contentVersion])

  const onScroll = useCallback(() => {
    const el = listRef.current
    if (!el) return
    nearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 140
    if (el.scrollTop < 60 && chat.hasMoreBefore && !chat.messagesLoading) {
      void chat.loadOlder()
    }
  }, [chat])

  const handleSend = () => {
    const text = input.trim()
    if (!text) return
    setInput('')
    void chat.send(text)
  }

  const handleStop = () => {
    void chat.cancel()
  }

  const handleDelete = async () => {
    if (!activeSession || deleting) return
    setMenuOpen(false)
    setDeleting(true)
    const ok = window.confirm(`删除会话「${activeSession.title}」？该操作不可恢复。`)
    setDeleting(false)
    if (!ok) return
    const done = await deleteSession(activeSession.id)
    if (done) {
      chat.reset()
      clearActiveSession()
    }
  }

  const handleSync = () => {
    void chat.syncAfterDrop()
  }

  const title = activeSession?.title ?? '会话'
  const streaming = chat.liveStatus === 'streaming'
  const errorBanner = chat.liveStatus === 'error'

  return (
    <div className="screen chat">
      <header className="topbar">
        <button type="button" className="icon-btn" aria-label="返回" onClick={clearActiveSession}>
          <span className="icon-back">‹</span>
        </button>
        <div className="topbar-title flex-1">
          <div className="topbar-name ellipsis">{title}</div>
          <div className="topbar-sub dim">
            {streaming ? '正在生成…' : chat.liveStatus === 'done' ? '已生成' : chat.messages.length > 0 ? '已就绪' : ' '}
          </div>
        </div>
        <div className="menu-wrap">
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
              <button
                type="button"
                className="menu-item"
                onClick={() => {
                  setMenuOpen(false)
                  if (sessionId) void chat.open(sessionId)
                }}
              >
                刷新消息
              </button>
              <button type="button" className="menu-item danger" onClick={() => void handleDelete()}>
                删除会话
              </button>
            </div>
          ) : null}
        </div>
      </header>

      {errorBanner ? (
        <div className="banner error">
          <span>连接中断，生成已在电脑端继续</span>
          <button type="button" className="banner-btn" onClick={handleSync}>
            同步
          </button>
        </div>
      ) : null}

      <div className="message-list" ref={listRef} onScroll={onScroll}>
        {chat.messagesLoading && chat.messages.length === 0 ? (
          <div className="list-state dim">加载中…</div>
        ) : null}
        {!chat.messagesLoading && chat.messagesError ? (
          <div className="list-state">
            <div className="test-result fail">{chat.messagesError}</div>
            <button
              type="button"
              className="btn ghost"
              onClick={() => sessionId && void chat.open(sessionId)}
            >
              重试
            </button>
          </div>
        ) : null}
        {chat.hasMoreBefore ? (
          <div className="list-state dim small">上滑加载更早消息…</div>
        ) : null}

        {chat.messages.map((m) => (
          <MessageBubble key={m.id} message={m} />
        ))}

        {chat.liveMessageId || (streaming && chat.liveParts.length > 0) ? (
          <LiveBubble parts={chat.liveParts} />
        ) : null}

        {chat.messages.length === 0 &&
        !chat.messagesLoading &&
        !chat.messagesError &&
        !chat.liveMessageId ? (
          <div className="list-state dim">
            <p>发送一条消息开始对话</p>
            <p className="small">消息通过电脑端服务生成，历史记录与电脑共享</p>
          </div>
        ) : null}
      </div>

      <div className="composer">
        <textarea
          className="composer-input"
          rows={1}
          value={input}
          placeholder="输入消息…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSend()
            }
          }}
        />
        {streaming ? (
          <button type="button" className="btn danger composer-btn" onClick={handleStop}>
            停止
          </button>
        ) : (
          <button
            type="button"
            className="btn primary composer-btn"
            disabled={!input.trim()}
            onClick={handleSend}
          >
            发送
          </button>
        )}
      </div>
    </div>
  )
}
