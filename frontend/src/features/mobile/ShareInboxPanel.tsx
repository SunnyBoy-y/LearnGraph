/**
 * 分享收件箱处理面板（APK 内）：
 *
 * 手机收到系统分享（文本/图片）→ 原生收件箱 → 本面板在网页版内
 * 拉取并让用户选择「存记忆 / 发起对话 / 挂目标」。
 * 仅当 window.LearnGraphNative.getInboxItems 存在（APK WebView）时激活，
 * 桌面浏览器完全无感（不渲染）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { BookmarkPlus, Inbox, MessageSquarePlus, Target, Trash2, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { createMemory } from '@/api/memory'
import { createSession, streamSessionMessage } from '@/api/sessions'
import { listGoals } from '@/api/goals'
import { uploadFile } from '@/api/files'
import { useAuth } from '@/features/auth/auth-context-value'
import {
  clearInbox,
  clearInboxItem,
  getInboxImageDataUrl,
  getInboxItems,
  type NativeShareInboxItem,
} from '@/lib/native-bridge'
import type {
  CSSProperties,
  PointerEvent as ReactPointerEvent,
} from 'react'

const POS_KEY = 'lg:inbox-floating-pos'

/** 右下角默认位置（px，相对视口） */
function defaultPos(): { x: number; y: number } {
  return { x: window.innerWidth - 64, y: window.innerHeight - 220 }
}

