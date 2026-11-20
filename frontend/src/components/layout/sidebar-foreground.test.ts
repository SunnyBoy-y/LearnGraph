import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { parse } from 'postcss'

const sidebarCss = readFileSync('src/components/layout/sidebar-navigation.css', 'utf8')
const indexCss = readFileSync('src/index.css', 'utf8')

function declarations(css: string, selector: string) {
  const result: Record<string, string> = {}
  parse(css).walkRules(rule => {
    if (!rule.selectors.includes(selector)) return
    rule.each(node => { if (node.type === 'decl') result[node.prop] = node.value })
  })
  return result
}

describe('neutral sidebar palette', () => {
  it('uses one neutral icon color instead of per-entry accent colors', () => {
    expect(declarations(sidebarCss, '.sidebar-nav')['--lg-sidebar-nav-icon']).toBe('#737373')
    expect(declarations(sidebarCss, '.dark .sidebar-nav')['--lg-sidebar-nav-icon']).toBe('var(--text-secondary)')
    for (const label of ['新对话', '图谱', '路线', '资料', '产物', '定时任务']) {
      expect(declarations(sidebarCss, `.sidebar-nav [data-nav-label='${label}']`)).not.toHaveProperty('--lg-sidebar-nav-icon')
      expect(declarations(indexCss, `.sidebar-nav [data-nav-label='${label}'] > svg`)).not.toHaveProperty('color')
    }
  })

  it('uses the active theme colors for the brand and glyph', () => {
    expect(declarations(sidebarCss, 'html[data-color-mode] .sidebar-nav .sidebar-brand .sidebar-brand__mark'))
      .toMatchObject({ background: 'var(--primary)', color: 'var(--primary-foreground)' })
  })

  it('emphasizes section labels and preserves session selection styling', () => {
    expect(declarations(sidebarCss, '.sidebar-nav .sidebar-section__toggle')['font-weight']).toBe('600')
    expect(declarations(sidebarCss, '.sidebar-nav .sidebar-project__session.is-active'))
      .toMatchObject({ background: 'color-mix(in srgb, var(--foreground) 8%, var(--sidebar))', color: 'var(--sidebar-accent-foreground)' })
  })
})
