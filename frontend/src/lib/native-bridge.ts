/**
 * 手机原生 JS bridge（LearnGraphNative）类型化封装。
 *
 * APK 的原生 WebView 通过 addJavascriptInterface 注入 window.LearnGraphNative，
 * 网页版运行在 APK 内时可调用；桌面浏览器无 bridge，所有方法安全降级。
 *
 * v0.11.0 扩展：
 *  - 分享收件箱：getInboxItems / clearInboxItem / clearInbox / getInboxImageDataUrl
 *  - 拍照即问：takePhoto（结果经 window.__lgPhotoCallback 回调）
 *  - 快捷动作：consumeShortcutAction（长按图标/通知打开会话）
 *  - 原有：clearAuth / download / saveBase64
 */

export interface NativeShareInboxItem {
  id: string
  kind: 'text' | 'image'
  text: string
  imagePath: string
  mime: string
  source: string
  created_at?: number
}

interface LearnGraphNativeBridge {
  clearAuth?: () => void
  download?: (url: string, fileName?: string) => void
  saveBase64?: (dataUrl: string, fileName?: string) => void
  getInboxItems?: () => string
  clearInboxItem?: (id: string) => void
  clearInbox?: () => void
  getInboxImageDataUrl?: (id: string) => string
  takePhoto?: () => void
  consumeShortcutAction?: () => string
  /** 后台任务投递成功标记：完成后台生成后（切到后台）推送完成通知 */
  notifyOnUpdate?: (sessionId: string) => void
  // A 类触觉 / 提示音 / 朗读
  haptic?: (intensity: number) => void
  replyHaptic?: () => void
  startReplyVibration?: () => void
  stopReplyVibration?: () => void
  stepHaptic?: () => void
  celebration?: () => void
  chime?: () => void
  speak?: (text: string) => void
}

function nativeBridge(): LearnGraphNativeBridge | null {
  try {
    if (typeof window === 'undefined') return null
    return (window as unknown as { LearnGraphNative?: LearnGraphNativeBridge })
      .LearnGraphNative ?? null
  } catch {
    return null
  }
}

/** APK 内运行（bridge 存在） */
export function isNativeApp(): boolean {
  return Boolean(nativeBridge())
}

// ------------------------------------------------------------------ //
// 分享收件箱
// ------------------------------------------------------------------ //

export function getInboxItems(): NativeShareInboxItem[] {
  const bridge = nativeBridge()
  if (!bridge?.getInboxItems) return []
  try {
    const raw = bridge.getInboxItems()
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    return Array.isArray(parsed) ? (parsed as NativeShareInboxItem[]) : []
  } catch {
    return []
  }
}

export function clearInboxItem(id: string): void {
  nativeBridge()?.clearInboxItem?.(id)
}

export function clearInbox(): void {
  nativeBridge()?.clearInbox?.()
}

/** 收件箱图片 → data URL（大图由原生压缩；超限/缺失返回空串） */
export function getInboxImageDataUrl(id: string): string {
  try {
    return nativeBridge()?.getInboxImageDataUrl?.(id) ?? ''
  } catch {
    return ''
  }
}

// ------------------------------------------------------------------ //
// 拍照即问
// ------------------------------------------------------------------ //

export type PhotoCallback = (dataUrl: string | null) => void

/**
 * 调用原生相机拍照。结果经 window.__lgPhotoCallback 异步回调：
 * 先注册回调再调用 takePhoto；未在 APK 内或无相机时回调 null。
 */
export function takePhoto(callback: PhotoCallback): void {
  const bridge = nativeBridge()
  if (!bridge?.takePhoto) {
    callback(null)
    return
  }
  ;(window as unknown as { __lgPhotoCallback?: PhotoCallback }).__lgPhotoCallback =
    callback
  bridge.takePhoto()
}

// ------------------------------------------------------------------ //
// 快捷动作（长按图标 / 通知打开会话）
// ------------------------------------------------------------------ //

export type NativeShortcutAction =
  | 'new-chat'
  | 'note'
  | 'tasks'
  | `open-session:${string}`

/** 读取并消费一次待执行的快捷动作；无则返回 null */
export function consumeShortcutAction(): NativeShortcutAction | null {
  const action = nativeBridge()?.consumeShortcutAction?.()
  return action && action.length > 0 ? (action as NativeShortcutAction) : null
}

// ------------------------------------------------------------------ //
// 后台任务完成通知
// ------------------------------------------------------------------ //

/**
 * 标记「此会话的后台生成完成后要推送通知」（仅 APK）：
 * 网页版把消息投递到 /messages/async 成功后调用，原生轮询遇到该会话
 * 变化时只等 App 切到后台再通知，避免前台轮询把变化基线吃掉。
 */
export function notifyOnUpdate(sessionId: string): void {
  nativeBridge()?.notifyOnUpdate?.(sessionId)
}

// ------------------------------------------------------------------ //
// A 类触觉 / 提示音（网页版在渲染关键时刻触发）
// ------------------------------------------------------------------ //

/** 最终回复到达轻震（思维链结束后第一帧正文） */
export function replyHaptic(): void {
  nativeBridge()?.replyHaptic?.()
}

/** 开始最终回答渲染期「答答答」持续震动 */
export function startReplyVibration(): void {
  nativeBridge()?.startReplyVibration?.()
}

/** 结束回答渲染期持续震动 */
export function stopReplyVibration(): void {
  nativeBridge()?.stopReplyVibration?.()
}

/** agent 工具/步骤完成弱震 */
export function stepHaptic(): void {
  nativeBridge()?.stepHaptic?.()
}

/** 目标/掌握度达成庆祝震动 */
export function celebration(): void {
  nativeBridge()?.celebration?.()
}

/** 可选手动震动（强度 0/1/2） */
export function haptic(intensity = 1): void {
  nativeBridge()?.haptic?.(intensity)
}

/** 可选提示音 */
export function chime(): void {
  nativeBridge()?.chime?.()
}

/** 朗读文本（B4 耳机自动朗读 / 手动播报） */
export function nativeSpeak(text: string): void {
  nativeBridge()?.speak?.(text)
}
