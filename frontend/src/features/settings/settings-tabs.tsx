import { Suspense, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { LoadingState } from '@/components/shared/page-elements'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { PageHeadingLevelContext } from '@/components/shared/page-heading-level'

export function SettingsTabs({ items }: { items: Array<{ id: string; label: string; content: ReactNode }> }) {
  const [params, setParams] = useSearchParams()
  const active = items.some(item => item.id === params.get('tab')) ? params.get('tab')! : items[0].id
  return <Tabs value={active} onValueChange={value => { const next = new URLSearchParams(params); next.set('tab', value); setParams(next, { replace: true }) }}>
    <TabsList aria-label="设置分组" className="mb-5">{items.map(item => <TabsTrigger key={item.id} value={item.id}>{item.label}</TabsTrigger>)}</TabsList>
    {/* Panels mount on first visit and remain mounted when switching tabs, preserving explicit-save drafts. */}
    {items.map(item => <SettingsTab key={item.id} active={active === item.id} item={item}/>) }
  </Tabs>
}

function SettingsTab({ active, item }: { active: boolean; item: { id: string; content: ReactNode } }) {
  const [visited, setVisited] = useState(active)
  if (active && !visited) setVisited(true)
  return <TabsContent forceMount value={item.id} hidden={!active} className="settings-subpage data-[state=inactive]:hidden">
    {visited ? <PageHeadingLevelContext.Provider value={2}><Suspense fallback={<LoadingState/>}>{item.content}</Suspense></PageHeadingLevelContext.Provider> : null}
  </TabsContent>
}