function loadPos(): { x: number; y: number } | null {
  try {
    const raw = window.localStorage.getItem(POS_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { x?: number; y?: number }
    if (typeof parsed.x === 'number' && typeof parsed.y === 'number') {
      return { x: parsed.x, y: parsed.y }
    }
  } catch {
    /* ignore */
  }
  return null
}

function titleFromText(text: string): string {
  const single = text.replace(/\s+/g, ' ').trim()
  return single.length > 40 ? `${single.slice(0, 40)}…` : single || '分享内容'
}

export function ShareInboxPanel() {
  const { workspaceId = '' } = useParams()
  const { workspaceId: activeWorkspaceId } = useAuth()
  const navigate = useNavigate()
  const wid = workspaceId || activeWorkspaceId

  const [items, setItems] = useState<NativeShareInboxItem[]>([])
  const [open, setOpen] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [goalPickerFor, setGoalPickerFor] = useState<string | null>(null)
  const [goals, setGoals] = useState<Array<{ id: string; title: string }>>([])
  // 可拖动浮窗：位置（px）持久化到 localStorage
  const [pos, setPos] = useState<{ x: number; y: number }>(() => loadPos() ?? defaultPos())
  const dragRef = useRef<{
    pointerId: number
    originX: number
    originY: number
    startX: number
    startY: number
  } | null>(null)

  const handleDragStart = useCallback(
    (event: ReactPointerEvent<HTMLButtonElement>) => {
      dragRef.current = {
        pointerId: event.pointerId,
        originX: pos.x,
        originY: pos.y,
        startX: event.clientX,
        startY: event.clientY,
      }
      try {
        event.currentTarget.setPointerCapture(event.pointerId)
      } catch {
        /* ignore */
      }
    },
    [pos],
  )

  const handleDragMove = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    const nextX = drag.originX + (event.clientX - drag.startX)
    const nextY = drag.originY + (event.clientY - drag.startY)
    setPos({
      x: Math.min(Math.max(0, nextX), Math.max(0, window.innerWidth - 64)),
      y: Math.min(Math.max(0, nextY), Math.max(0, window.innerHeight - 64)),
    })
  }, [])

  const handleDragEnd = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!dragRef.current) return
    dragRef.current = null
    try {
      event.currentTarget.releasePointerCapture(event.pointerId)
    } catch {
      /* ignore */
    }
    window.localStorage.setItem(
      POS_KEY,
      JSON.stringify({ x: pos.x, y: pos.y }),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pos])

  const refresh = useCallback(() => {
    setItems(getInboxItems())
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const enabled = useMemo(
    () => typeof window !== 'undefined' &&
      Boolean((window as unknown as { LearnGraphNative?: { getInboxItems?: unknown } }).LearnGraphNative?.getInboxItems),
    [],
  )

  // 有新的分享条目时自动展开一次
  useEffect(() => {
    if (enabled && items.length > 0 && !open) setOpen(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items.length, enabled])

  if (!enabled) return null

  const loadGoals = async () => {
    if (goals.length > 0) return
    try {
      const list = await listGoals()
      setGoals(list.map((g) => ({ id: g.id, title: g.title })))
    } catch {
      toast.error('目标列表加载失败')
    }
  }

  const removeItem = (id: string) => {
    clearInboxItem(id)
    refresh()
  }

  /** 存记忆（文本条目） */
  const handleSaveMemory = async (item: NativeShareInboxItem) => {
    if (item.kind !== 'text') return
    setBusyId(item.id)
    try {
      await createMemory({ title: titleFromText(item.text), content: item.text, source: 'share' })
      toast.success('已存入记忆')
      removeItem(item.id)
    } catch {
      toast.error('存记忆失败')
    } finally {
      setBusyId(null)
    }
  }

  /** 发起对话（文本/图片均可，图片走附件上传） */
  const handleStartChat = async (item: NativeShareInboxItem) => {
    setBusyId(item.id)
    try {
      const session = await createSession({ title: item.kind === 'text' ? titleFromText(item.text) : '图片提问' })
      let fileIds: string[] | undefined
      if (item.kind === 'image') {
        const dataUrl = getInboxImageDataUrl(item.id)
        if (dataUrl) {
          const blob = await (await fetch(dataUrl)).blob()
          const file = new File([blob], `share-${Date.now()}.jpg`, { type: 'image/jpeg' })
          const record = await uploadFile(file)
          fileIds = [record.id]
        }
      }
      const content = item.kind === 'text' ? item.text : '请看一下这张图片'
      // 消费完整个流（消息完整落库）再跳转
      for await (const _chunk of streamSessionMessage(session.id, { content, file_ids: fileIds })) {
        // 流式消费，无需 UI
      }
      removeItem(item.id)
      navigate(`/w/${wid}/chat/${session.id}`)
    } catch {
      toast.error('发起对话失败')
    } finally {
      setBusyId(null)
    }
  }

  /** 挂目标：选择目标后以 goal_id 存记忆（文本条目） */
  const handleAttachGoal = async (item: NativeShareInboxItem, goalId: string) => {
    if (item.kind !== 'text') return
    setBusyId(item.id)
    try {
      await createMemory({
        title: titleFromText(item.text),
        content: item.text,
        goal_id: goalId,
        source: 'share',
      })
      toast.success('已挂到目标')
      removeItem(item.id)
      setGoalPickerFor(null)
    } catch {
      toast.error('挂目标失败')
    } finally {
      setBusyId(null)
    }
  }

  const panelStyle = useMemo<CSSProperties>(() => {
    const BUTTON = 44
    const GAP = 8
    const vw = window.innerWidth
    const vh = window.innerHeight
    const pw = Math.min(360, vw - 16)
    let left: number
    const rightSpace = vw - (pos.x + BUTTON)
    if (rightSpace >= pw + GAP) {
      // 右侧空间足够 → 面板在按钮右侧
      left = pos.x + BUTTON + GAP
    } else if (pos.x - GAP >= pw) {
      // 右侧不足但左侧够 → 面板在按钮左侧
      left = pos.x - GAP - pw
    } else {
      // 两侧都不足 → 靠右对齐，不溢出
      left = Math.max(8, vw - pw - 8)
    }
    // 垂直：面板底边贴在按钮上方 GAP；高度受按钮到顶部距离约束，防顶部溢出
    const bottom = vh - pos.y - BUTTON + GAP
    const maxHeight = Math.max(120, Math.min(vh * 0.5, pos.y - 16))
    return { left, bottom, width: pw, maxHeight }
  }, [pos])

  return (
    <>
      {open && (
        <div
          className="fixed z-50 flex flex-col overflow-hidden rounded-2xl border bg-popover text-popover-foreground shadow-lg"
          style={panelStyle}
        >
          <div className="flex items-center gap-2 border-b px-4 py-3">
            <Inbox className="size-4" />
            <span className="text-sm font-semibold">分享收件箱（{items.length}）</span>
            <div className="ml-auto flex items-center gap-1">
              {items.length > 0 && (
                <Button
                  aria-label="清空收件箱"
                  className="h-7 px-2 text-xs"
                  onClick={() => {
                    clearInbox()
                    setItems([])
                    setOpen(false)
                  }}
                  variant="ghost"
                >
                  <Trash2 className="size-3.5" />
                  清空
                </Button>
              )}
              <Button
                aria-label="关闭"
                className="h-7 px-2"
                onClick={() => setOpen(false)}
                variant="ghost"
              >
                <X className="size-4" />
              </Button>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-2">
            {items.length === 0 ? (
              <p className="px-3 py-6 text-center text-xs text-muted-foreground">
                收件箱为空。从其他 App 分享内容到 LearnGraph 会出现在这里。
              </p>
            ) : (
              items.map((item) => (
                <div key={item.id} className="mb-2 rounded-xl border p-3">
                  {item.kind === 'image' ? (
                    <ImageThumb item={item} />
                  ) : (
                    <p className="mb-2 line-clamp-3 whitespace-pre-wrap break-all text-xs leading-5">
                      {item.text}
                    </p>
                  )}

                  {goalPickerFor === item.id ? (
                    <GoalPicker
                      goals={goals}
                      onCancel={() => setGoalPickerFor(null)}
                      onPick={(goalId) => handleAttachGoal(item, goalId)}
                    />
                  ) : (
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {item.kind === 'text' && (
                        <>
                          <Button
                            className="h-7 gap-1 px-2 text-xs"
                            disabled={busyId === item.id}
                            onClick={() => handleSaveMemory(item)}
                            size="sm"
                            variant="secondary"
                          >
                            <BookmarkPlus className="size-3.5" />
                            存记忆
                          </Button>
                          <Button
                            className="h-7 gap-1 px-2 text-xs"
                            disabled={busyId === item.id}
                            onClick={async () => {
                              setGoalPickerFor(item.id)
                              await loadGoals()
                            }}
                            size="sm"
                            variant="secondary"
                          >
                            <Target className="size-3.5" />
                            挂目标
                          </Button>
                        </>
                      )}
                      <Button
                        className="h-7 gap-1 px-2 text-xs"
                        disabled={busyId === item.id}
                        onClick={() => handleStartChat(item)}
                        size="sm"
                      >
                        <MessageSquarePlus className="size-3.5" />
                        {busyId === item.id ? '处理中…' : '发起对话'}
                      </Button>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      )}
      <div className="fixed z-50" style={{ left: pos.x, top: pos.y }}>
        <Button
          aria-label="分享收件箱（可按住拖动）"
        className="relative h-11 w-11 cursor-grab touch-none rounded-full shadow-md active:cursor-grabbing"
        onClick={() => setOpen((v) => !v)}
        onPointerCancel={handleDragEnd}
        onPointerDown={handleDragStart}
        onPointerMove={handleDragMove}
        onPointerUp={handleDragEnd}
        size="icon"
        title="可按住拖动；点击开合"
      >
        <Inbox className="size-5" />
        {items.length > 0 && (
          <span className="absolute -right-1 -top-1 grid h-5 min-w-5 place-items-center rounded-full bg-destructive px-1 text-[10px] font-bold text-destructive-foreground">
            {items.length}
          </span>
        )}
        </Button>
      </div>
    </>
  )
}

function ImageThumb({ item }: { item: NativeShareInboxItem }) {
  const [src, setSrc] = useState<string | null>(null)
  useEffect(() => {
    setSrc(getInboxImageDataUrl(item.id) || null)
  }, [item.id])
  return src ? (
    <img
      alt="分享图片"
      className="mb-2 max-h-32 w-full rounded-lg object-cover"
      src={src}
    />
  ) : (
    <p className="mb-2 text-xs text-muted-foreground">（图片，点击发起对话后可查看）</p>
  )
}

function GoalPicker({
  goals,
  onCancel,
  onPick,
}: {
  goals: Array<{ id: string; title: string }>
  onCancel: () => void
  onPick: (goalId: string) => void
}) {
  const [value, setValue] = useState('')
  return (
    <div className="flex items-center gap-1.5">
      <Select value={value} onValueChange={setValue}>
        <SelectTrigger className="h-7 flex-1 text-xs">
          <SelectValue placeholder={goals.length ? '选择目标' : '暂无目标，先创建目标再挂载'} />
        </SelectTrigger>
        <SelectContent>
          {goals.map((g) => (
            <SelectItem key={g.id} value={g.id}>
              <span className="line-clamp-1 max-w-[240px]">{g.title}</span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button
        className="h-7 px-2 text-xs"
        disabled={!value}
        onClick={() => value && onPick(value)}
        size="sm"
      >
        确定
      </Button>
      <Button className="h-7 px-2 text-xs" onClick={onCancel} size="sm" variant="ghost">
        取消
      </Button>
    </div>
  )
}
