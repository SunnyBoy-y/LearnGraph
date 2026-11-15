// Desktop-agent-ui clip-audit function, preserved for repeatable notification QA.
export default (opts = {}) => {
  const tol = opts.tol ?? 1;
  const scope = opts.root ? document.querySelector(opts.root) : document.body;
  if (!scope) return { ok: false, error: 'root 未找到: ' + opts.root };
  const px = (v) => parseFloat(v) || 0;
  const name = (el) => {
    if (!el || el === document.documentElement) return 'html';
    let s = el.tagName.toLowerCase();
    if (el.id) return s + '#' + el.id;
    const c = [...el.classList].slice(0, 3).join('.');
    return c ? s + '.' + c : s;
  };
  const path = (el) => {
    const out = [];
    for (let n = el; n && n !== document.body && out.length < 4; n = n.parentElement) out.unshift(name(n));
    return out.join(' > ');
  };
  const split = (v) => {
    const parts = []; let depth = 0, cur = '';
    for (const ch of v) {
      if (ch === '(') depth++;
      if (ch === ')') depth--;
      if (ch === ',' && !depth) { parts.push(cur); cur = ''; } else cur += ch;
    }
    if (cur.trim()) parts.push(cur);
    return parts;
  };
  const lens = (s) => (s.replace(/rgba?\([^)]*\)|hsla?\([^)]*\)|#[0-9a-f]+|[a-z-]+\([^)]*\)/gi, ' ')
    .match(/-?\d*\.?\d+(px)?/g) || []).map(px);
  const transparent = (s) => /rgba?\([^)]*,\s*0\s*\)|transparent/i.test(s);
  const boxExt = (v) => {
    const e = { t: 0, r: 0, b: 0, l: 0 };
    if (!v || v === 'none') return null;
    for (const sh of split(v)) {
      if (/inset/.test(sh) || transparent(sh)) continue;
      const [ox = 0, oy = 0, blur = 0, spread = 0] = lens(sh);
      const g = blur + spread;
      e.l = Math.max(e.l, g - ox); e.r = Math.max(e.r, g + ox);
      e.t = Math.max(e.t, g - oy); e.b = Math.max(e.b, g + oy);
    }
    return e.t + e.r + e.b + e.l > 0 ? e : null;
  };
  const dropExt = (v) => {
    if (!v || !/drop-shadow/.test(v)) return null;
    const e = { t: 0, r: 0, b: 0, l: 0 };
    for (const m of v.matchAll(/drop-shadow\(((?:[^()]|\([^)]*\))*)\)/g)) {
      if (transparent(m[1])) continue;
      const [ox = 0, oy = 0, blur = 0] = lens(m[1]);
      e.l = Math.max(e.l, blur - ox); e.r = Math.max(e.r, blur + ox);
      e.t = Math.max(e.t, blur - oy); e.b = Math.max(e.b, blur + oy);
    }
    return e.t + e.r + e.b + e.l > 0 ? e : null;
  };
  const paintRect = (el, cs) => {
    const r = el.getBoundingClientRect();
    const isMedia = el.tagName === 'IMG' || el.tagName === 'VIDEO';
    if (!isMedia) return r;
    const pl = px(cs.paddingLeft) + px(cs.borderLeftWidth), pr = px(cs.paddingRight) + px(cs.borderRightWidth);
    const pt = px(cs.paddingTop) + px(cs.borderTopWidth), pb = px(cs.paddingBottom) + px(cs.borderBottomWidth);
    let L = r.left + pl, T = r.top + pt, W = r.width - pl - pr, H = r.height - pt - pb;
    const nw = el.naturalWidth || el.videoWidth, nh = el.naturalHeight || el.videoHeight;
    if (nw && nh && (cs.objectFit === 'contain' || cs.objectFit === 'scale-down')) {
      const k = Math.min(W / nw, H / nh, cs.objectFit === 'scale-down' ? 1 : Infinity);
      const w = nw * k, h = nh * k;
      L += (W - w) / 2; T += (H - h) / 2; W = w; H = h;
    }
    const a = el.tagName === 'IMG' && alphaBox(el);
    if (a) { L += a.l * W; T += a.t * H; W *= a.r - a.l; H *= a.b - a.t; }
    return { left: L, top: T, right: L + W, bottom: T + H };
  };
  const alphaCache = new Map();
  const alphaBox = (img) => {
    if (!img.complete || !img.naturalWidth) return null;
    if (alphaCache.has(img.currentSrc)) return alphaCache.get(img.currentSrc);
    let box = null;
    try {
      const n = 200, k = n / Math.max(img.naturalWidth, img.naturalHeight);
      const w = Math.max(1, Math.round(img.naturalWidth * k)), h = Math.max(1, Math.round(img.naturalHeight * k));
      const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
      const cx = cv.getContext('2d'); cx.drawImage(img, 0, 0, w, h);
      const d = cx.getImageData(0, 0, w, h).data;
      let l = w, t = h, r = -1, b = -1;
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        if (d[(y * w + x) * 4 + 3] > 8) { if (x < l) l = x; if (x > r) r = x; if (y < t) t = y; if (y > b) b = y; }
      }
      if (r >= 0) box = { l: l / w, t: t / h, r: (r + 1) / w, b: (b + 1) / h };
    } catch { /* Cross-origin pixels are conservatively measured as a full box. */ }
    alphaCache.set(img.currentSrc, box);
    return box;
  };
  const clipOf = (a, cs) => {
    const ox = cs.overflowX, oy = cs.overflowY;
    const cx = ox !== 'visible', cy = oy !== 'visible';
    const cp = cs.clipPath && cs.clipPath !== 'none';
    const paint = /paint|strict|content/.test(cs.contain);
    if (!cx && !cy && !cp && !paint) return null;
    const r = a.getBoundingClientRect();
    const scrollX = /auto|scroll/.test(ox) && a.scrollWidth > a.clientWidth + 1;
    const scrollY = /auto|scroll/.test(oy) && a.scrollHeight > a.clientHeight + 1;
    return {
      left: r.left + px(cs.borderLeftWidth), right: r.right - px(cs.borderRightWidth),
      top: r.top + px(cs.borderTopWidth), bottom: r.bottom - px(cs.borderBottomWidth),
      x: (cx || cp || paint) && !scrollX, y: (cy || cp || paint) && !scrollY,
      scrollX, scrollY,
      why: cp ? 'clip-path' : paint ? 'contain:' + cs.contain : 'overflow:' + (ox === oy ? ox : ox + '/' + oy),
    };
  };
  const issues = [];
  const check = (el, ext, kind) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    const r = paintRect(el, cs);
    if (r.right - r.left < 1 || r.bottom - r.top < 1) return;
    const sh = { left: r.left - ext.l, right: r.right + ext.r, top: r.top - ext.t, bottom: r.bottom + ext.b };
    let doneX = false, doneY = false;
    const bounds = [];
    for (let a = el.parentElement; a && a !== document.documentElement; a = a.parentElement) {
      const c = clipOf(a, getComputedStyle(a));
      if (c) bounds.push([a, c]);
    }
    if (opts.viewport) bounds.push([document.documentElement, { left: 0, top: 0, right: innerWidth, bottom: innerHeight, x: true, y: true, why: 'window' }]);
    const add = (side, gap, cut, a, c) => issues.push({
      el: path(el), kind, side, level: gap <= 1 ? 'hidden' : 'cut',
      cut: +cut.toFixed(1), gap: +gap.toFixed(1), by: path(a), why: c.why,
    });
    for (const [a, c] of bounds) {
      if (!doneX && c.x) {
        if (r.left >= c.left - 0.5 && sh.left < c.left - tol) add('left', r.left - c.left, c.left - sh.left, a, c);
        if (r.right <= c.right + 0.5 && sh.right > c.right + tol) add('right', c.right - r.right, sh.right - c.right, a, c);
      }
      if (!doneY && c.y) {
        if (r.top >= c.top - 0.5 && sh.top < c.top - tol) add('top', r.top - c.top, c.top - sh.top, a, c);
        if (r.bottom <= c.bottom + 0.5 && sh.bottom > c.bottom + tol) add('bottom', c.bottom - r.bottom, sh.bottom - c.bottom, a, c);
      }
      if (c.scrollX) doneX = true;
      if (c.scrollY) doneY = true;
    }
  };
  for (const el of scope.querySelectorAll('*')) {
    const cs = getComputedStyle(el);
    const b = boxExt(cs.boxShadow); if (b) check(el, b, 'box-shadow');
    const d = dropExt(cs.filter); if (d) check(el, d, 'drop-shadow');
  }
  if (opts.hover !== false) {
    const seen = new Set();
    for (const sheet of document.styleSheets) {
      let rules; try { rules = sheet.cssRules; } catch { continue; }
      const walk = (list) => {
        for (const rule of list) {
          if (rule.cssRules && !rule.selectorText) { walk(rule.cssRules); continue; }
          if (!rule.selectorText || !/:hover/.test(rule.selectorText)) continue;
          const b = boxExt(rule.style.boxShadow), d = dropExt(rule.style.filter);
          if (!b && !d) continue;
          for (const sel of rule.selectorText.split(',')) {
            if (!/:hover/.test(sel)) continue;
            let els; try { els = scope.querySelectorAll(sel.replace(/:hover/g, '')); } catch { continue; }
            for (const el of els) {
              const k = sel + '|' + path(el);
              if (seen.has(k)) continue; seen.add(k);
              if (b) check(el, b, 'hover box-shadow');
              if (d) check(el, d, 'hover drop-shadow');
            }
          }
        }
      };
      walk(rules);
    }
  }
  const uniq = [], key = new Set();
  for (const i of issues) { const k = i.el + i.kind + i.side; if (!key.has(k)) { key.add(k); uniq.push(i); } }
  uniq.sort((x, y) => (x.level === 'cut' ? 0 : 1) - (y.level === 'cut' ? 0 : 1) || y.cut - x.cut);
  const cut = uniq.filter((i) => i.level === 'cut').length;
  return { ok: cut === 0, cut, hidden: uniq.length - cut, issues: uniq.slice(0, opts.max ?? 40) };
}
