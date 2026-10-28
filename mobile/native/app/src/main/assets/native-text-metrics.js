/* Debug-only read-only WebView evaluateJavascript probe. No DOM or application state changes. */
(function () {
  const selectors = [
    '.chat-empty-state__eyebrow', '.chat-empty-state h2', '.chat-empty-state__prompt',
    '.chat-composer__textarea', '.chat-workbench-toolbar button', '.topbar-model-trigger',
    '.sidebar-brand .sidebar-text', '.sidebar-project__header', '.sidebar-sessions__heading p',
    '.sidebar-nav__primary .sidebar-text', '.sidebar-nav__scroll .sidebar-text',
    '.sidebar-user-menu [data-slot="avatar"]', '.sidebar-user-avatar',
  ];
  const context = document.createElement('canvas').getContext('2d');
  const rows = [];
  for (const selector of selectors) {
    for (const element of document.querySelectorAll(selector)) {
      const rect = element.getBoundingClientRect();
      if (!rect.width || !rect.height || rect.bottom <= 0 || rect.top >= innerHeight || rect.right <= 0 || rect.left >= innerWidth) continue;
      const style = getComputedStyle(element);
      const text = 'value' in element ? element.value || element.getAttribute('placeholder') || '' : element.textContent || '';
      const size = parseFloat(style.fontSize);
      const lineHeight = parseFloat(style.lineHeight);
      let primary = null, ink = null;
      if (context) {
        context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        const first = context.measureText('Hgj');
        const glyphs = context.measureText(text.slice(0, 256));
        if (Number.isFinite(first.fontBoundingBoxAscent) && Number.isFinite(first.fontBoundingBoxDescent)) {
          primary = { ascent: first.fontBoundingBoxAscent, descent: first.fontBoundingBoxDescent,
            cssBaseline: Number.isFinite(lineHeight) ? first.fontBoundingBoxAscent + (lineHeight - first.fontBoundingBoxAscent - first.fontBoundingBoxDescent) / 2 : null };
        }
        ink = { ascent: glyphs.actualBoundingBoxAscent, descent: glyphs.actualBoundingBoxDescent, width: glyphs.width };
      }
      rows.push({ selector, characters: text.length, rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
        fontFamily: style.fontFamily, fontSize: size, fontWeight: style.fontWeight, lineHeight,
        letterSpacing: style.letterSpacing, fontSynthesis: style.fontSynthesis, fontOpticalSizing: style.fontOpticalSizing,
        fontFeatureSettings: style.fontFeatureSettings, fontVariationSettings: style.fontVariationSettings,
        color: style.color, background: style.backgroundColor, border: style.borderColor,
        borderRadius: style.borderRadius, boxShadow: style.boxShadow,
        padding: [style.paddingTop, style.paddingRight, style.paddingBottom, style.paddingLeft], primary, ink });
    }
  }
  const root = getComputedStyle(document.documentElement);
  const tokens = Object.fromEntries(['--foreground', '--background', '--primary', '--primary-foreground',
    '--muted', '--muted-foreground', '--border', '--secondary', '--accent', '--sidebar', '--sidebar-accent',
    '--reading-text'].map(key => [key, root.getPropertyValue(key).trim()]));
  return JSON.stringify({ schema: 1, devicePixelRatio, viewport: [innerWidth, innerHeight],
    language: navigator.language, fontsStatus: document.fonts?.status,
    dark: document.documentElement.classList.contains('dark'), colorMode: document.documentElement.dataset.colorMode, tokens, rows });
})();
