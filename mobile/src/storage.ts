/**
 * 持久化：APK 内走 @capacitor/preferences（Android 侧加密存储），
 * 浏览器开发态走 localStorage。统一异步接口。
 */

import { Preferences } from '@capacitor/preferences'

const isNative =
  typeof window !== 'undefined' &&
  Boolean(
    (window as unknown as { Capacitor?: { isNativePlatform?: () => boolean } }).Capacitor
      ?.isNativePlatform?.(),
  )

export async function storageGet(key: string): Promise<string | null> {
  if (isNative) {
    const { value } = await Preferences.get({ key })
    return value
  }
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export async function storageSet(key: string, value: string): Promise<void> {
  if (isNative) {
    await Preferences.set({ key, value })
    return
  }
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // 隐私模式等场景忽略
  }
}

export async function storageRemove(key: string): Promise<void> {
  if (isNative) {
    await Preferences.remove({ key })
    return
  }
  try {
    window.localStorage.removeItem(key)
  } catch {
    // ignore
  }
}

export function createUuid(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID()
    }
  } catch {
    // fall through
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}
