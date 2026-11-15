import { createElement, Fragment, isValidElement, type ReactNode } from 'react'
import { toast as sonner, type Action, type ExternalToast, type ToastT } from 'sonner'

type Kind = 'message' | 'success' | 'info' | 'warning' | 'error' | 'loading'
type Title = Parameters<typeof sonner>[0]
type Id = string | number
export type NotificationOptions = ExternalToast & {
  /** Stable object/session key, captured before starting asynchronous work. */
  scope?: string
  context?: string
  dedupeKey?: string
  persistent?: boolean
}
type Entry = { id: Id; kind: Kind; title: Title; options: NotificationOptions; key?: string; closing?: boolean }

export const NOTIFICATION_LIMIT = 2
export const NOTIFICATION_DURATIONS = { message: 4000, success: 2800, info: 4000, warning: 5500, error: 6000, loading: Infinity } as const
const EXIT_DELAY = 220 // Let Sonner finish its 200ms unmount before revealing the next item.
const active = new Map<Id, Entry>()
const waiting: Entry[] = []
const releaseTimers = new Set<ReturnType<typeof setTimeout>>()
let sequence = 0
let surfaces = 0
let keyboardFocusWithin = false

/** Hover is handled by Sonner; keyboard focus must pause even after pointer leave. */
export function setNotificationFocus(focused: boolean) {
  if (keyboardFocusWithin === focused) return
  keyboardFocusWithin = focused
  for (const entry of active.values()) if (!entry.closing) render(entry)
}

function release(id: Id) {
  const entry = active.get(id)
  if (!entry || entry.closing) return
  entry.closing = true
  const timer = setTimeout(() => {
    releaseTimers.delete(timer)
    active.delete(id)
    drain()
  }, EXIT_DELAY)
  releaseTimers.add(timer)
}

function wrapAction(value: ExternalToast['action'], id: Id, cancel = false): ExternalToast['action'] {
  if (!value || isValidElement(value) || typeof value !== 'object' || !('onClick' in value)) return value
  const action = value as Action
  return { ...action, onClick: event => {
    action.onClick(event)
    if (cancel || !event?.defaultPrevented) release(id)
  } }
}

function render(entry: Entry) {
  const { scope: _scope, context, dedupeKey: _dedupeKey, persistent, ...options } = entry.options
  const description = context
    ? typeof options.description === 'string' || options.description == null
      ? [context, options.description].filter(Boolean).join('\n')
      : createElement(Fragment, null, context, createElement('br'), typeof options.description === 'function' ? options.description() : options.description)
    : options.description
  const data: ExternalToast = {
    ...options,
    id: entry.id,
    description,
    // Actionable failures remain available until handled or explicitly closed.
    duration: keyboardFocusWithin ? Infinity : options.duration ?? (persistent || options.action ? Infinity : NOTIFICATION_DURATIONS[entry.kind]),
    action: wrapAction(options.action, entry.id),
    cancel: wrapAction(options.cancel, entry.id, true),
    onDismiss: item => { release(entry.id); options.onDismiss?.(item) },
    onAutoClose: item => { release(entry.id); options.onAutoClose?.(item) },
  }
  sonner[entry.kind](entry.title, data)
}

function drain() {
  while (surfaces > 0 && active.size < NOTIFICATION_LIMIT && waiting.length) {
    // An explicit id may be re-issued during its exit. Wait for the old DOM node.
    const index = waiting.findIndex(entry => !active.has(entry.id))
    if (index === -1) break
    const [entry] = waiting.splice(index, 1)
    active.set(entry.id, entry)
    render(entry)
  }
}

/** Mounted by AppToaster. Queued items do not start an invisible expiration timer. */
export function mountNotificationSurface() {
  surfaces += 1
  drain()
  return () => {
    surfaces = Math.max(0, surfaces - 1)
    if (surfaces) return
    for (const timer of releaseTimers) clearTimeout(timer)
    releaseTimers.clear()
    for (const id of active.keys()) sonner.dismiss(id)
    active.clear()
    waiting.length = 0
    keyboardFocusWithin = false
  }
}

function publish(kind: Kind, title: Title, options: NotificationOptions = {}): Id {
  const scope = options.scope ?? (typeof location === 'undefined' ? '' : location.pathname)
  const key = options.dedupeKey ?? (typeof title === 'string'
    ? `${scope}\u0000${kind}\u0000${title}\u0000${typeof options.description === 'string' ? options.description : ''}\u0000${options.context ?? ''}`
    : undefined)
  const existing = [...active.values(), ...waiting].find(entry => !entry.closing && (options.id !== undefined ? entry.id === options.id : key !== undefined && entry.key === key))
  if (existing) {
    existing.kind = kind
    existing.title = title
    existing.options = options
    if (active.has(existing.id)) render(existing)
    return existing.id
  }
  const entry: Entry = { id: options.id ?? `notice-${++sequence}`, kind, title, options, key }
  // Previews/tests without the shared surface keep the normal Sonner API behavior.
  if (!surfaces) { render(entry); return entry.id }
  waiting.push(entry)
  drain()
  return entry.id
}

function dismiss(id?: Id) {
  if (id === undefined) {
    waiting.length = 0
    for (const activeId of active.keys()) release(activeId)
  } else {
    const index = waiting.findIndex(entry => entry.id === id)
    if (index !== -1) waiting.splice(index, 1)
    release(id)
  }
  return sonner.dismiss(id)
}

/** Compatible with existing message/success/error/loading call sites. */
export const toast = Object.assign(
  (title: Title, options?: NotificationOptions) => publish('message', title, options),
  {
    success: (title: ReactNode, options?: NotificationOptions) => publish('success', title, options),
    info: (title: ReactNode, options?: NotificationOptions) => publish('info', title, options),
    warning: (title: ReactNode, options?: NotificationOptions) => publish('warning', title, options),
    error: (title: ReactNode, options?: NotificationOptions) => publish('error', title, options),
    message: (title: Title, options?: NotificationOptions) => publish('message', title, options),
    loading: (title: ReactNode, options?: NotificationOptions) => publish('loading', title, options),
    dismiss,
    getHistory: () => sonner.getHistory(),
    getToasts: (): ToastT[] => sonner.getToasts(),
  },
)
