import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'

const script = readFileSync('../mobile/native/app/src/main/assets/native-composer.js', 'utf8')
const captureScript = readFileSync('../mobile/native/app/src/main/assets/native-composer-state.js', 'utf8')

describe('native composer handoff uses actual mounted Web controls', () => {
  const acknowledge = vi.fn()
  const request = { id: 'test-handoff', workspaceId: 'workspace', sessionId: 'session', command: 'attachments', draft: '' }
  beforeEach(() => {
    vi.useFakeTimers()
    window.history.replaceState({}, '', '/w/workspace/chat/session')
    document.body.innerHTML = '<textarea aria-label="输入消息"></textarea>'
    localStorage.clear()
    Object.assign(window, { LearnGraphNative: { completeComposerAction: acknowledge }, __lgComposerHandoff: undefined, LearnGraphComposer: undefined, __lgComposerSync: undefined })
    acknowledge.mockClear()
  })
  afterEach(() => { vi.runOnlyPendingTimers(); vi.useRealTimers(); vi.restoreAllMocks(); document.body.innerHTML = '' })
  const run = (value: Omit<typeof request, 'draft'> & { draft?: string; options?: unknown } = request) => window.eval(`${script}(${JSON.stringify(value)});`)

  function mountRadixMenu(disabledImage = false) {
    const trigger = document.createElement('button')
    trigger.setAttribute('aria-label', '打开对话功能菜单')
    trigger.setAttribute('aria-expanded', 'false')
    const file = document.createElement('input')
    file.type = 'file'
    file.setAttribute('aria-label', 'Upload files')
    file.hidden = true
    document.body.append(trigger, file)

    // jsdom has no browser activation policy. Model the Chromium boundary
    // explicitly so calling the file input from the handoff fails this test.
    let activated = false
    const chooser = vi.fn()
    const fileClick = vi.spyOn(file, 'click').mockImplementation(() => {
      if (!activated) throw new DOMException('File chooser dialog can only be shown with a user activation', 'NotAllowedError')
      chooser()
    })
    const menu = document.createElement('div')
    menu.className = 'chat-plus-menu'
    menu.setAttribute('role', 'menu')
    menu.setAttribute('data-state', 'open')
    const attachment = document.createElement('div')
    attachment.setAttribute('role', 'menuitem')
    attachment.textContent = '添加资料'
    attachment.addEventListener('click', () => file.click())
    menu.append(attachment)
    const selection = vi.fn()
    for (const label of ['绘图', '图谱创建和更新']) {
      const item = document.createElement('div')
      item.setAttribute('role', 'menuitem')
      item.textContent = label
      if (disabledImage && label === '绘图') item.setAttribute('aria-disabled', 'true')
      item.addEventListener('click', () => selection(label))
      menu.append(item)
    }
    const triggerClick = vi.fn()
    trigger.addEventListener('click', triggerClick)
    const openKey = vi.fn((event: KeyboardEvent) => {
      if (event.key !== 'ArrowDown' || trigger.getAttribute('aria-expanded') === 'true') return
      trigger.setAttribute('aria-expanded', 'true')
      // Like Radix, a trigger can be expanded before its Portal is mounted.
      setTimeout(() => document.body.append(menu), 50)
    })
    trigger.addEventListener('keydown', openKey)
    return {
      trigger, menu, chooser, fileClick, selection, triggerClick, openKey,
      assertActivationRequired() {
        expect(() => file.click()).toThrow('File chooser dialog can only be shown with a user activation')
        fileClick.mockClear()
      },
      clickAttachmentWithActivation() {
        activated = true
        try { attachment.click() } finally { activated = false }
      },
    }
  }

  it.each(['tools', 'attachments'])('opens the original menu for %s and leaves the file chooser to a real click', async (command) => {
    const menu = mountRadixMenu()
    menu.assertActivationRequired()
    const action = { ...request, command, draft: '原生草稿' }
    run(action)
    expect(document.querySelector('textarea')!.value).toBe('原生草稿')
    expect(acknowledge).not.toHaveBeenCalled()
    expect(menu.fileClick).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(49)
    expect(acknowledge).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(menu.openKey).toHaveBeenCalledTimes(1)
    expect(menu.triggerClick).not.toHaveBeenCalled()
    expect(menu.menu.isConnected).toBe(true)
    expect(acknowledge).toHaveBeenCalledExactlyOnceWith(request.id, true)
    expect(menu.fileClick).not.toHaveBeenCalled()
    run(action)
    await vi.advanceTimersByTimeAsync(300)
    expect(menu.openKey).toHaveBeenCalledTimes(1)
    expect(acknowledge).toHaveBeenCalledTimes(1)
    expect(menu.fileClick).not.toHaveBeenCalled()
    menu.clickAttachmentWithActivation()
    expect(menu.fileClick).toHaveBeenCalledTimes(1)
    expect(menu.chooser).toHaveBeenCalledTimes(1)
  })

  it('reuses an already open menu without toggling or selecting an attachment', () => {
    const menu = mountRadixMenu()
    menu.trigger.setAttribute('aria-expanded', 'true')
    document.body.append(menu.menu)
    run()
    expect(acknowledge).toHaveBeenCalledExactlyOnceWith(request.id, true)
    expect(menu.openKey).not.toHaveBeenCalled()
    expect(menu.triggerClick).not.toHaveBeenCalled()
    expect(menu.fileClick).not.toHaveBeenCalled()
  })

  it.each([
    ['image', '绘图'], ['graph', '图谱创建和更新'],
  ])('waits for the original delayed Portal before selecting %s', async (command, label) => {
    const menu = mountRadixMenu()
    // A matching item from another page must not receive this action.
    const unrelated = document.createElement('div')
    unrelated.setAttribute('role', 'menuitem')
    unrelated.textContent = label
    const unrelatedSelection = vi.fn()
    unrelated.addEventListener('click', unrelatedSelection)
    document.body.append(unrelated)
    run({ ...request, command })
    expect(menu.selection).not.toHaveBeenCalled()
    expect(acknowledge).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(49)
    expect(menu.selection).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(menu.selection).toHaveBeenCalledExactlyOnceWith(label)
    expect(acknowledge).toHaveBeenCalledExactlyOnceWith(request.id, true)
    expect(menu.openKey).toHaveBeenCalledTimes(1)
    expect(menu.triggerClick).not.toHaveBeenCalled()
    expect(unrelatedSelection).not.toHaveBeenCalled()
    expect(menu.fileClick).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(300)
    expect(menu.selection).toHaveBeenCalledTimes(1)
  })

  it('respects a disabled image item in the original menu', async () => {
    const menu = mountRadixMenu(true)
    run({ ...request, command: 'image' })
    await vi.advanceTimersByTimeAsync(50)
    expect(menu.selection).not.toHaveBeenCalled()
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('当前配置暂不支持')
    expect(acknowledge).toHaveBeenCalledTimes(1)
    expect(menu.fileClick).not.toHaveBeenCalled()
  })

  it('merges the native draft using an input event before starting dictation', () => {
    const textarea = document.querySelector('textarea')!
    textarea.value = 'Web 草稿'
    const update = vi.fn(); textarea.addEventListener('input', update)
    const button = document.createElement('button'); button.setAttribute('aria-label', '开始语音输入')
    const click = vi.fn(); button.addEventListener('click', click); document.body.append(button)
    run({ ...request, command: 'dictation', draft: '原生草稿' })
    expect(textarea.value).toBe('Web 草稿\n\n原生草稿')
    expect(update).toHaveBeenCalledTimes(1)
    expect(click).toHaveBeenCalledTimes(1)
  })

  it('clears an old Web draft when native explicitly hands off an empty string', () => {
    const textarea = document.querySelector('textarea')!
    textarea.value = '上次留下的 Web 草稿'
    const update = vi.fn(); textarea.addEventListener('input', update)
    const button = document.createElement('button'); button.setAttribute('aria-label', '开始语音输入')
    document.body.append(button)
    run({ ...request, command: 'dictation', draft: '' })
    expect(textarea.value).toBe('')
    expect(update).toHaveBeenCalledTimes(1)
    expect(acknowledge).toHaveBeenCalledExactlyOnceWith(request.id, true)
    expect(JSON.parse(window.eval(captureScript)).draft).toBe('')
  })

  it('leaves an existing Web draft intact for legacy requests without a draft field', () => {
    const textarea = document.querySelector('textarea')!
    textarea.value = '旧客户端的 Web 草稿'
    const update = vi.fn(); textarea.addEventListener('input', update)
    const button = document.createElement('button'); button.setAttribute('aria-label', '开始语音输入')
    document.body.append(button)
    const { draft: _draft, ...legacy } = request
    run({ ...legacy, command: 'dictation' })
    expect(textarea.value).toBe('旧客户端的 Web 草稿')
    expect(update).not.toHaveBeenCalled()
    expect(acknowledge).toHaveBeenCalledExactlyOnceWith(request.id, true)
  })

  it('does not switch an already active goal off', () => {
    document.body.insertAdjacentHTML('beforeend', '<section class="chat-workbench-toolbar"><button aria-label="目标中" aria-pressed="true"></button></section>')
    const click = vi.fn(); document.querySelector('button')!.addEventListener('click', click)
    run({ ...request, command: 'goal' })
    expect(click).not.toHaveBeenCalled()
    expect(acknowledge).toHaveBeenCalledTimes(1)
  })

  it('never runs on another conversation and reports controls that remain unavailable', async () => {
    run({ ...request, sessionId: 'other' })
    expect(acknowledge).not.toHaveBeenCalled()
    run({ ...request, command: 'voice' })
    await vi.advanceTimersByTimeAsync(15_500)
    expect(acknowledge).toHaveBeenCalledTimes(1)
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('尚未就绪')
  })

  it('opens the original model menu with Radix keyboard activation', () => {
    const trigger = document.createElement('button'); trigger.className = 'topbar-model-trigger'
    trigger.setAttribute('aria-expanded', 'false')
    const click = vi.fn(); trigger.addEventListener('click', click)
    const key = vi.fn((event: KeyboardEvent) => { if (event.key === 'ArrowDown') trigger.setAttribute('aria-expanded', 'true') })
    trigger.addEventListener('keydown', key); document.body.append(trigger)
    run({ ...request, command: 'models' })
    expect(key).toHaveBeenCalledTimes(1)
    expect(click).not.toHaveBeenCalled()
  })

  it('applies preferences through the scoped contract before opening controls, waiting for React acknowledgement', async () => {
    const menu = mountRadixMenu()
    const options = { responseMode: 'fast', thinkingMode: 'off', searchRoute: 'disabled', generationMode: 'text', modelId: 'selected-model' } as const
    let appliedRequestId: string | null = null
    const apply = vi.fn(() => { setTimeout(() => { appliedRequestId = request.id }, 50); return 'accepted' as const })
    window.LearnGraphComposer = { version: 1, snapshot: (scope) => ({ version: 1, ...scope, ready: true, prefs: options, appliedRequestId }), apply }
    run({ ...request, command: 'tools', options })
    expect(apply).toHaveBeenCalledExactlyOnceWith({ id: request.id, workspaceId: 'workspace', sessionId: 'session', prefs: options })
    expect(menu.openKey).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(300)
    expect(menu.openKey).toHaveBeenCalledTimes(1)
    expect(acknowledge).toHaveBeenCalledExactlyOnceWith(request.id, true)
    expect(apply).toHaveBeenCalledTimes(1)
    const capture = JSON.parse(window.eval(captureScript))
    expect(capture.composer).toMatchObject({ source: 'live', prefs: options, limited: false, outboundApplied: true })
  })

  it('captures unsent new-session live settings and never invents a legacy new-session snapshot', () => {
    window.history.replaceState({}, '', '/w/workspace/chat/new')
    const prefs = { responseMode: 'agentic' as const, thinkingMode: 'xhigh' as const, searchRoute: 'disabled' as const, generationMode: 'image' as const, imageProviderId: 'ip', imageModelId: 'im' }
    window.LearnGraphComposer = { version: 1, snapshot: scope => ({ version: 1, ...scope, ready: true, prefs, appliedRequestId: null }), apply: () => 'accepted' }
    expect(JSON.parse(window.eval(captureScript)).composer).toMatchObject({ sessionId: 'new', prefs, source: 'live' })
    delete window.LearnGraphComposer
    expect(JSON.parse(window.eval(captureScript)).composer).toMatchObject({ sessionId: 'new', prefs: null, source: 'unavailable', limited: true })
  })

  it.each(['rejected', 'accepted'] as const)('keeps native choices when an empty-draft handoff is %s without a committed live request', async (outcome) => {
    const oldPrefs = { responseMode: 'agentic', thinkingMode: 'medium', searchRoute: 'auto', generationMode: 'text', modelId: 'old-web-model' } as const
    const options = { ...oldPrefs, responseMode: 'fast', thinkingMode: 'off', modelId: 'native-model' }
    window.LearnGraphComposer = { version: 1, snapshot: scope => ({ version: 1, ...scope, ready: true, prefs: oldPrefs, appliedRequestId: null }), apply: () => outcome }
    run({ ...request, command: 'tools', draft: '', options })
    await vi.advanceTimersByTimeAsync(15_500)
    // An explicit clear is not acknowledged before prefs are ready and the input event ran.
    expect(acknowledge).toHaveBeenCalledExactlyOnceWith(request.id, false)
    expect(JSON.parse(window.eval(captureScript)).composer).toMatchObject({ prefs: null, source: 'live-unconfirmed', limited: true, outboundApplied: false })
  })

  it('accepts React confirmation before the handoff polling tick and preserves the latest user choice', () => {
    const original = { responseMode: 'fast', thinkingMode: 'off', searchRoute: 'disabled', generationMode: 'text' } as const
    let committed = false
    let current = original as { responseMode: 'fast' | 'agentic'; thinkingMode: 'off' | 'xhigh'; searchRoute: 'disabled'; generationMode: 'text' }
    window.LearnGraphComposer = { version: 1, snapshot: scope => ({ version: 1, ...scope, ready: true, prefs: current, appliedRequestId: committed ? request.id : null }), apply: () => 'accepted' }
    run({ ...request, command: 'tools', options: original })
    expect(acknowledge).not.toHaveBeenCalled()
    committed = true
    current = { ...original, responseMode: 'agentic', thinkingMode: 'xhigh' }
    expect(JSON.parse(window.eval(captureScript)).composer).toMatchObject({ prefs: current, source: 'live', limited: false, outboundApplied: true })
  })

  it('legacy servers import only changed stored fields and keep untouched native choices', async () => {
    const menu = mountRadixMenu()
    const oldPrefs = { responseMode: 'agentic', thinkingMode: 'medium', searchRoute: 'auto', generationMode: 'text', modelId: 'web-model' }
    const nativePrefs = { ...oldPrefs, responseMode: 'fast', thinkingMode: 'off', modelId: 'native-model' }
    localStorage.setItem('learngraph:session-composer-prefs', JSON.stringify({ session: oldPrefs }))
    run({ ...request, command: 'tools', options: nativePrefs })
    await vi.advanceTimersByTimeAsync(50)
    expect(menu.openKey).toHaveBeenCalledTimes(1)
    expect(JSON.parse(window.eval(captureScript)).composer).toMatchObject({ prefs: null, source: 'legacy-unchanged', limited: true })
    localStorage.setItem('learngraph:session-composer-prefs', JSON.stringify({ session: { ...oldPrefs, searchRoute: 'disabled' } }))
    expect(JSON.parse(window.eval(captureScript)).composer).toMatchObject({ prefs: { ...nativePrefs, searchRoute: 'disabled' }, source: 'legacy-merge', limited: true })
    expect(document.querySelector('[role="alert"]')).toBeNull()
  })
})
