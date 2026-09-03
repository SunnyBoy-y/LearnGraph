/**
 * 分享直连消费（APK 内）：
 *
 * 系统分享投递到原生收件箱后，本组件在网页版加载完成时一次性拉取并自动消费：
 *  - 文本 / 链接 → 填充到对话框文本
 *  - 文件（任意可上传类型）→ 上传后作为新会话附件挂载
 * 无需再进收件箱手动点击；桌面浏览器无 bridge，安全无副作用。
 */

import { useEffect, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'

import { uploadFile } from '@/api/files'
import { useAuth } from '@/features/auth/auth-context-value'
import {
  clearInbox,
  dataUrlToFile,
  getInboxFileDataUrl,
  getInboxItems,
} from '@/lib/native-bridge'
import type { FileRecord } from '@/types/files'

export const PREFILL_COMPOSER_EVENT = 'learngraph:prefill-composer'

function extFromMime(mime: string): string {
  const map: Record<string, string> = {
    'application/pdf': 'pdf',
    'text/plain': 'txt',
    'application/json': 'json',
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/webp': 'webp',
    'image/gif': 'gif',
  }
  return map[mime.split(';')[0].toLowerCase()] ?? 'bin'
}

export function PendingShareConsumer() {
  const { workspaceId = '' } = useParams()
  const { workspaceId: activeWorkspaceId } = useAuth()
  const navigate = useNavigate()
  const wid = workspaceId || activeWorkspaceId
  const consumedRef = useRef(false)

  useEffect(() => {
    if (consumedRef.current || !wid) return
    consumedRef.current = true
    void getInboxItems().then(async (items) => {
      if (!items.length) return

      // 先读取全部文件 data URL，再清空收件箱（清空会删除缓存文件）
      const textParts: string[] = []
      const fileEntries: Array<{ name: string; mime: string; dataUrl: string }> = []
      for (const item of items) {
        if (item.kind === 'text') {
          if (item.text.trim()) textParts.push(item.text.trim())
          continue
        }
        const dataUrl = await getInboxFileDataUrl(item.id)
        if (dataUrl) {
          fileEntries.push({
            name:
              item.name ||
              `share-${Date.now()}.${extFromMime(item.mime || '')}`,
            mime: item.mime || 'application/octet-stream',
            dataUrl,
          })
        }
      }
      clearInbox()

      const files: FileRecord[] = []
      for (const entry of fileEntries) {
        const file = dataUrlToFile(entry.dataUrl, entry.name, entry.mime)
        try {
          const record = await uploadFile(file)
          files.push(record)
        } catch {
          toast.error(`「${entry.name}」上传失败`)
        }
      }

      if (!textParts.length && !files.length) return
      navigate(`/w/${wid}/chat/new`, { replace: true })
      window.setTimeout(() => {
        window.dispatchEvent(
          new CustomEvent(PREFILL_COMPOSER_EVENT, {
            detail: { text: textParts.join('\n'), files },
          }),
        )
      }, 250)
    })
  }, [navigate, wid])

  return null
}
