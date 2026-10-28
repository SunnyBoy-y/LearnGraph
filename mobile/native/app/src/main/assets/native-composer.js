/* Runs only in the trusted top document. Arguments are supplied by the native host. */
(function (request) {
  if (!request || !request.id || window.__lgComposerHandoff === request.id) return;
  const path = '/w/' + request.workspaceId + '/chat/' + request.sessionId;
  if (location.pathname !== path) return;
  window.__lgComposerHandoff = request.id;
  const selectors = {
    attachments: 'button[aria-label="打开对话功能菜单"]',
    photo: 'button[aria-label="现场拍照并作为附件"]',
    goal: '.chat-workbench-toolbar button[aria-label="目标"],.chat-workbench-toolbar button[aria-label="目标中"]',
    graph: '[role="menuitem"]', image: '[role="menuitem"]',
    tools: 'button[aria-label="打开对话功能菜单"]',
    dictation: 'button[aria-label="开始语音输入"]',
    voice: 'button[aria-label="开始全双工语音"]',
    models: 'button.topbar-model-trigger,button.chat-composer__mode',
  };
  if (!selectors[request.command]) return;
  const started = Date.now();
  // An explicit empty draft means the native user cleared it. Missing draft is
  // the legacy protocol, which leaves the Web composer's existing text alone.
  let draftApplied = typeof request.draft !== 'string';
  let menuOpened = false;
  let finished = false;
  let observer;
  let timer;
  let prefsApplied = !request.options;
  let prefsRequested = false;
  const scope = { workspaceId: request.workspaceId, sessionId: request.sessionId };
  const sync = { ...scope, requestId: request.id, requestedPrefs: request.options || null, initialPrefs: null, outboundApplied: false };
  window.__lgComposerSync = sync;
  function existingPrefs() {
    if (request.sessionId === 'new') return null;
    try {
      const value = JSON.parse(localStorage.getItem('learngraph:session-composer-prefs') || '{}')[request.sessionId];
      return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
    } catch { return null; }
  }
  function finish(message) {
    if (finished) return;
    finished = true;
    observer?.disconnect(); clearInterval(timer);
    window.LearnGraphNative?.completeComposerAction?.(request.id, draftApplied);
    if (message) {
      const notice = document.createElement('p');
      notice.setAttribute('role', 'alert');
      notice.style.cssText = 'position:fixed;z-index:2147483647;bottom:24px;left:16px;right:16px;padding:12px;border-radius:12px;background:#262626;color:white;font:14px sans-serif';
      notice.textContent = message;
      notice.addEventListener('click', () => notice.remove());
      document.body.append(notice);
      setTimeout(() => notice.remove(), 7000);
    }
  }
  function attempt() {
    if (finished) return;
    if (location.pathname !== path) { finish(); return; }
    if (Date.now() - started > 15_000) { finish('功能尚未就绪，请在当前页面重试；输入内容已保留。'); return; }
    const composer = document.querySelector('textarea[aria-label="输入消息"]');
    if (!composer) return;
    if (!prefsApplied) {
      const bridge = window.LearnGraphComposer;
      if (bridge?.version === 1) {
        const snapshot = bridge.snapshot(scope);
        if (!snapshot?.ready) return;
        if (!prefsRequested) {
          const result = bridge.apply({ id: request.id, ...scope, prefs: request.options });
          if (result === 'pending') return;
          if (result !== 'accepted') { finish('当前会话设置未能恢复，请检查当前选择后继续。'); return; }
          prefsRequested = true;
        }
        // Wait for the public snapshot to acknowledge the committed React update.
        if (snapshot.appliedRequestId !== request.id) return;
        sync.outboundApplied = true;
      } else {
        // An old server has no safe public state setter. Keep its user's live
        // selection intact; return capture imports only subsequently changed fields.
        sync.initialPrefs = existingPrefs();
      }
      prefsApplied = true;
    }
    if (!draftApplied) {
      const old = composer.value || '';
      const text = request.draft === '' ? '' : old && old !== request.draft ? old + '\n\n' + request.draft : request.draft;
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(composer, text);
      composer.dispatchEvent(new Event('input', { bubbles: true }));
      draftApplied = true;
    }
    if ((request.command === 'goal' || request.command === 'image') && document.querySelector(request.command === 'goal' ? '.chat-goal-mode-banner,button[aria-label="退出目标设定模式"]' : '[aria-label="绘图设置"]')) { finish(); return; }
    if (['tools', 'attachments', 'graph', 'image'].includes(request.command)) {
      let menu = document.querySelector('.chat-plus-menu[role="menu"]:not([data-state="closed"])');
      if (!menu && !menuOpened) {
        const trigger = document.querySelector(selectors.tools);
        if (!trigger || trigger.disabled) return;
        menuOpened = true;
        // Radix opens on pointerdown or keydown, not element.click(). ArrowDown
        // explicitly opens instead of toggling an already expanded trigger.
        if (trigger.getAttribute('aria-expanded') !== 'true') {
          trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
        }
        menu = document.querySelector('.chat-plus-menu[role="menu"]:not([data-state="closed"])');
      }
      if (!menu) return;
      // Opening a file chooser needs a real Web user activation. Leave the
      // original menu mounted so its attachment item receives that next tap.
      if (request.command === 'tools' || request.command === 'attachments') { finish(); return; }
      const text = request.command === 'image' ? '绘图' : '图谱创建和更新';
      const target = Array.from(menu.querySelectorAll('[role="menuitem"]')).find(item => item.textContent.trim() === text);
      if (!target) return;
      if (target.getAttribute('data-disabled') !== null || target.getAttribute('aria-disabled') === 'true') { finish('当前配置暂不支持此功能，请检查模型、权限和会话状态。'); return; }
      target.click(); finish(); return;
    }
    const target = document.querySelector(selectors[request.command]);
    if (!target || target.disabled) return;
    if (request.command === 'models') {
      if (target.getAttribute('aria-expanded') === 'true') { finish(); return; }
      if (!menuOpened) {
        menuOpened = true;
        target.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
      }
      return;
    }
    // A second tap must not turn off an already active goal mode.
    if (request.command === 'goal' && target.getAttribute('aria-pressed') === 'true') { finish(); return; }
    finish(); target.click();
  }
  observer = new MutationObserver(attempt);
  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'aria-disabled'] });
  timer = setInterval(attempt, 250);
  attempt();
})
