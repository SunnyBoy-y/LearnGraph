import { FileClock, RotateCcw } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { EmptyState } from '@/components/shared/page-elements'
import type { MemoryEntry } from '@/types/memory'
import { formatMemoryTime } from '../memory-display'

/**
 * 回收站：原来的「删除恢复」一级 Tab 降级到这里。
 * 30 分钟恢复窗口与过窗销毁语义完全不变。
 */
export function MemoryTrashDialog({
  busy,
  items,
  open,
  onOpenChange,
  onRestore,
}: {
  busy: boolean
  items: MemoryEntry[]
  open: boolean
  onOpenChange: (open: boolean) => void
  onRestore: (id: string) => void
}) {
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>回收站</DialogTitle>
          <DialogDescription>
            删除的记忆会在这里保留 30 分钟，可以直接恢复；超过窗口后正文会被不可逆销毁。
          </DialogDescription>
        </DialogHeader>
        {items.length ? (
          <div className="divide-y border-y">
            {items.map((item) => (
              <div className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center" key={item.id}>
                <FileClock className="size-4 shrink-0 text-amber-600" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-semibold">{item.title}</p>
                    <Badge variant="secondary">{item.state === 'destroyed' ? '已销毁' : '已删除'}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {item.restore_available
                      ? `可恢复到 ${formatMemoryTime(item.recoverable_until)}`
                      : `正文已于 ${formatMemoryTime(item.content_destroyed_at)} 销毁`}
                  </p>
                </div>
                <Button
                  disabled={!item.restore_available || busy}
                  onClick={() => onRestore(item.id)}
                  size="sm"
                  variant="outline"
                >
                  <RotateCcw className="size-4" />恢复
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            description="删除的记忆会短暂出现在这里，过窗后只保留不含正文的审计元数据。"
            title="回收站是空的"
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
