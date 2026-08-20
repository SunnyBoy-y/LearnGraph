/**
 * LearnGraph bidirectional subapp client SDK — inline carrier (srcDoc path).
 *
 * Exports the SDK source as a string so the srcDoc subapp preview can inline
 * it. The same SDK is served by the preview gateway as a same-origin static
 * file (`backend/app/preview.py` → `SUBAPP_CLIENT` + `/api/v1/subapps/subapp-client.js`)
 * for the gateway-URL bundle path. **Keep both copies in sync** — the
 * `component.event` / `renderer.unlock` / `renderer.state` / `component.event.ack`
 * protocol is the single source of truth.
 *
 * Protocol:
 *   iframe→parent: { event_type: 'component.event', payload: { type,
 *                    client_event_id, schema_version, occurred_at, ... } }
 *   parent→iframe: renderer.unlock | renderer.state | component.event.ack |
 *                  renderer.media
 */
export const SUBAPP_CLIENT = `// LearnGraph bidirectional subapp client SDK (opaque-origin iframe)
// iframe->parent: {event_type:'component.event', payload:{type, client_event_id, schema_version, occurred_at, ...}}
// parent->iframe: renderer.unlock | renderer.state | component.event.ack | renderer.media
(function () {
  'use strict';
  if (window.__lgSubapp) return;
  var PENDING = {}, SEQ = 0, TOKEN = null, STATE = null, STATE_VERSION = 0;
  var STATE_HANDLERS = [], STATUS_HANDLERS = [];
  var TIMEOUT_MS = 60000;
  function uuid() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }
  function send(payload) {
    try { parent.postMessage(payload, '*'); return true; } catch (e) { return false; }
  }
  function copyInto(dst, src) {
    if (!src || typeof src !== 'object') return dst;
    for (var k in src) {
      if (Object.prototype.hasOwnProperty.call(src, k)) dst[k] = src[k];
    }
    return dst;
  }
  function emit(type, data) {
    var payload = copyInto({}, data);
    var clientEventId = uuid();
    payload.type = type;
    payload.client_event_id = clientEventId;
    payload.schema_version = 1;
    payload.occurred_at = new Date().toISOString();
    var id = ++SEQ;
    var p = new Promise(function (resolve, reject) {
      PENDING[id] = { clientEventId: clientEventId, resolve: resolve, reject: reject };
      setTimeout(function () {
        if (PENDING[id]) { delete PENDING[id]; reject(new Error('learngraph subapp event timeout')); }
      }, TIMEOUT_MS);
    });
    send({ event_type: 'component.event', payload: payload });
    return p;
  }
  function notifyStatus(status, detail) {
    for (var i = 0; i < STATUS_HANDLERS.length; i++) {
      try { STATUS_HANDLERS[i](status, detail || null); } catch (e) {}
    }
  }
  function onMessage(event) {
    var data = event.data;
    if (!data || typeof data !== 'object') return;
    var et = data.event_type;
    if (et === 'renderer.unlock') {
      var up = data.payload || {};
      TOKEN = typeof up.token === 'string' ? up.token : null;
      notifyStatus('ready');
      return;
    }
    if (et === 'renderer.state') {
      var sp = data.payload || {};
      if (typeof sp.version === 'number' && sp.version >= STATE_VERSION) {
        STATE_VERSION = sp.version;
        STATE = sp.state || {};
        for (var i = 0; i < STATE_HANDLERS.length; i++) {
          try { STATE_HANDLERS[i](STATE, STATE_VERSION); } catch (e) {}
        }
      }
      return;
    }
    if (et === 'component.event.ack') {
      var ap = data.payload || {};
      var cid = ap.client_event_id;
      for (var key in PENDING) {
        var entry = PENDING[key];
        if (entry && entry.clientEventId === cid) {
          delete PENDING[key];
          var err = new Error(ap.error_code || 'event rejected');
          err.status = ap.status;
          err.error_code = ap.error_code;
          if (ap.status === 'persisted') { entry.resolve(ap); } else { entry.reject(err); }
          notifyStatus(ap.status === 'persisted' ? 'persisted' : 'rejected', ap);
        }
      }
      return;
    }
  }
  window.addEventListener('message', onMessage);
  window.__lgSubapp = {
    emit: emit,
    track: emit,
    submit: emit,
    onState: function (handler) {
      STATE_HANDLERS.push(handler);
      if (STATE) { try { handler(STATE, STATE_VERSION); } catch (e) {} }
    },
    onStatus: function (handler) { STATUS_HANDLERS.push(handler); },
    ready: function () { return TOKEN !== null; },
    requestAnalysis: function (purpose) {
      return emit('analysis.requested', { purpose: typeof purpose === 'string' ? purpose : '' });
    }
  };
})();
`

/** Build an inline <script> element tag carrying the bidirectional subapp client SDK. */
export function subappClientInlineTag(): string {
  return `<script>${SUBAPP_CLIENT}</script>`;
}
