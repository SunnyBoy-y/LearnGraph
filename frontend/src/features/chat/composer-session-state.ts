import { useCallback, useRef, useSyncExternalStore, type SetStateAction } from 'react'

/** One immutable owner for drafts, attachments and queues, including unsent /new canvases. */
type DraftRecord = { fields: Record<string, unknown>; updatedAt: number; changed: Set<string>; persisted: boolean }
const records = new Map<string, DraftRecord>()
const listeners = new Map<string, Set<() => void>>()
let database: Promise<IDBDatabase | null> | undefined
const STORAGE_PREFIX = 'lg:composer-state:v2:'

function isFieldRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

/** Storage is untrusted across versions; TypeScript casts cannot validate it. */
function matchesFieldShape(field: string, value: unknown, initial: unknown): boolean {
  if (field === 'longPaste') return value === null || typeof value === 'string'
  if (Array.isArray(initial)) return Array.isArray(value)
  if (initial === null) return value === null
  if (isFieldRecord(initial)) {
    return isFieldRecord(value) && Object.entries(initial).every(([key, seed]) =>
      Object.hasOwn(value, key) && typeof value[key] === typeof seed)
  }
  return value !== null && typeof value === typeof initial
}

function openDatabase(): Promise<IDBDatabase | null> {
  if (database) return database
  database = new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') { resolve(null); return }
    try {
      const request = indexedDB.open('learngraph-composer', 1)
      request.onupgradeneeded = () => request.result.createObjectStore('drafts')
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => resolve(null)
      request.onblocked = () => resolve(null)
    } catch { resolve(null) }
  })
  return database
}
function emit(scope: string) { listeners.get(scope)?.forEach(listener => listener()) }

/** Rebuild preview URLs from durable Blobs; never reuse a previous document's blob URL. */
function revive(value: unknown, missing: string[]): unknown {
  if (Array.isArray(value)) return value.map(item => revive(item, missing)).filter(item => item !== undefined)
  if (!value || typeof value !== 'object') return value
  const part = value as Record<string, unknown>
  if (part.type === 'file') {
    if (part.localFile instanceof Blob) {
      return { ...part, url: URL.createObjectURL(part.localFile) }
    }
    if (part.localFileMissing || (typeof part.url === 'string' && part.url.startsWith('blob:'))) {
      missing.push(String(part.filename || '本地附件'))
      return undefined
    }
  }
  return value
}
function getRecord(scope: string): DraftRecord {
  const existing = records.get(scope)
  if (existing) {
    // Explicit browser-data removal must also invalidate the resident cache.
    // Storage-disabled WebViews retain their in-memory fallback instead.
    let removed = false
    try { removed = existing.persisted && localStorage.getItem(STORAGE_PREFIX + scope) === null } catch { /* private mode */ }
    if (!removed) return existing
    records.delete(scope)
  }
  let fields: Record<string, unknown> = {}
  let updatedAt = 0
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_PREFIX + scope) || 'null')
    if (saved && saved.version === 2 && isFieldRecord(saved.fields)) {
      fields = saved.fields; updatedAt = Number(saved.updatedAt) || 0
    } else {
      const legacy = localStorage.getItem('lg:composer-draft:' + scope)
      if (legacy) fields.text = legacy
    }
  } catch { /* Private mode still has the in-memory draft. */ }
  const missing: string[] = []
  if (fields.files) fields.files = revive(fields.files, missing)
  if (missing.length) fields.missingAttachments = missing
  const record: DraftRecord = { fields, updatedAt, changed: new Set(), persisted: updatedAt > 0 }
  records.set(scope, record)
  void openDatabase().then(db => {
    if (!db) return
    try {
      const request = db.transaction('drafts', 'readonly').objectStore('drafts').get(scope)
      request.onsuccess = () => {
        const saved: unknown = request.result
        if (!isFieldRecord(saved) || !isFieldRecord(saved.fields) || typeof saved.updatedAt !== 'number' ||
          !Number.isFinite(saved.updatedAt) || saved.updatedAt < updatedAt || records.get(scope) !== record) return
        const missingFiles: string[] = []
        for (const [field, value] of Object.entries(saved.fields)) {
          if (record.changed.has(field)) continue
          // JSON always has the most recent text; IndexedDB preserves actual File/Blob bytes.
          record.fields[field] = field === 'files' ? revive(value, missingFiles)
            : field === 'queue' && Array.isArray(value) ? value.filter(isFieldRecord).map(item => ({ ...item, files: revive(item.files, missingFiles) }))
            : value
        }
        if (!record.changed.has('files')) record.fields.missingAttachments = missingFiles
        emit(scope)
      }
    } catch { /* Never prevent typing when storage is unavailable. */ }
  })
  return record
}
function persist(scope: string, record: DraftRecord) {
  const { updatedAt } = record
  try {
    localStorage.setItem(STORAGE_PREFIX + scope, JSON.stringify({ version: 2, updatedAt, fields: record.fields }, (key, value) => {
      // Queues need their complete binary snapshot. Restore them only from IndexedDB.
      if (key === 'queue') return undefined
      if (key === 'localFile') return undefined
      if (value && typeof value === 'object' && value.type === 'file' && value.localFile instanceof Blob) {
        const { localFile: _file, ...part } = value
        return { ...part, localFileMissing: true }
      }
      return value
    }))
    record.persisted = true
  } catch { /* Quota failures leave the complete current-window draft intact. */ }
  void openDatabase().then(db => {
    if (record.updatedAt !== updatedAt || records.get(scope) !== record) return
    if (!db) {
      if ((record.fields.files as unknown[] | undefined)?.length || (record.fields.queue as unknown[] | undefined)?.length) {
        record.fields.storageWarning = '当前浏览器无法持久保存本地附件和发送队列；切换会话仍会保留，关闭页面前请先发送或备份。'
        emit(scope)
      }
      return
    }
    try {
      const transaction = db.transaction('drafts', 'readwrite')
      transaction.objectStore('drafts').put({ fields: record.fields, updatedAt }, scope)
      transaction.onerror = () => {
        if ((record.fields.files as unknown[] | undefined)?.length) {
          record.fields.storageWarning = '本地附件暂时无法持久保存；切换会话仍会保留，关闭页面前请先发送或备份。'
          emit(scope)
        }
      }
    } catch { /* Some WebViews do not support cloning File; do not lose the live draft. */ }
  })
}

