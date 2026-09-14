import { type FormEvent, type RefObject } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { ArrowUp, Pencil } from 'lucide-react'

import { Button } from '@/components/ui/button'
import type { MemoryReportSelection } from './memory-report'

const EXAMPLES = [
  '记住我现在在准备考研',
  '我现在不学操作系统了',
  '我更喜欢先看例子再讲原理',
]

/**
 * 报告底部的自然语言入口：仍然是「添加或更新记忆」的同一个入口，
 * 只是文案与交互从后台术语改成了对导师说话。
 */
export function MemoryReportComposer({
  busy,
  draft,
  inputRef,
  selection,
  onClearSelection,
  onDraftChange,
  onSubmit,
}: {
  busy: boolean
  draft: string
  inputRef: RefObject<HTMLInputElement | null>
  selection: MemoryReportSelection | null
  onClearSelection: () => void
  onDraftChange: (value: string) => void
  onSubmit: (event: FormEvent) => void
}) {
  return (
    <div className="w-full max-w-[800px]">
      <form
        className="rounded-2xl border bg-background/95 p-3 shadow-sm backdrop-blur"
        onSubmit={onSubmit}
      >
        <AnimatePresence initial={false}>
          {selection ? (
            <motion.div
              animate={{ height: 'auto', opacity: 1, y: 0 }}
              className="mb-2 flex items-center gap-2 overflow-hidden px-2 text-xs text-muted-foreground"
              exit={{ height: 0, opacity: 0, y: 4 }}
              initial={{ height: 0, opacity: 0, y: 4 }}
            >
              <Pencil className="size-3.5 text-primary" />
              <span className="min-w-0 flex-1 truncate">正在纠正：“{selection.text}”</span>
              <button
                className="shrink-0 hover:text-foreground"
                onClick={onClearSelection}
                type="button"
              >
                取消
              </button>
            </motion.div>
          ) : null}
        </AnimatePresence>
        <div className="flex items-center gap-2 rounded-[1.4rem] border bg-background py-1.5 pl-5 pr-1.5 shadow-sm transition-[border-color,box-shadow] focus-within:border-primary/40 focus-within:shadow-md">
          <input
            aria-label="告诉 AI 导师需要记住、修改或忘记什么"
            className="h-9 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            maxLength={2000}
            onChange={(event) => onDraftChange(event.target.value)}
            placeholder="告诉 AI 导师需要记住、修改或忘记什么……"
            ref={inputRef}
            value={draft}
          />
          <motion.div
            whileHover={busy || !draft.trim() ? undefined : { scale: 1.04 }}
            whileTap={busy || !draft.trim() ? undefined : { scale: 0.96 }}
          >
            <Button
              aria-label="提交记忆"
              className="size-9 rounded-full"
              disabled={busy || !draft.trim()}
              size="icon"
              type="submit"
            >
              <ArrowUp className="size-4" />
            </Button>
          </motion.div>
        </div>
        <p className="mt-2 px-2 text-xs leading-5 text-muted-foreground">
          例如
          {EXAMPLES.map((example) => (
            <span className="whitespace-nowrap" key={example}>
              {' · '}“{example}”
            </span>
          ))}
          <br />
          也可以在报告里选中一句话直接纠正。
        </p>
      </form>
    </div>
  )
}
