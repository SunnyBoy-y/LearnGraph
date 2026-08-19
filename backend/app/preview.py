"""LearnGraph independent subapp preview origin.

Serves immutable multi-file teaching bundles through a capability-gated
opaque-origin iframe on its own port, so the preview origin is distinct from
the main API origin. This satisfies the ``script-src 'self'`` invariant in
``doc/LearnGraph_交互式子应用_双向状态通道设计_v1.0.md``: CSP ``'self'`` here
can only ever load resources on this preview origin, never main-API scripts.

Run it as a separate process (``scripts/dev.mjs`` starts it automatically):

    uv run python -m uvicorn app.preview:preview_app --host 127.0.0.1 --port 8001

The preview process reuses the shared database and object storage. It does not
carry user sessions: each resource is authorized by its short-lived capability
token (``capability 即授权``), and responses carry a host-owned CSP that keeps
the previewed application fully offline and unable to reach LearnGraph APIs.

Browser-sandbox runtime: every ``text/html`` bundle response is injected with
one same-origin ``<script src="/api/v1/subapps/runtime-shim.js">`` tag. The
shim (see ``RUNTIME_SHIM`` below) installs ``window.__lg`` and wraps
``window.fetch`` so sandbox code can reach multi-file VFS reads and the
approval-free network relay through the host bridge — the CSP stays
``script-src 'self'`` and ``connect-src 'none'``, so no network request leaves
the sandbox except through the relay protocol. Keep ``RUNTIME_SHIM`` in sync
with ``frontend/src/lib/sandbox-runtime-shim.ts`` (same protocol, different
injection carrier: static file here, inline there).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, FastAPI, Path
from fastapi.responses import Response, StreamingResponse

from app.api.deps import AppSettings, DB
from app.core.database import init_database
from app.core.errors import install_error_handlers
from app.providers.storage_factory import object_storage_provider
from app.services.subapp_bundles import SubAppBundleService

router = APIRouter(prefix="/api/v1/subapps", tags=["subapp-preview"])

health_router = APIRouter(prefix="/api/v1", tags=["subapp-preview"])

# --------------------------------------------------------------------------- //
# Browser-sandbox runtime shim (opaque-origin iframe)
# --------------------------------------------------------------------------- //
# Injected as a same-origin script into every text/html bundle response. Keep
# in sync with frontend/src/lib/sandbox-runtime-shim.ts — the protocol is the
# single source of truth (lg:1 postMessage relay: vfs.read / net.fetch).
RUNTIME_SHIM = r"""// LearnGraph sandbox runtime shim (opaque-origin iframe)
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
    return typeof url === 'string' && !/^(https?:)?\/\//i.test(url) && url.indexOf('://') === -1 && url.indexOf('data:') !== 0 && url.indexOf('blob:') !== 0;
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
})();
"""

RUNTIME_SHIM_SCRIPT_TAG = '<script src="/api/v1/subapps/runtime-shim.js"></script>'


@router.get("/runtime-shim.js", include_in_schema=False)
def serve_runtime_shim() -> Response:
    """Same-origin runtime shim for browser sandboxes served from the preview origin."""
    return Response(
        content=RUNTIME_SHIM,
        media_type="text/javascript",
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _inject_runtime_shim(html: bytes) -> bytes:
    """Insert the runtime-shim script tag into a bundle HTML response.

    Keeps ``script-src 'self'`` intact (the shim is same-origin), so the
    injected script never broadens the bundle's own CSP.
    """
    try:
        text = html.decode("utf-8")
    except UnicodeDecodeError:
        text = html.decode("utf-8", errors="replace")
    marker = "</head>"
    if marker in text:
        text = text.replace(marker, RUNTIME_SHIM_SCRIPT_TAG + marker, 1)
    else:
        text += RUNTIME_SHIM_SCRIPT_TAG
    return text.encode("utf-8")


@health_router.get("/livez")
async def livez() -> dict[str, str]:
    """Process/event-loop liveness probe used by the development supervisor."""
    return {"status": "ok"}


@health_router.get("/health")
def health() -> dict[str, str]:
    """Readiness endpoint retained for diagnostics and compatibility."""
    return {"status": "ok"}


@router.get("/preview/{raw_token}/{bundle_id}/{path:path}")
def serve_bundle_preview(
    raw_token: str,
    bundle_id: str,
    path: str,
    db: DB,
    settings: AppSettings,
):
    """Serve one immutable bundle resource to a capability-authorized viewer.

    The capability token is the complete authorization; the request is validated
    against the immutable manifest and no host/blob/filesystem path is exposed.
    """
    service = SubAppBundleService(db, "", "", settings)
    bundle, item, blob = service.resolve_preview(raw_token, bundle_id, path)
    storage = object_storage_provider(db, bundle.workspace_id, settings)
    headers = {
        "Cache-Control": "private, no-store",
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Content-Security-Policy": (
            "default-src 'none'; base-uri 'none'; connect-src 'none'; form-action 'none'; "
            "frame-src 'none'; object-src 'none'; manifest-src 'none'; script-src 'self' https: http:; "
            "style-src 'self' 'unsafe-inline' https: http:; img-src 'self' data: blob: https: http:; "
            "font-src 'self' data: https: http:; media-src 'self' data: blob: https: http:; "
            "worker-src blob:"
        ),
    }
    if item.mime_type == "text/html":
        # Inject the runtime shim so sandbox code can use window.__lg / fetch
        # relay (multi-file VFS + approval-free networking via the host bridge).
        payload = _inject_runtime_shim(b"".join(storage.iter_bytes(blob.object_key, offset=0, length=item.size_bytes)))
        return Response(content=payload, media_type="text/html", headers=headers)
    return StreamingResponse(
        storage.iter_bytes(blob.object_key, offset=0, length=item.size_bytes),
        media_type=item.mime_type,
        headers=headers,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    # The preview process owns its own database initialization (idempotent
    # create_all), so it can be started independently of the main API.
    init_database()
    yield


preview_app = FastAPI(title="LearnGraph subapp preview", lifespan=lifespan)
install_error_handlers(preview_app)
preview_app.include_router(router)
preview_app.include_router(health_router)
