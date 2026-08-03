import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

import { authStore } from '@/api/auth-store'
import {
  clearAllSelectionExplanations,
  clearPendingSelectionExplanation,
} from '@/features/chat/selection-explanation'

afterEach(() => {
  cleanup()
  clearPendingSelectionExplanation()
  clearAllSelectionExplanations()
  authStore.clear()
  window.localStorage.clear()
  window.sessionStorage.clear()
  vi.restoreAllMocks()
})

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }),
})

class TestResizeObserver implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

globalThis.ResizeObserver = TestResizeObserver

/**
 * Minimal DOMMatrix for the motion/animation libraries used by the chat panel.
 * jsdom does not implement DOMMatrix; these chainable stubs keep transform
 * parsing from throwing in behavior tests.
 */
class TestDOMMatrix {
  a = 1
  b = 0
  c = 0
  d = 1
  e = 0
  f = 0
  m11 = 1
  m12 = 0
  m13 = 0
  m14 = 0
  m21 = 0
  m22 = 1
  m23 = 0
  m24 = 0
  m31 = 0
  m32 = 0
  m33 = 1
  m34 = 0
  m41 = 0
  m42 = 0
  m43 = 0
  m44 = 1
  is2D = true
  isIdentity = true

  static fromString(_source?: string): TestDOMMatrix {
    return new TestDOMMatrix()
  }
  static fromMatrix(_other?: unknown): TestDOMMatrix {
    return new TestDOMMatrix()
  }
  translate(): TestDOMMatrix {
    return this
  }
  translateX(): TestDOMMatrix {
    return this
  }
  translateY(): TestDOMMatrix {
    return this
  }
  scale(): TestDOMMatrix {
    return this
  }
  scale3d(): TestDOMMatrix {
    return this
  }
  rotate(): TestDOMMatrix {
    return this
  }
  rotateAxisAngle(): TestDOMMatrix {
    return this
  }
  multiply(): TestDOMMatrix {
    return this
  }
  multiplySelf(): TestDOMMatrix {
    return this
  }
  inverse(): TestDOMMatrix {
    return this
  }
  setMatrixValue(): TestDOMMatrix {
    return this
  }
  transformPoint(point: { x?: number; y?: number }) {
    return { x: point.x ?? 0, y: point.y ?? 0, z: 0, w: 1 }
  }
  toString(): string {
    return 'matrix(1, 0, 0, 1, 0, 0)'
  }
}

globalThis.DOMMatrix = TestDOMMatrix as unknown as typeof DOMMatrix
globalThis.DOMMatrixReadOnly = TestDOMMatrix as unknown as typeof DOMMatrixReadOnly

Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
  configurable: true,
  value: () => undefined,
})
