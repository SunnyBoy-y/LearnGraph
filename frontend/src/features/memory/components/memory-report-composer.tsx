import { type RefObject, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { useReducedMotionPreference } from '@/lib/motion-preferences'
import { cn } from '@/lib/utils'
import { ArrowUp, Check, ChevronDown, LoaderCircle, Pencil, Search } from 'lucide-react'

import {
  FEATURE_MODEL_DEFAULT,
  useFeatureModelChoices,
  withConfiguredModelChoice,
} from '@/components/shared/feature-model-select'
import {
  PromptInput,
  PromptInputBody,
  PromptInputButton,
  PromptInputSubmit,
  PromptInputTextarea,
} from '@/components/ai-elements/prompt-input'
import { InputGroupAddon } from '@/components/ui/input-group'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import '../memory-report-composer.css'
import type { MemoryReportSelection } from './memory-report'

function modelValue(providerId: string, modelId: string) {
  return `${providerId}::${modelId}`
}
function MemoryModelPicker({
  disabled,
  modelId,
  onChange,
  providerId,
}: {
  disabled?: boolean
  modelId: string
  onChange: (selection: { providerId: string; modelId: string }) => void
  providerId: string
}) {
  const { choices, providers } = useFeatureModelChoices()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const selectedValue = providerId && modelId ? modelValue(providerId, modelId) : ''
  const options = useMemo(() => {
    const configured = withConfiguredModelChoice(
      choices,
      providerId,
      modelId,
      providers.find((provider) => provider.id === providerId)?.display_name,
    )
    return configured.filter((choice) => choice.value !== FEATURE_MODEL_DEFAULT)
  }, [choices, modelId, providerId, providers])
  const visibleOptions = options.filter((choice) =>
    `${choice.label} ${choice.value}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
  )
  const selected = options.find((choice) => choice.value === selectedValue)
  const shortLabel = selected?.modelId || (modelId ? modelId : '默认模型')

  return (
    <DropdownMenu
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) setQuery('')
      }}
      open={open}
    >
      <DropdownMenuTrigger asChild>
        <PromptInputButton
          aria-label="选择记忆整理模型"
          className="chat-composer__mode"
          disabled={disabled}
          title={selected?.label || '按记忆设置选择模型'}
          type="button"
        >
          <span className="max-w-36 truncate">{shortLabel}</span>
          <ChevronDown aria-hidden="true" className="size-4" />
        </PromptInputButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="chat-model-menu max-h-[min(60vh,32rem)] w-72 overflow-y-auto"
        collisionPadding={12}
        side="top"
      >
        <DropdownMenuLabel>记忆整理模型</DropdownMenuLabel>
        <DropdownMenuItem
          className="text-xs"
          onSelect={() => onChange({ providerId: '', modelId: '' })}
        >
          <span className="min-w-0 flex-1">按记忆设置</span>
          {!selectedValue ? <Check className="size-3.5" /> : null}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <div className="relative px-1 pb-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            aria-label="搜索记忆模型"
            className="h-8 pl-8 font-mono text-xs"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => event.stopPropagation()}
            onPointerDown={(event) => event.stopPropagation()}
            placeholder="搜索模型或 Provider…"
            value={query}
          />
        </div>
        {visibleOptions.length ? (
          <DropdownMenuRadioGroup
            onValueChange={(value) => {
              const separator = value.indexOf('::')
              if (separator < 1) return
              onChange({
                providerId: value.slice(0, separator),
                modelId: value.slice(separator + 2),
              })
              setOpen(false)
            }}
            value={selectedValue}
          >
            {visibleOptions.map((choice) => (
              <DropdownMenuRadioItem key={choice.value} value={choice.value}>
                <span className="flex min-w-0 flex-col">
                  <span className="truncate font-mono text-xs">{choice.modelId}</span>
                  <span className="truncate text-[10px] text-muted-foreground">
                    {choice.label.replace(`${choice.modelId}`, '').replace(/^\s*[·:-]\s*/, '') || choice.providerId}
                  </span>
                </span>
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        ) : (
          <DropdownMenuItem disabled>
            {choices.length ? '没有匹配的模型' : '暂无可用模型，请先配置 Provider'}
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
/**
 * Memory report composer built on the same PromptInput primitives as the
 * conversation composer. It keeps report-specific correction context while
 * inheriting the shared textarea/submit behavior and model affordance.
 */
export function MemoryReportComposer({
  busy,
  draft,
  inputRef,
  modelId,
  onClearSelection,
  onDraftChange,
  onModelChange,
  onSubmit,
  providerId,
  selection,
}: {
  busy: boolean
  draft: string
  inputRef: RefObject<HTMLTextAreaElement | null>
  modelId: string
  onClearSelection: () => void
  onDraftChange: (value: string) => void
  onModelChange: (selection: { providerId: string; modelId: string }) => void
  onSubmit: (text: string) => Promise<boolean | void>
  providerId: string
  selection: MemoryReportSelection | null
}) {
  const reducedMotion = useReducedMotionPreference()
  const localTextareaRef = useRef<HTMLTextAreaElement | null>(null)
  const [expanded, setExpanded] = useState(false)

  const syncHeight = useCallback(() => {
    const el = localTextareaRef.current
    if (!el) return
    el.style.width = '100%'
    const styles = window.getComputedStyle(el)
    const maxHeight = Number.parseFloat(styles.getPropertyValue('--composer-textarea-max')) || 210
    const minHeight = Number.parseFloat(styles.getPropertyValue('--composer-textarea-min')) || 52
    el.style.height = '0px'
    const contentHeight = el.scrollHeight
    const next = Math.min(maxHeight, Math.max(minHeight, contentHeight))
    el.style.height = `${next}px`
    el.style.overflowY = contentHeight > maxHeight + 1 ? 'auto' : 'hidden'
    setExpanded(contentHeight > minHeight + 1)
  }, [])

  useEffect(() => {
    syncHeight()
  }, [draft, syncHeight])

  useEffect(() => {
    const el = localTextareaRef.current
    if (!el) return
    let frame = 0
    const scheduleSync = () => {
      if (frame) return
      frame = window.requestAnimationFrame(() => {
        frame = 0
        syncHeight()
      })
    }
    window.addEventListener('resize', scheduleSync)
    const group = el.closest("[data-slot='input-group']")
    let lastWidth = group ? group.getBoundingClientRect().width : -1
    let observer: ResizeObserver | undefined
    if (group && typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(([entry]) => {
        const nextWidth = entry?.contentRect.width ?? group.getBoundingClientRect().width
        if (Math.abs(nextWidth - lastWidth) <= 0.5) return
        lastWidth = nextWidth
        scheduleSync()
      })
      observer.observe(group)
    }
    return () => {
      window.removeEventListener('resize', scheduleSync)
      observer?.disconnect()
      if (frame) window.cancelAnimationFrame(frame)
    }
  }, [syncHeight])

  return (
    <div className="w-full">
      <AnimatePresence initial={false}>
        {selection ? (
          <motion.div
            animate={{ height: 'auto', opacity: 1, y: 0 }}
            className="mb-2 flex items-center gap-2 overflow-hidden px-2 text-xs text-muted-foreground"
            exit={{ height: 0, opacity: 0, y: reducedMotion ? 0 : 4 }}
            initial={reducedMotion ? false : { height: 0, opacity: 0, y: 4 }}
            transition={{ duration: reducedMotion ? 0 : 0.16 }}
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

      <PromptInput
        className={cn('chat-composer memory-report-composer', expanded && 'is-expanded')}
        onSubmit={async ({ text }) => {
          if (!text.trim()) return false
          return (await onSubmit(text)) !== false
        }}
      >
        <PromptInputBody>
          <PromptInputTextarea
            aria-label="告诉 AI 导师需要记住、修改或忘记什么"
            className="chat-composer__textarea memory-report-composer__textarea"
            disabled={busy}
            maxLength={2000}
            onChange={(event) => onDraftChange(event.currentTarget.value)}
            placeholder="告诉 AI 导师需要记住、修改或忘记什么……"
            ref={(node) => {
              localTextareaRef.current = node
              inputRef.current = node
            }}
            value={draft}
          />
        </PromptInputBody>
        <InputGroupAddon align="inline-end" className="chat-composer__end">
          <MemoryModelPicker
            disabled={busy}
            modelId={modelId}
            onChange={onModelChange}
            providerId={providerId}
          />
          <PromptInputSubmit
            aria-label={busy ? '正在整理记忆' : '提交记忆'}
            className="chat-composer__submit"
            disabled={busy || !draft.trim()}
            status={busy ? 'submitted' : 'ready'}
          >
            {busy ? <LoaderCircle className="size-3.5 animate-spin" /> : <ArrowUp className="size-4" />}
          </PromptInputSubmit>
        </InputGroupAddon>
      </PromptInput>
    </div>
  )
}
