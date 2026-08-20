import { useEffect, useState } from 'react'
import { useChatStore } from '../chat-store'
import { fileContent, partFileRef } from '../api/endpoints'
import { Markdown } from '../markdown'
import type { Message, MessagePart } from '../types'

// ------------------------------------------------------------------------- //
// part 分类与渲染
// ------------------------------------------------------------------------- //

const THINKING_TYPES = new Set([
  'reasoning_summary',
  'reasoning_content',
  'agent_step',
  'tool_call',
  'skill_trigger',
  'fetch_authorization',
  'egress_authorization',
  'fetch_setup_notice',
  'sandbox_status',
  'graph_progress',
])

function isThinking(part: MessagePart): boolean {
  return THINKING_TYPES.has(part.type)
}

function isBody(part: MessagePart): boolean {
  return part.type === 'text' || part.type === 'image' || part.type === 'attachment' || part.type === 'error' || part.type === 'source_list'
}

function orderedParts(parts: MessagePart[]): MessagePart[] {
  return [...parts].sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0))
}

function splitParts(parts: MessagePart[]) {
  const ordered = orderedParts(parts)
  const thinking: MessagePart[] = []
  const body: MessagePart[] = []
  for (const p of ordered) {
    if (isThinking(p)) thinking.push(p)
    else if (isBody(p)) body.push(p)
  }
  return { thinking, body }
}

function partToolName(part: MessagePart): string | null {
  const d = part.data ?? {}
  const name =
    typeof d.tool_name === 'string' ? d.tool_name : typeof d.name === 'string' ? d.name : null
  return name
}

// ------------------------------------------------------------------------- //
// 组件
// ------------------------------------------------------------------------- //

function ThinkingBlock({ parts }: { parts: MessagePart[] }) {
  const expanded = useChatStore((s) => s.thinkingExpanded)
  const setExpanded = useChatStore((s) => s.setThinkingExpanded)
  const hasContent = parts.some((p) => (p.content ?? '').trim().length > 0)
  const toolCount = parts.filter((p) => p.type === 'tool_call').length
  const stillWorking = parts.some((p) => p.status === 'streaming' || p.status === 'pending')

  const label = toolCount > 0 ? `思考过程 · ${toolCount} 次工具调用` : '思考过程'

  if (!expanded) {
    return (
      <button type="button" className="thinking-toggle" onClick={() => setExpanded(true)}>
        <span className="thinking-dot" aria-hidden="true" />
        {label}
        {stillWorking ? <span className="thinking-spin" aria-hidden="true" /> : null}
      </button>
    )
  }

  return (
    <div className="thinking">
      <button type="button" className="thinking-toggle" onClick={() => setExpanded(false)}>
        <span className="thinking-dot" aria-hidden="true" />
        {label}
        <span className="thinking-chevron">▾</span>
      </button>
      <div className="thinking-body">
        {parts.map((p) => {
          const content = (p.content ?? '').trim()
          const name = partToolName(p)
          if (p.type === 'tool_call' && name) {
            return (
              <div key={p.id} className="thinking-tool">
                <span className="thinking-tool-icon">🔧</span>
                <span className="thinking-tool-name">{name}</span>
                {p.status === 'completed' ? <span className="thinking-tool-ok">✓</span> : null}
                {p.status === 'failed' ? <span className="thinking-tool-fail">✕</span> : null}
              </div>
            )
          }
          if (p.type === 'agent_step' || p.type === 'skill_trigger') {
            return (
              <div key={p.id} className="thinking-tool">
                <span className="thinking-tool-icon">⚙️</span>
                <span className="thinking-tool-name">{content || name || '步骤'}</span>
              </div>
            )
          }
          if (!content) return null
          if (p.type === 'fetch_authorization' || p.type === 'egress_authorization') {
            return (
              <div key={p.id} className="thinking-note">
                🔒 {content}
              </div>
            )
          }
          return (
            <div key={p.id} className="thinking-text">
              {content}
            </div>
          )
        })}
        {hasContent ? null : <div className="thinking-text dim">（正在思考…）</div>}
      </div>
    </div>
  )
}

