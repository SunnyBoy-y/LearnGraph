import { afterEach, describe, expect, it, vi } from 'vitest'
import { saveBackupDownload } from './backup-download'

vi.mock('./native-download', () => ({ nativeDownloadSupported: () => false, saveBlobViaNative: async () => false,downloadViaNative:()=>false,toAbsoluteApiUrl:(value:string)=>value }))
afterEach(() => { Reflect.deleteProperty(window, 'showSaveFilePicker') })

describe('large backup download', () => {
  it('selects a file before the request and pipes without a whole response blob', async () => {
    let received = 0
    const order: string[] = []
    const output = new WritableStream<Uint8Array>({ write(chunk) { received += chunk.byteLength } })
    Object.assign(window, { showSaveFilePicker: async () => { order.push('picker'); return { createWritable: async () => output } } })
    const response = new Response(new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(1024)); controller.close() } }), { headers: { 'Content-Length': String(6 * 1024 ** 3) } })
    const blob = vi.spyOn(response, 'blob').mockRejectedValue(new Error('whole blob forbidden'))
    await saveBackupDownload(async () => { order.push('request'); return response }, 'backup.zip')
    expect(order).toEqual(['picker', 'request'])
    expect(received).toBe(1024)
    expect(blob).not.toHaveBeenCalled()
  })
  it('refuses large fallback downloads before reading the body', async () => {
    const cancel = vi.fn()
    const response = new Response(new ReadableStream({ cancel }), { headers: { 'Content-Length': String(4 * 1024 ** 3) } })
    await expect(saveBackupDownload(async () => response, 'backup.zip')).rejects.toThrow('桌面 Chrome')
    expect(cancel).toHaveBeenCalled()
  })
  it('does not start a server backup if file selection was cancelled', async () => {
    const request = vi.fn()
    Object.assign(window, { showSaveFilePicker: async () => { throw new DOMException('cancelled', 'AbortError') } })
    await expect(saveBackupDownload(request, 'backup.zip')).rejects.toThrow('cancelled')
    expect(request).not.toHaveBeenCalled()
  })
  it('uses a prepared native browser download without reading a response blob',async()=>{
    const request=vi.fn()
    const native=vi.fn().mockResolvedValue(undefined)
    await saveBackupDownload(request,'backup.zip',native)
    expect(native).toHaveBeenCalledOnce()
    expect(request).not.toHaveBeenCalled()
  })
})
