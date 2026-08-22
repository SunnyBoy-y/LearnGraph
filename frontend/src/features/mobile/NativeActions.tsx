/**
 * 原生快捷动作消费（APK 内）：
 *
 * 长按图标快捷方式（新对话/记笔记/投递任务）或通知「打开会话」点击后，
 * 原生把动作写入待消费队列，网页版加载完成后在这里消费并导航。
 * 无动作或桌面浏览器时无副作用。
 */

import { useEffect, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { useAuth } from '@/features/auth/auth-context-value'
import { consumeShortcutAction } from '@/lib/native-bridge'

export function NativeActions() {
  const { workspaceId = '' } = useParams()
  const { workspaceId: activeWorkspaceId } = useAuth()
  const navigate = useNavigate()
  const wid = workspaceId || activeWorkspaceId
  const consumedRef = useRef(false)

  useEffect(() => {
    if (consumedRef.current || !wid) return
    consumedRef.current = true
    const action = consumeShortcutAction()
    if (!action) return
    if (action === 'new-chat') {
      navigate(`/w/${wid}/chat/new`)
    } else if (action === 'note') {
      navigate(`/w/${wid}/memory`)
    } else if (action === 'tasks') {
      navigate(`/w/${wid}/chat/new`)
    } else if (action.startsWith('open-session:')) {
      const sessionId = action.slice('open-session:'.length)
      if (sessionId) navigate(`/w/${wid}/chat/${sessionId}`)
    }
  }, [navigate, wid])

  return null
}
