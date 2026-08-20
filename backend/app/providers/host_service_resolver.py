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

* ``host_access_mode == "direct"`` (trusted desktop direct) -> loopback URLs
  are rewritten to ``http://host.docker.internal:<same-port>``, preserving
  path/query. Docker Desktop forwards that alias to the real machine's
  loopback; no service registry, token or audit (single-user trusted mode).
* ``host_access_mode == "off"`` -> URL unchanged.
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
from pathlib import Path
from urllib.parse import urlunsplit, urlsplit

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})

PERSONAL_DESKTOP_PROFILE = "personal_desktop"

# Docker Desktop host gateway alias: inside a container it reaches the real
# machine's loopback (Windows/macOS Desktop). Compose additionally wires
# ``extra_hosts: host.docker.internal:host-gateway`` for native Linux.
HOST_DOCKER_HOST = "host.docker.internal"

# Header the backend sends to the Host Service Bridge for authentication.
# Kept separate from Authorization so provider/MCP target credentials
# (e.g. Ollama's conventional "Bearer ollama") never collide with it.
HOST_BRIDGE_TOKEN_HEADER = "X-LearnGraph-Host-Bridge-Token"

_PATH_SAFE_RE = re.compile(r"[^a-z0-9._-]")


def sanitize_service_id(value: str) -> str:
    """Make an arbitrary identifier path-safe for bridge registry ids.

    Host Service Bridge registry ids must be URL-path-safe slugs
    (``validate_host_service`` rejects slashes, whitespace and dot segments).
    MCP ``server_key`` values are human-supplied, so sanitize before embedding
    them in a bridge URL.
    """
    return _PATH_SAFE_RE.sub("-", (value or "").strip().casefold()) or "unknown"


def read_bridge_token(token_file: str | Path | None) -> str | None:
    """Read the bridge bearer token from a mounted secret file (cached-free).

    Missing, unreadable or empty files yield ``None`` so callers can simply
    skip the token header and let the bridge deny the call (fail closed) —
    the frontend surfaces that state with an actionable hint.
    """
    if not token_file:
        return None
    try:
        raw = Path(token_file).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return raw or None


def is_loopback_url(url: str) -> bool:
    """True when the URL host is a literal loopback host (case-insensitive)."""
    try:
        parsed = urlsplit((url or "").strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    return host in LOOPBACK_HOSTS


def rewrite_loopback_to_host_docker(base_url: str) -> str:
    """Trusted-desktop direct: rewrite a loopback URL to the Docker gateway.

    ``http://127.0.0.1:PORT/path`` -> ``http://host.docker.internal:PORT/path``
    (port, path and query preserved; literal loopback hosts only). Remote
    endpoints are never touched. On native Linux the ``host-gateway`` alias
    only reaches the Docker bridge, so host services must bind an interface
    reachable from it — see doc/host-service-bridge.md.
    """
    if not is_loopback_url(base_url):
        return base_url
    parsed = urlsplit((base_url or "").strip())
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return base_url
    netloc = f"{HOST_DOCKER_HOST}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


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
    host_access_mode: str | None = None,
) -> str:
    """Rewrite a loopback provider URL per the host-access strategy.

    ``host_access_mode`` is the *effective* strategy (``"direct"`` |
    ``"bridge"`` | ``"off"``, see ``Settings.effective_host_access_mode``);
    ``None`` keeps the legacy bridge-only behavior for older callers. See
    module docstring for the exact rules. The return value is always a
    usable URL; callers normalize it afterwards (e.g.
    ``normalize_ollama_api_base_url``).
    """
    if host_access_mode == "direct":
        return rewrite_loopback_to_host_docker(base_url)
    if host_access_mode == "off":
        return base_url
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
    host_access_mode: str | None = None,
) -> str:
    """Rewrite any loopback URL (MCP endpoints, local APIs) per host-access strategy.

    Same rules as :func:`resolve_host_service_url` but with an explicit
    registry service id, used by non-provider callers such as the MCP adapter
    wiring (``app.services.mcp_skills``).
    """
    if host_access_mode == "direct":
        return rewrite_loopback_to_host_docker(base_url)
    if host_access_mode == "off":
        return base_url
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
