import { useEffect, useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { MessageAction, type MessageActionProps } from '@/components/ai-elements/message'
import { toast } from '@/lib/notifications'

/** Success stays on the originating control, never in the global notification queue. */
export function useCopyFeedback(scope = '') {
  const [copiedScope, setCopiedScope] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const current = useRef(scope)
  current.current = scope
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; clearTimeout(timer.current) }
  }, [])
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      if (!mounted.current || current.current !== scope) return true
      clearTimeout(timer.current)
      setCopiedScope(scope)
      timer.current = setTimeout(() => setCopiedScope(null), 2500)
      return true
    } catch {
      if (mounted.current && current.current === scope) {
        setCopiedScope(null)
        toast.error('复制失败，请选择内容后手动复制', { scope, dedupeKey: `clipboard:${scope}` })
      }
      return false
    }
  }
  return { copied: copiedScope !== null && copiedScope === scope, copy }
}

type CopyMessageActionProps = Omit<MessageActionProps, 'onClick' | 'children'> & { text: string; scope?: string }
export function CopyMessageAction({ text, scope, label = '复制全文', tooltip, ...props }: CopyMessageActionProps) {
  const { copied, copy } = useCopyFeedback(scope ?? text)
  return (
    <MessageAction {...props} label={copied ? '已复制' : label} tooltip={copied ? '已复制' : tooltip ?? label} onClick={() => void copy(text)}>
      {copied ? <Check className="size-3.5" aria-hidden="true" /> : <Copy className="size-3.5" aria-hidden="true" />}
      <span className="sr-only" role="status">{copied ? '内容已复制到剪贴板' : ''}</span>
    </MessageAction>
  )
}
