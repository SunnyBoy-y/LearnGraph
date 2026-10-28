export const COLOR_THEMES = [
  { id: 'mono', label: '石墨', color: '#353535' },
  { id: 'forest', label: '松绿', color: '#087f65' },
  { id: 'ocean', label: '海蓝', color: '#2563eb' },
  { id: 'violet', label: '鸢紫', color: '#7c3aed' },
  { id: 'amber', label: '琥珀', color: '#b45309' },
  { id: 'rose', label: '蔷薇', color: '#be185d' },
] as const

export type ThemeMode = 'light' | 'dark' | 'system'
export type ColorTheme = typeof COLOR_THEMES[number]['id'] | 'custom'
export type AppearancePreferences = { theme: ThemeMode; colorTheme: ColorTheme; customColor: string }
export const DEFAULT_APPEARANCE: AppearancePreferences = { theme: 'light', colorTheme: 'mono', customColor: '#2563eb' }
export const APPEARANCE_STORAGE_KEY = 'lg-appearance'

export function normalizeAppearance(value: unknown): AppearancePreferences {
  const record = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
  return {
    theme: record.theme === 'dark' || record.theme === 'system' ? record.theme : 'light',
    colorTheme: record.colorTheme === 'custom' || COLOR_THEMES.some(item => item.id === record.colorTheme)
      ? record.colorTheme as ColorTheme : 'mono',
    customColor: typeof record.customColor === 'string' && /^#[\da-f]{6}$/i.test(record.customColor)
      ? record.customColor.toLowerCase() : DEFAULT_APPEARANCE.customColor,
  }
}

export function readLocalAppearance(): AppearancePreferences {
  try {
    const saved = window.localStorage.getItem(APPEARANCE_STORAGE_KEY)
    if (saved) return normalizeAppearance(JSON.parse(saved))
    return normalizeAppearance({ theme: window.localStorage.getItem('lg-theme') })
  } catch { return { ...DEFAULT_APPEARANCE } }
}

function rgb(hex: string) {
  return [1, 3, 5].map(offset => Number.parseInt(hex.slice(offset, offset + 2), 16))
}

export function contrastRatio(a: string, b: string): number {
  const luminance = (hex: string) => rgb(hex).map(channel => {
    const v = channel / 255
    return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4
  }).reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0)
  const [x, y] = [luminance(a), luminance(b)]
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05)
}

/** Keep theme-colored text and button labels readable for extreme custom colors. */
export function accessibleThemeColor(color: string, dark: boolean): string {
  const background = dark ? '#30343a' : '#f4f4f5'
  let channels = rgb(color)
  const hex = () => `#${channels.map(value => value.toString(16).padStart(2, '0')).join('')}`
  while (contrastRatio(hex(), background) < 4.6) {
    channels = channels.map(value => dark ? Math.min(255, value + 5) : Math.max(0, value - 5))
  }
  return hex()
}

export function applyAppearance(value: unknown, persist = true): AppearancePreferences {
  const preferences = normalizeAppearance(value)
  const root = document.documentElement
  const dark = preferences.theme === 'dark' || (preferences.theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  root.classList.toggle('dark', dark)
  root.dataset.themeMode = preferences.theme
  root.dataset.colorMode = preferences.colorTheme
  const color = preferences.colorTheme === 'custom' ? preferences.customColor
    : COLOR_THEMES.find(item => item.id === preferences.colorTheme)!.color
  root.style.setProperty('--theme-color', color)
  root.style.setProperty('--theme-primary', accessibleThemeColor(color, dark))
  root.style.setProperty('--theme-on-primary', dark ? '#111315' : '#ffffff')
  if (persist) {
    try {
      window.localStorage.setItem(APPEARANCE_STORAGE_KEY, JSON.stringify(preferences))
      window.localStorage.setItem('lg-theme', preferences.theme)
    } catch { /* Preferences still apply when storage is unavailable. */ }
  }
  return preferences
}

/** Restore before React mounts and follow OS changes on every route. */
export function initializeAppearance(): () => void {
  applyAppearance(readLocalAppearance(), false)
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  const systemChanged = () => {
    if (document.documentElement.dataset.themeMode === 'system') applyAppearance(readLocalAppearance(), false)
  }
  const storageChanged = (event: StorageEvent) => {
    if (event.key === APPEARANCE_STORAGE_KEY) applyAppearance(readLocalAppearance(), false)
  }
  media.addEventListener('change', systemChanged)
  window.addEventListener('storage', storageChanged)
  return () => {
    media.removeEventListener('change', systemChanged)
    window.removeEventListener('storage', storageChanged)
  }
}
