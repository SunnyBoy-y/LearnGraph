import { useCallback, useRef, useState } from 'react'
import { CircleAlert, X } from 'lucide-react'
import { Button } from '@/components/ui/button'

export type ComposerNotice = { message: string; action?: { label: string; onClick: () => void }; tone?: 'info' | 'error' }

/** A stale async result must not replace a newer session's inline feedback. */
export function useComposerFeedback(scope: string) {
  const currentScope = useRef(scope)
  currentScope.current = scope
  const [state, setState] = useState<{ scope: string; notice: ComposerNotice } | null>(null)
  const show = useCallback((message: string, action?: ComposerNotice['action'], tone: ComposerNotice['tone'] = 'info') => {
    if (currentScope.current === scope) setState({ scope, notice: { message, action, tone } })
  }, [scope])
  const clear = useCallback(() => {
    if (currentScope.current === scope) setState(null)
  }, [scope])
  return { notice: state?.scope === scope ? state.notice : null, show, clear }
}

export function ComposerFeedback({ id, notice, onDismiss }: { id: string; notice: ComposerNotice; onDismiss?: () => void }) {
  return (
    <div id={id} className="composer-feedback" role="status" aria-live="polite" data-tone={notice.tone ?? 'info'}>
      <CircleAlert size={16} aria-hidden="true" className="composer-feedback__icon" />
      <span className="composer-feedback__message">{notice.message}</span>
      {notice.action ? <Button type="button" variant="ghost" size="sm" onClick={notice.action.onClick}>{notice.action.label}</Button> : null}
      {onDismiss ? <Button type="button" variant="ghost" size="icon-sm" aria-label="关闭提示" onClick={onDismiss}><X size={14} /></Button> : null}
    </div>
  )
}
