import { afterEach, describe, expect, it, vi } from 'vitest'
import { accessibleThemeColor, APPEARANCE_STORAGE_KEY, applyAppearance, COLOR_THEMES, contrastRatio, DEFAULT_APPEARANCE, initializeAppearance, normalizeAppearance, readLocalAppearance } from './appearance'

afterEach(() => {
  document.documentElement.classList.remove('dark')
  delete document.documentElement.dataset.themeMode
  delete document.documentElement.dataset.colorMode
})

describe('appearance restoration and contrast', () => {
  it('migrates the old dark preference and safely rejects corrupt values', () => {
    localStorage.setItem('lg-theme', 'dark')
    expect(readLocalAppearance()).toEqual({ ...DEFAULT_APPEARANCE, theme: 'dark' })
    expect(normalizeAppearance({ theme: 'oops', colorTheme: 'unknown', customColor: 'red;display:none' })).toEqual(DEFAULT_APPEARANCE)
    localStorage.setItem(APPEARANCE_STORAGE_KEY, 'not-json')
    expect(readLocalAppearance()).toEqual(DEFAULT_APPEARANCE)
  })

  it('restores both the custom color and mode after startup', () => {
    const choice = { theme: 'dark', colorTheme: 'custom', customColor: '#ffea00' }
    applyAppearance(choice)
    document.documentElement.classList.remove('dark')
    const dispose = initializeAppearance()
    expect(document.documentElement).toHaveClass('dark')
    expect(document.documentElement.dataset.colorMode).toBe('custom')
    expect(document.documentElement.style.getPropertyValue('--theme-color')).toBe('#ffea00')
    expect(readLocalAppearance()).toEqual(choice)
    dispose()
  })

  it('keeps every preset and extreme custom color readable in both modes', () => {
    for (const color of [...COLOR_THEMES.map(item => item.color), '#ffffff', '#000000', '#ffff00', '#00ff00']) {
      const light = accessibleThemeColor(color, false)
      const dark = accessibleThemeColor(color, true)
      expect(contrastRatio(light, '#f4f4f5')).toBeGreaterThanOrEqual(4.5)
      expect(contrastRatio(light, '#ffffff')).toBeGreaterThanOrEqual(4.5)
      expect(contrastRatio(dark, '#30343a')).toBeGreaterThanOrEqual(4.5)
      expect(contrastRatio(dark, '#111315')).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('follows OS changes only in system mode and releases its listeners', () => {
    const media = new EventTarget() as EventTarget & { matches: boolean }
    media.matches = false
    vi.spyOn(window, 'matchMedia').mockImplementation(() => media as unknown as MediaQueryList)
    applyAppearance({ theme: 'system', colorTheme: 'forest' })
    const dispose = initializeAppearance()
    media.matches = true
    media.dispatchEvent(new Event('change'))
    expect(document.documentElement).toHaveClass('dark')
    applyAppearance({ theme: 'light', colorTheme: 'forest' })
    media.dispatchEvent(new Event('change'))
    expect(document.documentElement).not.toHaveClass('dark')
    dispose()
    applyAppearance({ theme: 'system' })
    media.matches = false
    media.dispatchEvent(new Event('change'))
    expect(document.documentElement).toHaveClass('dark')
  })

  it('applies the theme even when browser storage is disabled', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    expect(() => applyAppearance({ theme: 'dark', colorTheme: 'violet' })).not.toThrow()
    expect(document.documentElement.dataset.colorMode).toBe('violet')
    expect(document.documentElement).toHaveClass('dark')
  })
})
