/**
 * 极简 Markdown 渲染器（安全：只产出 React 元素，不使用 innerHTML）。
 * 覆盖：标题/段落/代码块/行内代码/加粗/斜体/链接/列表/引用/分割线。
 * 表格等少见结构降级为普通文本行。
 */

import { Fragment, type ReactNode } from 'react'

function renderInline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const regex =
    /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)|(\[[^\]]+\]\([^)\s]+\))/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  let i = 0
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(<Fragment key={`${keyBase}-t${i++}`}>{text.slice(lastIndex, match.index)}</Fragment>)
    }
    const token = match[0]
    if (token.startsWith('`')) {
      nodes.push(
        <code key={`${keyBase}-c${i++}`} className="md-code">
          {token.slice(1, -1)}
        </code>,
      )
    } else if (token.startsWith('**')) {
      nodes.push(
        <strong key={`${keyBase}-b${i++}`}>{token.slice(2, -2)}</strong>,
      )
    } else if (token.startsWith('*')) {
      nodes.push(<em key={`${keyBase}-e${i++}`}>{token.slice(1, -1)}</em>)
    } else {
      const linkMatch = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(token)
      if (linkMatch) {
        const url = linkMatch[2]
        if (/^https?:\/\//i.test(url)) {
          nodes.push(
            <a
              key={`${keyBase}-a${i++}`}
              className="md-link"
              href={url}
              target="_blank"
              rel="noreferrer"
            >
              {linkMatch[1]}
            </a>,
          )
        } else {
          nodes.push(<Fragment key={`${keyBase}-a${i++}`}>{linkMatch[1]}</Fragment>)
        }
      } else {
        nodes.push(<Fragment key={`${keyBase}-f${i++}`}>{token}</Fragment>)
      }
    }
    lastIndex = match.index + token.length
  }
  if (lastIndex < text.length) {
    nodes.push(<Fragment key={`${keyBase}-tail${i++}`}>{text.slice(lastIndex)}</Fragment>)
  }
  return nodes
}

function renderCodeBlock(lang: string | null, code: string, keyBase: string): ReactNode {
  return (
    <pre key={keyBase} className="md-pre">
      {lang ? <div className="md-pre-lang">{lang}</div> : null}
      <code>{code.replace(/\n$/, '')}</code>
    </pre>
  )
}

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let key = 0

  while (i < lines.length) {
    const line = lines[i]
    const k = key++

    // 代码块
    const fence = /^```(\w*)\s*$/.exec(line)
    if (fence) {
      const lang = fence[1] || null
      const code: string[] = []
      i += 1
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        code.push(lines[i])
        i += 1
      }
      i += 1 // 跳过结束 fence
      blocks.push(renderCodeBlock(lang, code.join('\n'), `b${k}`))
      continue
    }

    // 标题
    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading) {
      const level = Math.min(heading[1].length, 4)
      const content = heading[2]
      const Tag = (['h1', 'h2', 'h3', 'h4'] as const)[level - 1]
      blocks.push(
        <Tag key={`b${k}`} className={`md-h md-h${level}`}>
          {renderInline(content, `h${k}`)}
        </Tag>,
      )
      i += 1
      continue
    }

    // 分割线
    if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) {
      blocks.push(<hr key={`b${k}`} className="md-hr" />)
      i += 1
      continue
    }

    // 引用块（连续 > 行）
    if (line.startsWith('>')) {
      const quote: string[] = []
      while (i < lines.length && lines[i].startsWith('>')) {
        quote.push(lines[i].replace(/^>\s?/, ''))
        i += 1
      }
      blocks.push(
        <blockquote key={`b${k}`} className="md-quote">
          {quote.map((q, idx) => (
            <div key={idx}>{renderInline(q, `q${k}-${idx}`)}</div>
          ))}
        </blockquote>,
      )
      continue
    }

    // 无序列表
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ''))
        i += 1
      }
      blocks.push(
        <ul key={`b${k}`} className="md-ul">
          {items.map((item, idx) => (
            <li key={idx}>{renderInline(item, `u${k}-${idx}`)}</li>
          ))}
        </ul>,
      )
      continue
    }

    // 有序列表
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ''))
        i += 1
      }
      blocks.push(
        <ol key={`b${k}`} className="md-ol">
          {items.map((item, idx) => (
            <li key={idx}>{renderInline(item, `o${k}-${idx}`)}</li>
          ))}
        </ol>,
      )
      continue
    }

    // 普通段落（空行结束）
    if (line.trim()) {
      const paragraph: string[] = []
      while (i < lines.length && lines[i].trim()) {
        paragraph.push(lines[i])
        i += 1
      }
      blocks.push(
        <p key={`b${k}`} className="md-p">
          {renderInline(paragraph.join('\n'), `p${k}`)}
        </p>,
      )
      continue
    }

    i += 1
  }

  return <div className="md">{blocks}</div>
}
