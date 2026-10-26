import { downloadViaNative, nativeDownloadSupported, saveBlobViaNative, toAbsoluteApiUrl } from './native-download'

type SavePicker = (options: { suggestedName: string; types: { description: string; accept: Record<string, string[]> }[] }) => Promise<{
  createWritable(): Promise<WritableStream<Uint8Array> & { abort(reason?: unknown): Promise<void> }>
}>

/** Keep browser memory independent of archive size when file streaming exists. */
export async function saveBackupDownload(request: () => Promise<Response>, name: string, browserDownload?: () => Promise<void>): Promise<void> {
  const picker = (window as unknown as { showSaveFilePicker?: SavePicker }).showSaveFilePicker
  if (picker) {
    // Open during the initiating user gesture, before awaiting the HTTP call.
    const file = await picker.call(window, { suggestedName: name, types: [{ description: '加密全站备份', accept: { 'application/zip': ['.zip'] } }] })
    const output = await file.createWritable()
    try {
      const response = await request()
      if (!response.body) throw new Error('浏览器未提供下载数据流')
      await response.body.pipeTo(output)
      return
    } catch (error) {
      await output.abort(error).catch(() => {})
      throw error
    }
  }
  if (browserDownload) {
    await browserDownload()
    return
  }
  const response = await request()
  const limit = (nativeDownloadSupported() ? 20 : 64) * 1024 * 1024
  const message = '此浏览器无法直接写入大备份，请使用支持文件保存的桌面 Chrome 或 Edge。'
  if (Number(response.headers.get('Content-Length')) > limit) {
    await response.body?.cancel()
    throw new Error(message)
  }
  const reader = response.body?.getReader()
  if (!reader) throw new Error('浏览器未提供下载数据流')
  const chunks: Uint8Array<ArrayBuffer>[] = []
  let size = 0
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > limit) throw new Error(message)
      chunks.push(value as Uint8Array<ArrayBuffer>)
    }
  } finally {
    await reader.cancel()
    reader.releaseLock()
  }
  const blob = new Blob(chunks, { type: 'application/zip' })
  if (await saveBlobViaNative(blob, name)) return
  if (nativeDownloadSupported()) throw new Error('移动端保存失败，请使用桌面浏览器下载。')
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

/** Native browser/phone downloads stream the response directly to disk. */
export function startPreparedBackupDownload(url: string, name: string): void {
  if (downloadViaNative(toAbsoluteApiUrl(url), name)) return
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  anchor.rel = 'noreferrer'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}
