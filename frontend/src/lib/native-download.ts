/**
 * 移动端原生下载通道（手机 App WebView 环境）。
 *
 * WebView 的 setDownloadListener 只接管「真实 URL」下载；网页端用
 * URL.createObjectURL + <a download> 触发的 blob 下载在 WebView 里是
 * blob: 协议，原生 OkHttp 无法请求 → 内置下载器失败。此模块在存在
 * window.LearnGraphNative（原生注册的 JS bridge）时，把下载交给原生：
 *  - download(url, name)：真实 URL → 原生 OkHttp 下载（同源自动附 Bearer）
 *  - saveBase64(dataUrl, name)：纯前端生成的 blob → base64 传回原生落盘
 * 桌面浏览器无 bridge，自动回退原有 blob 下载逻辑。
 */

interface NativeDownloadBridge {
  download?: (url: string, fileName?: string) => void
  saveBase64?: (dataUrl: string, fileName?: string) => void
}

const SAVE_BASE64_MAX_BYTES = 20 * 1024 * 1024 // 20 MiB，防止超大字符串撑爆 bridge

function nativeBridge(): NativeDownloadBridge | null {
  try {
    if (typeof window === 'undefined') return null
    return (
      window as unknown as { LearnGraphNative?: NativeDownloadBridge }
    ).LearnGraphNative ?? null
  } catch {
    return null
  }
}

/** WebView 原生通道是否可用 */
export function nativeDownloadSupported(): boolean {
  return Boolean(nativeBridge()?.download)
}

/** 把相对 API 路径解析为同源绝对 URL（原生下载需要完整地址） */
export function toAbsoluteApiUrl(path: string): string {
  try {
    return new URL(path, window.location.origin).href
  } catch {
    return path
  }
}

/**
 * 真实 URL 下载：优先走原生通道，返回 true 表示已交给原生。
 * 未命中原生（桌面浏览器 / 桥缺失）返回 false，调用方按原逻辑处理。
 */
export function downloadViaNative(url: string, fileName?: string): boolean {
  const bridge = nativeBridge()
  if (!bridge?.download) return false
  try {
    bridge.download(url, fileName)
    return true
  } catch {
    return false
  }
}

/**
 * 纯前端生成 blob 的下载：优先走原生 base64 通道，返回 true 表示已交给原生。
 * 超过大小上限或桥缺失返回 false，调用方回退原 blob 下载。
 */
export function saveBlobViaNative(
  blob: Blob,
  fileName: string,
): Promise<boolean> {
  return new Promise((resolve) => {
    const bridge = nativeBridge()
    if (!bridge?.saveBase64) {
      resolve(false)
      return
    }
    if (blob.size > SAVE_BASE64_MAX_BYTES) {
      resolve(false)
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      try {
        const dataUrl = String(reader.result ?? '')
        if (!dataUrl.startsWith('data:')) {
          resolve(false)
          return
        }
        bridge.saveBase64!(dataUrl, fileName)
        resolve(true)
      } catch {
        resolve(false)
      }
    }
    reader.onerror = () => resolve(false)
    reader.readAsDataURL(blob)
  })
}
