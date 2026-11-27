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

function srgbToLinear(channel: number): number {
  const v = channel / 255
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
}

function linearToSrgb(value: number): number {
  const c = Math.max(0, Math.min(1, value))
  const s = c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055
  return Math.round(s * 255)
}

function hexToOklch(hex: string): [number, number, number] {
  const [r, g, b] = rgb(hex).map(srgbToLinear)
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  const L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
  const A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
  const C = Math.hypot(A, B)
  const H = C < 1e-6 ? 0 : (Math.atan2(B, A) * 180 / Math.PI + 360) % 360
  return [L, C, H]
}

function oklchToLinearRgb(L: number, C: number, H: number): [number, number, number] {
  const rad = H * Math.PI / 180
  const A = C * Math.cos(rad)
  const B = C * Math.sin(rad)
  const l_ = L + 0.3963377774 * A + 0.2158037573 * B
  const m_ = L - 0.1055613458 * A - 0.0638541728 * B
  const s_ = L - 0.0894841775 * A - 1.2914855480 * B
  const l = l_ ** 3
  const m = m_ ** 3
  const s = s_ ** 3
  return [
    +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
  ]
}

function inSrgbGamut([r, g, b]: [number, number, number]): boolean {
  return r >= -1e-4 && r <= 1 + 1e-4 && g >= -1e-4 && g <= 1 + 1e-4 && b >= -1e-4 && b <= 1 + 1e-4
}

function oklchToHex(L: number, C: number, H: number): string {
  let chroma = C
  if (!inSrgbGamut(oklchToLinearRgb(L, C, H))) {
    let lo = 0
    let hi = C
    for (let i = 0; i < 16; i++) {
      const mid = (lo + hi) / 2
      if (inSrgbGamut(oklchToLinearRgb(L, mid, H))) lo = mid
      else hi = mid
    }
    chroma = lo
  }
  const channels = oklchToLinearRgb(L, chroma, H).map(linearToSrgb)
  return `#${channels.map(value => value.toString(16).padStart(2, '0')).join('')}`
}

export function contrastRatio(a: string, b: string): number {
  const luminance = (hex: string) => rgb(hex).map(srgbToLinear)
    .reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0)
  const [x, y] = [luminance(a), luminance(b)]
  return (Math.max(x, y) + .05) / (Math.min(x, y) + .05)
}

/** Keep theme-colored text and controls readable while preserving perceptual hue in OKLCH. */
export function accessibleThemeColor(color: string, dark: boolean): string {
  const background = dark ? '#30343a' : '#f4f4f5'
  const normalized = color.toLowerCase()
  if (contrastRatio(normalized, background) >= 4.6) return normalized
  const [L, C, H] = hexToOklch(normalized)
  let lo = dark ? L : 0
  let hi = dark ? 1 : L
  let best = oklchToHex(dark ? 1 : 0, 0, H)
  for (let i = 0; i < 18; i++) {
    const mid = (lo + hi) / 2
    const candidate = oklchToHex(mid, C, H)
    if (contrastRatio(candidate, background) >= 4.6) {
      best = candidate
      if (dark) hi = mid
      else lo = mid
    } else if (dark) {
      lo = mid
    } else {
      hi = mid
    }
  }
  return best
}

/** Pick the higher-contrast ink color for labels placed on top of a theme-primary fill. */
export function accessibleOnPrimary(primary: string): '#ffffff' | '#111315' {
  return contrastRatio('#ffffff', primary) >= contrastRatio('#111315', primary) ? '#ffffff' : '#111315'
}

/** Generate a 5-series chart palette anchored on the theme primary with distinct OKLCH hues. */
export function themeChartPalette(primary: string, dark: boolean): [string, string, string, string, string] {
  const [, C, H] = hexToOklch(primary)
  const chroma = Math.max(0.09, Math.min(C || 0.12, dark ? 0.13 : 0.14))
  const lightness = dark ? 0.72 : 0.56
  const offsets = [72, 144, 216, 288] as const
  return [
    primary,
    ...offsets.map(offset => oklchToHex(lightness, chroma, (H + offset) % 360)),
  ] as [string, string, string, string, string]
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
  const primary = accessibleThemeColor(color, dark)
  const chartPalette = themeChartPalette(primary, dark)
  root.style.setProperty('--theme-color', color)
  root.style.setProperty('--theme-primary', primary)
  root.style.setProperty('--theme-on-primary', accessibleOnPrimary(primary))
  chartPalette.forEach((swatch, index) => {
    root.style.setProperty(`--theme-chart-${index + 1}`, swatch)
  })
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