export function readComposerField<T>(scope: string, field: string, initial: T): T {
  const record = getRecord(scope)
  if (!Object.hasOwn(record.fields, field) || !matchesFieldShape(field, record.fields[field], initial)) {
    // Repair only the resident field, not the stored record or other drafts.
    // Cache the fallback so useSyncExternalStore sees a stable snapshot.
    record.fields[field] = initial
  }
  return record.fields[field] as T
}
export function writeComposerField<T>(scope: string, field: string, update: SetStateAction<T>, initial: T, expectedRecord?: DraftRecord): void {
  const record = getRecord(scope)
  if (expectedRecord && expectedRecord !== record) return
  const current = readComposerField(scope, field, initial)
  const next = typeof update === 'function' ? (update as (previous: T) => T)(current) : update
  if (Object.is(current, next)) return
  record.fields = { ...record.fields, [field]: next }
  record.changed.add(field)
  record.updatedAt = Math.max(Date.now(), record.updatedAt + 1)
  persist(scope, record)
  emit(scope)
}

/** The setter captures its scope: a delayed A callback can never mutate B's draft. */
export function useComposerField<T>(scope: string, field: string, initial: T): [T, (update: SetStateAction<T>) => void] {
  const seedRef = useRef({ scope, field, value: initial })
  if (seedRef.current.scope !== scope || seedRef.current.field !== field) seedRef.current = { scope, field, value: initial }
  const seed = seedRef.current.value
  const subscribe = useCallback((listener: () => void) => {
    const set = listeners.get(scope) ?? new Set<() => void>()
    listeners.set(scope, set); set.add(listener)
    return () => { set.delete(listener); if (!set.size) listeners.delete(scope) }
  }, [scope])
  const record = getRecord(scope)
  const value = useSyncExternalStore(subscribe, () => readComposerField(scope, field, seed))
  const setValue = useCallback((update: SetStateAction<T>) => writeComposerField(scope, field, update, seed, record), [scope, field, seed, record])
  return [value, setValue]
}

/** Promote only this visible draft; never merge unrelated project/new canvases. */
export function promoteComposerScope(from: string, to: string, sessionId: string) {
  if (from === to) return
  const source = getRecord(from)
  const target = getRecord(to)
  target.fields = { ...source.fields, ...target.fields,
    queue: ((source.fields.queue ?? []) as Array<Record<string, unknown>>).map(item => ({ ...item, sessionId })),
  }
  target.updatedAt = Date.now(); persist(to, target); emit(to)
  source.fields = {}; source.updatedAt = Date.now(); persist(from, source); emit(from)
}
