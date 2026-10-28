(function () {
  if (window.top !== window || window.__lgUiMetrics || !window.LearnGraphNative?.reportUiParity) return;
  window.__lgUiMetrics = true;
  let serial = 0;
  const report = value => window.LearnGraphNative.reportUiParity(JSON.stringify(value));
  const fixtures = [
    ['empty_eyebrow', '.chat-empty-state__eyebrow', '新会话'],
    ['empty_title', '.chat-empty-state h2', '从一个问题开始'],
    ['suggestion', '.chat-empty-state__prompt', '我现在最应该先学什么？'],
    ['toolbar', '.chat-workbench-toolbar__action', '拍照'],
    ['model_trigger', '.topbar-model-trigger', '智能'],
    ['composer', '.chat-composer__textarea', '输入消息，或输入 @ 选择模式 / 文件…'],
    ['model_label', '.chat-model-menu [data-slot="dropdown-menu-label"]', '响应与思考力度'],
    ['search_input', '.workspace-search-input-row input', '搜索文件、会话、消息或 ID…'],
    ['sidebar_heading', '.sidebar-sessions__heading p', '会话'],
    ['sidebar_nav', '.sidebar-nav__primary .sidebar-text', '新对话'],
    ['avatar', '.sidebar-user-avatar', 'U'],
  ];
  const round = value => Math.round(value * 10000) / 10000;
  const seen = new Map();
  function typography() {
    for (const [id, selector, sample] of fixtures) {
      const element = document.querySelector(selector);
      if (!element) continue;
      const style = getComputedStyle(element);
      const bounds = element.getBoundingClientRect();
      const line = document.createElement('div');
      line.dataset.lgMetricsProbe = 'true';
      Object.assign(line.style, { position: 'fixed', left: '-10000px', top: '0', width: 'max-content', visibility: 'hidden', whiteSpace: 'nowrap', margin: '0', padding: '0', border: '0',
        fontFamily: style.fontFamily, fontWeight: style.fontWeight, fontSize: style.fontSize, fontStyle: style.fontStyle, lineHeight: style.lineHeight, letterSpacing: style.letterSpacing });
      line.append(document.createTextNode(sample));
      const baseline = document.createElement('span');
      Object.assign(baseline.style, { display: 'inline-block', width: '0', height: '0', verticalAlign: 'baseline', padding: '0', margin: '0' });
      line.append(baseline); document.body.append(line);
      const rect = line.getBoundingClientRect();
      const canvas = document.createElement('canvas'); const context = canvas.getContext('2d');
      context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
      const metrics = context.measureText(sample);
      const value = { kind: 'typography', id, dpr: devicePixelRatio, viewport: [innerWidth, innerHeight], fontFamily: style.fontFamily, fontSize: style.fontSize, fontWeight: style.fontWeight, lineHeight: style.lineHeight, letterSpacing: style.letterSpacing,
        color: style.color, background: style.backgroundColor, borderColor: style.borderColor, borderRadius: style.borderRadius, boxShadow: style.boxShadow,
        lineBox: { width: round(rect.width), height: round(rect.height), baseline: round(baseline.getBoundingClientRect().top - rect.top) },
        ink: { ascent: metrics.actualBoundingBoxAscent, descent: metrics.actualBoundingBoxDescent, fontAscent: metrics.fontBoundingBoxAscent, fontDescent: metrics.fontBoundingBoxDescent },
        bounds: { x: round(bounds.x), y: round(bounds.y), width: round(bounds.width), height: round(bounds.height) } };
      const serialized = JSON.stringify(value);
      if (seen.get(id) !== serialized) { seen.set(id, serialized); report(value); }
      line.remove();
    }
  }
  let scheduled = false, measuredAt = -Infinity;
  function scheduleTypography() {
    if (scheduled) return; scheduled = true;
    setTimeout(() => requestAnimationFrame(() => requestAnimationFrame(() => { scheduled = false; measuredAt = performance.now(); typography(); })), Math.max(0, 500 - (performance.now() - measuredAt)));
  }
  const watched = '.chat-model-menu,[data-slot="dialog-content"],[data-slot="dialog-overlay"],.mobile-nav-drawer,.mobile-nav-drawer-overlay,.graph-drawer-backdrop,.graph-drawer-close,.context-rail';
  const active = new WeakMap();
  function begin(event) {
    const element = event.target;
    if (!(element instanceof Element) || !element.matches(watched)) return;
    if (active.has(element)) return;
    const id = `web_motion_${++serial}`;
    const started = performance.now(); const frames = [];
    const initial = getComputedStyle(element);
    const data = { kind: 'motion', id, className: element.className, state: element.getAttribute('data-state'), animationName: initial.animationName, animationDuration: initial.animationDuration, animationTimingFunction: initial.animationTimingFunction,
      transitionProperty: initial.transitionProperty, transitionDuration: initial.transitionDuration, transitionTimingFunction: initial.transitionTimingFunction, transformOrigin: initial.transformOrigin, frames };
    active.set(element, data);
    function frame(time) {
      if (active.get(element) !== data) return;
      const style = getComputedStyle(element), bounds = element.getBoundingClientRect();
      frames.push({ t: round(time - started), opacity: Number(style.opacity), transform: style.transform, x: round(bounds.x), y: round(bounds.y), width: round(bounds.width), height: round(bounds.height) });
      if (!element.isConnected || time - started >= 650) { active.delete(element); report(data); return; }
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }
  document.addEventListener('animationstart', begin, true);
  document.addEventListener('transitionrun', begin, true);
  new MutationObserver(records => {
    if (records.some(record => {
      if (record.target instanceof Element && record.target.closest('[data-lg-metrics-probe]')) return false;
      if (record.type === 'attributes') return true;
      return [...record.addedNodes, ...record.removedNodes].some(node => !(node instanceof Element && node.hasAttribute('data-lg-metrics-probe')));
    })) scheduleTypography();
  }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-state', 'class'] });
  document.fonts.ready.then(scheduleTypography);
})();
