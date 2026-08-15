from __future__ import annotations

"""Host Service Resolver: keep provider URLs logical, resolve per deployment.

Background: in the whole-app Docker deployment the ``app`` container's
``127.0.0.1`` is the container itself, so a persisted provider base URL such
as ``http://127.0.0.1:11434`` (Ollama) cannot reach the physical host. Instead
of asking users to hand-edit their provider config per deployment shape (the
``if docker: host.docker.internal`` patch that becomes tech debt), LearnGraph
keeps the *logical* loopback URL in the provider row and rewrites it at the
single Model Gateway assembly point (``app.providers.factory``) and at probe
time (``ProviderService._discover``).

Rewrite rules (fail-open by design — a missing bridge simply leaves the URL
unchanged so source-mode installs are never affected):

* No ``host_bridge_url`` configured  -> URL unchanged (source mode, direct).
* ``deployment_profile == personal_desktop`` -> URL unchanged (single-user
  source install; localhost is the real machine).
* URL host is not loopback (``127.0.0.1``/``localhost``/``::1``) -> unchanged
  (remote endpoints like ``https://ollama.com`` are never rewritten).
* Otherwise -> ``{host_bridge_url}/services/{service_id}/{path}``.

The bridge (``app.services.host_service_bridge``) is the host-side daemon that
serves ``/services/<id>/...``; it default-denies unknown services and forwards
only registered loopback targets. ``service_id`` maps provider families to the
registry id the operator reviews on the host (e.g. ``ollama``).
"""

import re
from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})

PERSONAL_DESKTOP_PROFILE = "personal_desktop"

_PATH_SAFE_RE = re.compile(r"[^a-z0-9._-]")


def sanitize_service_id(value: str) -> str:
    """Make an arbitrary identifier path-safe for bridge registry ids.

    Host Service Bridge registry ids must be URL-path-safe slugs
    (``validate_host_service`` rejects slashes, whitespace and dot segments).
    MCP ``server_key`` values are human-supplied, so sanitize before embedding
    them in a bridge URL.
    """
    return _PATH_SAFE_RE.sub("-", (value or "").strip().casefold()) or "unknown"


def is_loopback_url(url: str) -> bool:
    """True when the URL host is a literal loopback host (case-insensitive)."""
    try:
        parsed = urlsplit((url or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    return host in LOOPBACK_HOSTS


def service_id_for_provider(provider_type: str) -> str:
    """Map a provider type to its host-service registry id.

    ``ollama`` and ``ollama_embedding`` share one real-machine service
    (``ollama``); anything else keeps its provider type as the id so operators
    can register it explicitly.
    """
    normalized = (provider_type or "").strip().casefold()
    if normalized in {"ollama", "ollama_embedding"}:
        return "ollama"
    return normalized or "unknown"


def resolve_host_service_url(
    *,
    provider_type: str,
    base_url: str,
    host_bridge_url: str | None,
    deployment_profile: str,
) -> str:
    """Rewrite a loopback provider URL through the Host Service Bridge.

    See module docstring for the exact rules. The return value is always a
    usable URL; callers normalize it afterwards (e.g.
    ``normalize_ollama_api_base_url``).
    """
    if not host_bridge_url:
        return base_url
    if deployment_profile == PERSONAL_DESKTOP_PROFILE:
        return base_url
    if not is_loopback_url(base_url):
        return base_url
    bridge = host_bridge_url.rstrip("/")
    service_id = service_id_for_provider(provider_type)
    parsed = urlsplit(base_url)
    path = parsed.path.lstrip("/")
    rewritten = f"{bridge}/services/{service_id}/{path}"
    if parsed.query:
        rewritten = f"{rewritten}?{parsed.query}"
    return rewritten


def resolve_loopback_url(
    *,
    service_id: str,
    base_url: str,
    host_bridge_url: str | None,
    deployment_profile: str,
) -> str:
    """Rewrite any loopback URL (MCP endpoints, local APIs) through the bridge.

    Same rules as :func:`resolve_host_service_url` but with an explicit
    registry service id, used by non-provider callers such as the MCP adapter
    wiring (``app.services.mcp_skills``).
    """
    if not host_bridge_url:
        return base_url
    if deployment_profile == PERSONAL_DESKTOP_PROFILE:
        return base_url
    if not is_loopback_url(base_url):
        return base_url
    bridge = host_bridge_url.rstrip("/")
    parsed = urlsplit(base_url)
    path = parsed.path.lstrip("/")
    rewritten = f"{bridge}/services/{service_id}/{path}"
    if parsed.query:
        rewritten = f"{rewritten}?{parsed.query}"
    return rewritten
