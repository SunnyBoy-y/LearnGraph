/**
 * LearnGraph browser-sandbox runtime shim — inline carrier.
 *
 * This module exports the shim source as a string so the srcDoc preview path
 * can inline it (its CSP allows `script-src 'unsafe-inline'`). The same
 * protocol is served by the preview gateway as a same-origin static file
 * (`backend/app/preview.py` → `RUNTIME_SHIM` + `/api/v1/subapps/runtime-shim.js`)
 * for the gateway-URL bundle path. **Keep both copies in sync** — the
 * `lg:1` postMessage relay protocol is the single source of truth.
 *
 * Protocol:
 *   iframe→parent: { lg: 1, kind: 'vfs.read' | 'net.fetch', id, ... }
 *   parent→iframe: { lg: 1, kind: 'lg.result', id, ok, status, statusText,
 *                    headers?, bodyBase64?, bodyText?, error? }
 */
export const SANDBOX_RUNTIME_SHIM = `// LearnGraph sandbox runtime shim (opaque-origin iframe)
// Relay protocol: iframe->parent {lg:1,kind,id,...} ; parent->iframe {lg:1,kind:'lg.result',id,ok,...}
(function () {
  'use strict';
  if (window.__lg) return;
  var PENDING = new Map(), SEQ = 0, TIMEOUT_MS = 30000;
  function call(kind, payload) {
    return new Promise(function (resolve, reject) {
      var id = ++SEQ;
      PENDING.set(id, { resolve: resolve, reject: reject });
      try { parent.postMessage(Object.assign({ lg: 1, kind: kind, id: id }, payload), '*'); }
      catch (e) { PENDING.delete(id); reject(e); return; }
      setTimeout(function () {
        if (PENDING.delete(id)) reject(new Error('learngraph sandbox relay timeout'));
      }, TIMEOUT_MS);
    });
  }
  window.addEventListener('message', function (event) {
    var data = event.data;
    if (!data || data.lg !== 1 || data.kind !== 'lg.result') return;
    var entry = PENDING.get(data.id);
    if (!entry) return;
    PENDING.delete(data.id);
    if (data.ok) entry.resolve(data); else entry.reject(new Error(data.error || 'relay failed'));
  });
  function decodeBody(result) {
    if (result.bodyBase64) {
      var bin = atob(result.bodyBase64);
      var bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return new Response(bytes.buffer, { status: result.status, statusText: result.statusText, headers: result.headers || {} });
    }
    return new Response(result.bodyText != null ? result.bodyText : null, { status: result.status, statusText: result.statusText, headers: result.headers || {} });
  }
  function isRelative(url) {
    return typeof url === 'string' && !/^(https?:)?\\/\\//i.test(url) && url.indexOf('://') === -1 && url.indexOf('data:') !== 0 && url.indexOf('blob:') !== 0;
  }
  function headerRecord(headers) {
    var out = {};
    if (headers instanceof Headers) headers.forEach(function (v, k) { out[k] = v; });
    else if (Array.isArray(headers)) headers.forEach(function (p) { out[p[0]] = p[1]; });
    else if (headers && typeof headers === 'object') Object.assign(out, headers);
    return out;
  }
  function lgFetch(input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    var options = init || {};
    var method = String(options.method || 'GET').toUpperCase();
    var body = options.body;
    var promise;
    if (isRelative(url)) {
      promise = call('vfs.read', { path: url, method: method });
      return promise.then(function (result) {
        if (!result.ok) throw new Error(result.error || 'file not found');
        return decodeBody(result);
      });
    }
    if (typeof body === 'string') {
      // string bodies only in v1 (JSON/text); FormData/Blob not relayed
    } else if (body != null && !(body instanceof URLSearchParams)) {
      body = null;
    }
    promise = call('net.fetch', {
      url: url,
      method: method,
      headers: headerRecord(options.headers),
      body: typeof body === 'string' ? body : (body instanceof URLSearchParams ? body.toString() : null)
    });
    return promise.then(function (result) {
      if (!result.ok) throw new Error(result.error || 'network request failed');
      return decodeBody(result);
    });
  }
  window.__lg = {
    fetch: lgFetch,
    vfs: function (path) { return lgFetch(path); }
  };
  if (window.fetch) window.fetch = lgFetch;
})();`;

/** Build an inline <script> element tag carrying the runtime shim. */
export function sandboxRuntimeShimInlineTag(): string {
  return `<script>${SANDBOX_RUNTIME_SHIM}</script>`;
}
