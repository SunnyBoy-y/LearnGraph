import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { promoteComposerScope, readComposerField, useComposerField, writeComposerField } from './composer-session-state'

const scope = () => `draft-test:${crypto.randomUUID()}`
describe('session-owned composer state', () => {
  it.each([
    ['files', null, []],
    ['pendingFiles', {}, []],
    ['stickers', null, []],
    ['queue', 'invalid queue', []],
    ['missingAttachments', {}, []],
    ['text', { text: 'invalid text wrapper' }, ''],
    ['storageWarning', false, ''],
    ['stopState', null, { phase: 'idle' }],
    ['stopState', { phase: 1 }, { phase: 'idle' }],
    ['longPaste', {}, null],
  ])('recovers malformed persisted %s without clearing the stored draft', (field, invalid, fallback) => {
    const owner = scope()
    const stored = JSON.stringify({ version: 2, updatedAt: Date.now(), fields: { text: '保留有效草稿', [field]: invalid } })
    localStorage.setItem('lg:composer-state:v2:' + owner, stored)
    const value = readComposerField(owner, field as string, fallback)
    expect(value).toEqual(fallback)
    expect(readComposerField(owner, field as string, fallback)).toBe(value)
    if (field !== 'text') expect(readComposerField(owner, 'text', '')).toBe('保留有效草稿')
    expect(localStorage.getItem('lg:composer-state:v2:' + owner)).toBe(stored)
  })
  it('preserves a nullable long paste, valid stop state and every unrelated draft', () => {
    const owner = scope(), other = scope()
    localStorage.setItem('lg:composer-state:v2:' + owner, JSON.stringify({ version: 2, updatedAt: Date.now(), fields: {
      files: null, longPaste: '长文本草稿', stopState: { phase: 'confirmed', messageId: 'answer-1' },
    } }))
    writeComposerField(other, 'text', '另一个会话的草稿', '')
    expect(readComposerField(owner, 'files', [])).toEqual([])
    expect(readComposerField(owner, 'longPaste', null)).toBe('长文本草稿')
    expect(readComposerField(owner, 'stopState', { phase: 'idle' })).toEqual({ phase: 'confirmed', messageId: 'answer-1' })
    expect(readComposerField(other, 'text', '')).toBe('另一个会话的草稿')
  })
  it('rejects an array masquerading as the fields record and restores legacy text', () => {
    const owner = scope()
    localStorage.setItem('lg:composer-state:v2:' + owner, JSON.stringify({ version: 2, updatedAt: Date.now(), fields: ['invalid'] }))
    localStorage.setItem('lg:composer-draft:' + owner, '旧版有效草稿')
    expect(readComposerField(owner, 'text', '')).toBe('旧版有效草稿')
    expect(readComposerField(owner, 'files', [])).toEqual([])
  })
  it('saves the very last keystroke synchronously before switching sessions', () => {
    const a = scope(), b = scope()
    const hook = renderHook(({ owner }) => useComposerField(owner, 'text', ''), { initialProps: { owner: a } })
    act(() => hook.result.current[1]('最后一个字'))
    expect(JSON.parse(localStorage.getItem('lg:composer-state:v2:' + a)!).fields.text).toBe('最后一个字')
    hook.rerender({ owner: b })
    expect(hook.result.current[0]).toBe('')
    act(() => hook.result.current[1]('B 草稿'))
    hook.rerender({ owner: a })
    expect(hook.result.current[0]).toBe('最后一个字')
  })
  it('binds delayed setters to their immutable owner', () => {
    const a = scope(), b = scope()
    const hook = renderHook(({ owner }) => useComposerField<string[]>(owner, 'queue', []), { initialProps: { owner: a } })
    const setA = hook.result.current[1]
    hook.rerender({ owner: b })
    act(() => setA(previous => [...previous, 'A failed upload returns to A']))
    expect(hook.result.current[0]).toEqual([])
    hook.rerender({ owner: a })
    expect(hook.result.current[0]).toEqual(['A failed upload returns to A'])
  })
  it('rejects stale asynchronous setters after the same scope is explicitly cleared and recreated', () => {
    const owner = scope()
    const old = renderHook(() => useComposerField<string[]>(owner, 'stickers', []))
    act(() => old.result.current[1](['old sticker']))
    const delayedOldSetter = old.result.current[1]
    old.unmount()
    localStorage.removeItem('lg:composer-state:v2:' + owner)
    const fresh = renderHook(() => useComposerField<string[]>(owner, 'stickers', []))
    act(() => fresh.result.current[1](['new sticker 1', 'new sticker 2']))
    act(() => delayedOldSetter([]))
    expect(fresh.result.current[0]).toEqual(['new sticker 1', 'new sticker 2'])
    fresh.unmount()
  })
  it('restores direct File bytes across unmount, not a JSON File placeholder', () => {
    const owner = scope()
    const file = new File(['actual bytes'], 'notes.txt', { type: 'text/plain' })
    const hook = renderHook(() => useComposerField<unknown[]>(owner, 'files', []))
    act(() => hook.result.current[1]([{ type: 'file', filename: file.name, localFile: file, url: 'blob:owned-file' }]))
    hook.unmount()
    const next = renderHook(() => useComposerField<unknown[]>(owner, 'files', []))
    expect(next.result.current[0][0]).toMatchObject({ localFile: file, filename: 'notes.txt' })
    expect((next.result.current[0][0] as { localFile: File }).localFile.size).toBe(12)
  })
  it('keeps new project canvases separate and migrates only the promoted draft', () => {
    const workspace = scope(), a = `${workspace}:new:project-a`, b = `${workspace}:new:project-b`, durable = `${workspace}:created`
    writeComposerField(a, 'text', 'new A', '')
    writeComposerField(b, 'text', 'new B', '')
    writeComposerField(a, 'queue', [{ sessionId: 'new', text: 'queued A' }], [])
    promoteComposerScope(a, durable, 'created')
    expect(readComposerField(durable, 'text', '')).toBe('new A')
    expect(readComposerField(durable, 'queue', [])).toEqual([{ sessionId: 'created', text: 'queued A' }])
    expect(readComposerField(b, 'text', '')).toBe('new B')
    expect(readComposerField(a, 'text', '')).toBe('')
  })
})
