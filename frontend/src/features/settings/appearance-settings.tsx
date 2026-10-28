import { listSettings, updateSetting } from '@/api'
import { ErrorState, LoadingState } from '@/components/shared/page-elements'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { currentWorkspaceQueryKey } from '@/lib/query-keys'
import { applyAppearance, COLOR_THEMES, normalizeAppearance, readLocalAppearance, type AppearancePreferences } from '@/lib/appearance'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Monitor, Moon, Sun } from 'lucide-react'
import { useEffect, useId, useState, type CSSProperties } from 'react'
import { toast } from 'sonner'
import type { WorkspaceSetting } from '@/types/settings'
import { SettingsSection } from './settings-primitives'
import './appearance-settings.css'

const MODES = [
  { id: 'light', label: '明亮', icon: Sun },
  { id: 'dark', label: '深色', icon: Moon },
  { id: 'system', label: '跟随系统', icon: Monitor },
] as const

export function AppearanceSettings() {
  const queryClient = useQueryClient()
  const queryKey = currentWorkspaceQueryKey('settings')
  const settings = useQuery({ queryKey, queryFn: listSettings, staleTime: 30000 })
  const current = settings.data?.find(item => item.key === 'ui.preferences')?.value
  const [preferences, setPreferences] = useState(readLocalAppearance)
  const [customDraft, setCustomDraft] = useState(preferences.customColor)
  const [customError, setCustomError] = useState(false)
  const errorId = useId()
  useEffect(() => {
    if (!settings.isSuccess) return
    const next = applyAppearance(current)
    setPreferences(next)
    setCustomDraft(next.customColor)
  }, [current, settings.isSuccess])

  const save = useMutation({
    mutationFn: (value: Record<string, unknown>) => updateSetting('ui.preferences', value),
    onSuccess: (saved) => {
      queryClient.setQueryData<WorkspaceSetting[]>(queryKey, items => [
        ...(items ?? []).filter(item => item.key !== 'ui.preferences'), saved,
      ])
      void queryClient.invalidateQueries({ queryKey })
    },
    onError: (error) => {
      const previous = applyAppearance(current)
      setPreferences(previous)
      setCustomDraft(previous.customColor)
      toast.error(`外观保存失败，已恢复原设置：${error.message}`)
    },
  })

  function change(patch: Partial<AppearancePreferences>) {
    const next = normalizeAppearance({ ...preferences, ...patch })
    if (save.isPending) return
    applyAppearance(next)
    setPreferences(next)
    setCustomError(false)
    save.mutate({ ...(current && typeof current === 'object' && !Array.isArray(current) ? current : {}), ...next })
  }
  function saveCustom() {
    if (!/^#[\da-f]{6}$/i.test(customDraft.trim())) { setCustomError(true); return }
    change({ colorTheme: 'custom', customColor: customDraft.trim() })
  }

  if (settings.isPending) return <LoadingState/>
  if (settings.error) return <ErrorState message={settings.error.message}/>
  return <SettingsSection title="外观" description="选择适合自己的明暗与配色，立即应用并保存到当前工作区。">
    <fieldset className="appearance-fieldset" disabled={save.isPending}>
      <legend>显示模式</legend>
      <div className="appearance-modes">
        {MODES.map(({ id, label, icon: Icon }) => <label key={id} className="appearance-mode">
          <input type="radio" name={`${errorId}-mode`} value={id} checked={preferences.theme === id} onChange={() => change({ theme: id })}/>
          <span><Icon size={17}/>{label}{preferences.theme === id && <Check size={14} aria-hidden="true"/>}</span>
        </label>)}
      </div>
    </fieldset>
    <fieldset className="appearance-fieldset" disabled={save.isPending}>
      <legend>主题配色</legend>
      <div className="appearance-palettes">
        {COLOR_THEMES.map(item => <label key={item.id} className="appearance-palette" style={{ '--palette-color': item.color } as CSSProperties}>
          <input type="radio" name={`${errorId}-palette`} value={item.id} checked={preferences.colorTheme === item.id} onChange={() => change({ colorTheme: item.id })}/>
          <span className="appearance-palette__body"><span className="appearance-palette__sample" aria-hidden="true"><i/><i/><i/></span>
            <span className="appearance-palette__name">{item.label}{preferences.colorTheme === item.id && <Check size={14} aria-hidden="true"/>}</span>
          </span>
        </label>)}
        <label className="appearance-palette" style={{ '--palette-color': preferences.customColor } as CSSProperties}>
          <input type="radio" name={`${errorId}-palette`} value="custom" checked={preferences.colorTheme === 'custom'} onChange={() => change({ colorTheme: 'custom' })}/>
          <span className="appearance-palette__body"><span className="appearance-palette__sample" aria-hidden="true"><i/><i/><i/></span><span className="appearance-palette__name">自定义{preferences.colorTheme === 'custom' && <Check size={14} aria-hidden="true"/>}</span></span>
        </label>
      </div>
      <div className="appearance-custom">
        <input type="color" aria-label="拾取自定义主题色" value={/^#[\da-f]{6}$/i.test(customDraft) ? customDraft : preferences.customColor} onChange={event => { setCustomDraft(event.target.value); setCustomError(false) }}/>
        <Input aria-label="自定义主题色 HEX" aria-invalid={customError} aria-describedby={customError ? errorId : undefined} value={customDraft} maxLength={7} onChange={event => { setCustomDraft(event.target.value); setCustomError(false) }} onKeyDown={event => { if (event.key === 'Enter') saveCustom() }}/>
        <Button variant="outline" onClick={saveCustom}>应用颜色</Button>
      </div>
      {customError && <p id={errorId} role="alert" className="text-xs text-destructive">请输入六位十六进制颜色，例如 #2563eb。</p>}
    </fieldset>
    <div className="appearance-note" role="status">{save.isPending ? '正在保存外观…' : '按钮、选中状态与重点标记会随配色更新。正文保持清晰，过亮或过暗的主题色会自动调整对比度。'}</div>
  </SettingsSection>
}