function ImagePart({ part }: { part: MessagePart }) {
  const ref = partFileRef(part)
  const [src, setSrc] = useState<string | null>(ref.url ?? null)

  useEffect(() => {
    if (src || !ref.fileId) return
    let revoked: string | null = null
    let cancelled = false
    void fileContent(ref.fileId as string)
      .then((blob) => {
        if (cancelled) return
        revoked = URL.createObjectURL(blob)
        setSrc(revoked)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
      if (revoked) URL.revokeObjectURL(revoked)
    }
  }, [ref.fileId, src])

  if (!src) {
    return <div className="attachment loading">图片加载中…</div>
  }
  return (
    <div className="image-part">
      <img src={src} alt={ref.name ?? '图片'} loading="lazy" />
    </div>
  )
}

function AttachmentPart({ part }: { part: MessagePart }) {
  const ref = partFileRef(part)
  const size =
    typeof part.data?.size === 'number'
      ? part.data.size
      : typeof part.data?.size === 'string'
        ? Number(part.data.size)
        : null
  const sizeText = size !== null && size > 0 ? ` · ${formatSize(size)}` : ''
  return (
    <div className="attachment">
      <span className="attachment-icon">📎</span>
      <span className="attachment-name">{ref.name ?? '附件'}</span>
      <span className="attachment-meta">{sizeText}</span>
    </div>
  )
}

function ErrorPart({ part }: { part: MessagePart }) {
  return <div className="error-part">⚠️ {(part.content ?? '').trim() || '出错了'}</div>
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function BodyPart({ part }: { part: MessagePart }) {
  switch (part.type) {
    case 'image':
      return <ImagePart part={part} />
    case 'attachment':
      return <AttachmentPart part={part} />
    case 'error':
      return <ErrorPart part={part} />
    case 'source_list':
      return <div className="source-list">📚 引用了 {(part.data?.count as number | undefined) ?? ''} 个来源</div>
    default: {
      const content = (part.content ?? '').trim()
      if (!content) return null
      return <Markdown text={content} />
    }
  }
}

// ------------------------------------------------------------------------- //
// 消息气泡
// ------------------------------------------------------------------------- //

export function MessageBubble({ message }: { message: Message }) {
  if (message.role === 'user') {
    return (
      <div className="msg user">
        <div className="bubble user">{message.content}</div>
      </div>
    )
  }

  const { thinking, body } = splitParts(message.parts)
  const failed = message.status === 'failed' || body.some((p) => p.status === 'failed')

  return (
    <div className="msg assistant">
      <div className="bubble assistant">
        {thinking.length > 0 ? <ThinkingBlock parts={thinking} /> : null}
        {body.map((p) => (
          <BodyPart key={p.id} part={p} />
        ))}
        {body.length === 0 && thinking.length === 0 ? (
          <div className="dim">（空回复）</div>
        ) : null}
        {failed ? (
          <div className="error-part">⚠️ 生成失败{message.status === 'failed' ? '，可点重试' : ''}</div>
        ) : null}
      </div>
    </div>
  )
}

/** 正在流式生成的助手消息（liveParts 累积） */
export function LiveBubble({ parts }: { parts: MessagePart[] }) {
  const { thinking, body } = splitParts(parts)
  const streaming = parts.some((p) => p.status === 'streaming' || p.status === 'pending')
  return (
    <div className="msg assistant">
      <div className="bubble assistant">
        {thinking.length > 0 ? <ThinkingBlock parts={thinking} /> : null}
        {body.map((p) => (
          <BodyPart key={p.id} part={p} />
        ))}
        {body.length === 0 && thinking.length === 0 ? (
          <div className="thinking-text dim">（正在思考…）</div>
        ) : null}
        {streaming && body.some((p) => p.type === 'text') ? <span className="caret" aria-hidden="true" /> : null}
      </div>
    </div>
  )
}
