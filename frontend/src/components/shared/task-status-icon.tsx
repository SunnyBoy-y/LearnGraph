import { useEffect, useRef, useState } from 'react'
import { Check, Circle, LoaderCircle, X } from 'lucide-react'
import { cn } from '@/lib/utils'

export type TaskVisualStatus = 'idle' | 'running' | 'succeeded' | 'failed' | 'cancelled'

/** Only a live running -> succeeded transition celebrates; restored history stays still. */
export function TaskStatusIcon({ status, taskKey, className }: {
  status: TaskVisualStatus
  taskKey: string
  className?: string
}) {
  const previous = useRef({ status, taskKey })
  const [completed, setCompleted] = useState(false)

  useEffect(() => {
    const before = previous.current
    previous.current = { status, taskKey }
    const celebrate = before.taskKey === taskKey && before.status === 'running' && status === 'succeeded'
    setCompleted(celebrate)
    if (!celebrate) return
    const timer = setTimeout(() => setCompleted(false), 700)
    return () => clearTimeout(timer)
  }, [status, taskKey])

  return <span
    aria-hidden="true"
    className={cn('task-status-icon inline-grid size-4 shrink-0 place-items-center', className)}
    data-status={status}
    data-completed={completed || undefined}
  >
    {status === 'running' ? <LoaderCircle className="size-full animate-spin motion-reduce:animate-none" />
      : status === 'succeeded' ? <Check className="task-status-icon__check size-full" />
      : status === 'failed' || status === 'cancelled' ? <X className="size-full" />
      : <Circle className="size-full" />}
  </span>
}
