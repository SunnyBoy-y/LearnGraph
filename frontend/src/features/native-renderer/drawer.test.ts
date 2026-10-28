import { describe, expect, it } from 'vitest'
import { drawerAxis, rendererDrawerSide } from './drawer'

describe('embedded renderer drawer direction lock', () => {
  it('shares the web thresholds and yields to vertical movement', () => {
    expect(drawerAxis(7, 1)).toBe('pending')
    expect(drawerAxis(8, 0)).toBe('horizontal')
    expect(drawerAxis(10, 9)).toBe('pending')
    expect(drawerAxis(12, 9)).toBe('horizontal')
    expect(drawerAxis(7, 8)).toBe('dead')
    expect(drawerAxis(8, 8)).toBe('dead')
  })
  it('resolves canvas directions and keeps graph-surface gestures on the open right drawer', () => {
    expect(drawerAxis(-20, 1)).toBe('horizontal')
    expect(drawerAxis(-7, 0)).toBe('pending')
    expect(rendererDrawerSide(20, 'message')).toBe('left')
    expect(rendererDrawerSide(-20, 'message')).toBe('right')
    expect(rendererDrawerSide(20, 'graph')).toBe('right')
    expect(rendererDrawerSide(-20, 'graph')).toBe('right')
  })
})
