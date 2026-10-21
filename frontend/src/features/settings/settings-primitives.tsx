import { useState, type ReactNode } from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'

export function SettingsSection({ title, description, children, danger = false, id }: {
  title?: string; description?: string; children: ReactNode; danger?: boolean; id?: string
}) {
  return <section id={id} className={cn('settings-section', danger && 'settings-section--danger')}>
    {title || description ? <div className="mb-3">{title ? <h2 className="text-base font-semibold">{title}</h2> : null}
      {description ? <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p> : null}</div> : null}
    <div className="settings-rows">{children}</div>
  </section>
}

export function SettingRow({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return <div className="settings-row"><div className="min-w-0 flex-1"><p className="text-sm font-medium">{title}</p>
    {description ? <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p> : null}</div>
    <div className="settings-row__control">{children}</div></div>
}

// Native details keeps form state mounted while collapsed and supports keyboard navigation.
export function AdvancedSettings({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  const [visited, setVisited] = useState(false)
  return <details onToggle={event => { if (event.currentTarget.open) setVisited(true) }} className="settings-advanced group"><summary className="flex cursor-pointer items-center gap-3 rounded-lg py-3 text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring">
    <span className="min-w-0 flex-1">{title}{description ? <span className="mt-1 block text-xs font-normal text-muted-foreground">{description}</span> : null}</span>
    <ChevronDown className="size-4 shrink-0 transition-transform group-open:rotate-180"/></summary>
    <div className="space-y-4 pt-3">{visited ? children : null}</div></details>
}

