from __future__ import annotations

"""Reviewed, executable outbound-network policy for sandboxes.

The default sandbox posture is fully offline (``network_mode="none"``). This
module is the policy *layer* that a deployment may enable only with an explicit,
reviewed allow-list. Every decision here fails closed: an unknown host, an
ambiguous DNS answer, a private/loopback/link-local/metadata address, an expired
policy, or a missing approval record is denied.

The enforcement boundary is ``SandboxEgressProxy`` in
``app.services.sandbox_egress_proxy``: the sandbox never talks to the internet
directly, it may only reach the proxy, and the proxy authorizes each CONNECT
against a validated ``EgressPolicy`` while re-classifying the *resolved* address
at connection time (DNS-rebinding protection).
"""

import hashlib
import ipaddress
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

from app.core.config import get_settings

logger = logging.getLogger(__name__)

HOSTNAME_MAX_LENGTH = 253
LABEL_MAX_LENGTH = 63
PROTOCOL_HTTPS = "https"
DEFAULT_PORT = 443
PROTOCOL_HTTP = "http"
DEFAULT_HTTP_PORT = 80
ALLOWED_PROTOCOL_PORTS = {
    PROTOCOL_HTTP: DEFAULT_HTTP_PORT,
    PROTOCOL_HTTPS: DEFAULT_PORT,
}
ALLOWED_PORT_PROTOCOLS = {port: protocol for protocol, port in ALLOWED_PROTOCOL_PORTS.items()}


class NetworkCapability(str, Enum):
    """Task-scoped network capability granted to one sandbox execution.

    ``OFFLINE`` is the default and has no policy file. The other values are
    enforced by the host-side broker/proxy, never by prompt instructions.
    """

    OFFLINE = "OFFLINE"
    FETCH = "FETCH"
    BROWSER = "BROWSER"
    RESTRICTED_EGRESS = "RESTRICTED_EGRESS"


def normalize_network_capability(value: Any) -> NetworkCapability:
    if isinstance(value, NetworkCapability):
        return value
    if not isinstance(value, str):
        raise EgressPolicyInvalid("policy_capability_invalid")
    candidate = value.strip().upper()
    try:
        return NetworkCapability(candidate)
    except ValueError as exc:
        raise EgressPolicyInvalid("policy_capability_invalid") from exc

# Derived fetch egress: the unified ``web_fetch.policy`` allowlist is the single
# source of truth, and fetch egress is derived from it into a *separate* policy
# file with its own provenance. This keeps the generic per-workspace reviewed
# policy (and therefore generic Agent egress) untouched by fetch approvals.
# The default TTL is the maximum (1 day) on purpose: the policy digest is the
# SHA-256 of the document, so rewriting the file on expiry rotates the digest
# and every warm pooled runner container (which carries the digest it was
# created with) would fail CONNECT until it is evicted and recreated. A long
# TTL keeps the digest stable across the warm-pool lifetime, and
# ``refresh_workspace_fetch_policy_file`` still re-derives on every fetch so a
# shorter effective rotation only happens when the allowlist itself changes
# (which also evicts/rebuilds pool entries) or after a full day of inactivity.
WEB_FETCH_POLICY_ISSUER = "web_fetch_policy"
WEB_FETCH_POLICY_APPROVAL_ID = "web_fetch_policy"
WEB_FETCH_POLICY_DEFAULT_TTL_SECONDS = 86400
WEB_FETCH_POLICY_MAX_TTL_SECONDS = 86400
WEB_FETCH_POLICY_FILE_SUFFIX = ".web_fetch.json"

# Derived generic Agent egress (D2.1 T4.1): the workspace ``agent_egress``
# allowlist plus active ``allow_once`` leases is the source of truth. It is
# written to the same generic ``{workspace_id}.json`` slot the sandbox envelope
# and proxy read, but carries its own issuer so audits can distinguish an
# approval-derived policy from a deployment-reviewed baseline.
AGENT_EGRESS_POLICY_ISSUER = "agent_egress_authorization"
AGENT_EGRESS_POLICY_APPROVAL_ID = "agent_egress_authorization"
# Fallback when the sandbox session lifetime is unknown to the caller; the
# effective default is derived from the session's absolute TTL by
# ``agent_egress_policy_ttl_seconds``.
AGENT_EGRESS_POLICY_DEFAULT_TTL_SECONDS = 86400
AGENT_EGRESS_POLICY_MAX_TTL_SECONDS = 7 * 86400
# Margin added on top of the session's absolute TTL so a snapshot cannot expire
# while the container it authorizes is still legally alive.
AGENT_EGRESS_POLICY_TTL_MARGIN_SECONDS = 1800

# RFC 5737 documentation ranges and RFC 3849 IPv6 documentation range are not
# reachable on the public internet; treating them as unreachable keeps the
# classifier conservative.
DOCUMENTATION_RANGES = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("2001:db8::/32"),
)

# Well-known provider metadata endpoints that must never be reachable through a
# reviewed policy, in addition to the generic link-local block that already
# covers 169.254.169.254.
KNOWN_METADATA_ADDRESSES = frozenset(
    {
        "169.254.169.254",  # AWS / Azure / GCP instance metadata
        "100.100.100.200",  # Alibaba Cloud instance metadata
        "192.0.0.192",  # Cloudflare metadata (metadata.cp.cloudflare.com)
    }
)

FORBIDDEN_FAMILIES: tuple[tuple[str, tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]], ...] = (
    ("unspecified", (ipaddress.ip_network("0.0.0.0/8"), ipaddress.ip_network("::/128"))),
    ("loopback", (ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128"))),
    (
        "private",
        (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
            ipaddress.ip_network("fc00::/7"),
        ),
    ),
    ("link_local", (ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fe80::/10"))),
    ("multicast", (ipaddress.ip_network("224.0.0.0/4"), ipaddress.ip_network("ff00::/8"))),
    ("carrier_grade_nat", (ipaddress.ip_network("100.64.0.0/10"),)),
    ("broadcast", (ipaddress.ip_network("255.255.255.255/32"),)),
)

# Ranges that Python may expose as ordinary addresses but that are never
# valid destinations for a public-web fetch. ``is_global`` below is the final
# fail-closed gate; these named ranges keep audit reasons actionable.
NON_PUBLIC_RANGES = (
    ("benchmarking", (ipaddress.ip_network("198.18.0.0/15"),)),
    ("ietf_reserved", (ipaddress.ip_network("192.0.0.0/24"),)),
    ("protocol_assignment", (ipaddress.ip_network("192.88.99.0/24"),)),
    ("reserved", (ipaddress.ip_network("240.0.0.0/4"), ipaddress.ip_network("2001:10::/28"))),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now() -> datetime:
    """Public alias so callers/tests share the same clock helper."""
    return _utc_now()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class EgressPolicyDenied(Exception):
    """Raised when an outbound attempt is refused by the reviewed policy."""

    def __init__(self, reason: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


class EgressPolicyInvalid(Exception):
    """Raised when a policy document is malformed, stale, or unapproved."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _valid_label(label: str) -> bool:
    if not label or len(label) > LABEL_MAX_LENGTH:
        return False
    if label.startswith("-") or label.endswith("-"):
        return False
    return all(char.isalnum() or char == "-" for char in label)


def normalize_hostname(host: str) -> str:
    """Canonicalize a reviewed host identity, or reject it.

    - lowercases and strips exactly one trailing dot;
    - applies IDNA/Unicode normalization;
    - validates label lengths and characters;
    - **rejects IP literals** — a reviewed policy names domains, not addresses.
    """
    value = host.strip()
    if value.endswith("."):
        value = value[:-1]
    if not value or len(value) > HOSTNAME_MAX_LENGTH:
        raise EgressPolicyInvalid("hostname_out_of_range")
    lowered = value.casefold()
    labels = lowered.split(".")
    if len(labels) < 2:
        raise EgressPolicyInvalid("hostname_requires_registered_domain")
    normalized_parts: list[str] = []
    for label in labels:
        try:
            ascii_label = label.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise EgressPolicyInvalid("hostname_idna_invalid") from exc
        if not _valid_label(ascii_label):
            raise EgressPolicyInvalid("hostname_label_invalid")
        normalized_parts.append(ascii_label)
    candidate = ".".join(normalized_parts)
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise EgressPolicyInvalid("hostname_must_be_domain")
    return candidate


def _is_fake_ip_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether ``address`` falls inside a configured fake-IP range.

    Fake-IP is the synthetic address space a local TUN proxy (Clash-style,
    fake-ip mode) answers DNS with; the egress proxy must treat those
    addresses as public so sandbox CONNECTs keep working on such machines.
    Opt-in via ``ssrf_fake_ip_ranges``; empty keeps the strict classifier.
    """
    raw = get_settings().ssrf_fake_ip_ranges
    if not raw:
        return False
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            if address in ipaddress.ip_network(candidate, strict=False):
                return True
        except ValueError:
            continue
    return False


def _is_configured_deny_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    raw = getattr(get_settings(), "sandbox_deny_cidrs", "") or ""
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            network = ipaddress.ip_network(candidate, strict=False)
        except ValueError:
            logger.warning("Ignoring invalid SANDBOX_DENY_CIDRS entry %r", candidate)
            continue
        if address.version == network.version and address in network:
            return True
    return False


def classify_ip_address(value: str) -> str:
    """Return a coarse classification: 'public' or a forbidden category name."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return "invalid"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        # IPv4-mapped IPv6 is just another spelling of an IPv4 destination.
        # Normalizing it closes bypasses such as ::ffff:127.0.0.1.
        address = address.ipv4_mapped
    normalized = str(address)
    if normalized in KNOWN_METADATA_ADDRESSES:
        return "metadata"
    if _is_configured_deny_address(address):
        return "deployment_denied"
    for category, networks in FORBIDDEN_FAMILIES:
        if any(address in network for network in networks):
            return category
    # Fake-IP TUN ranges are an explicit local-proxy compatibility escape
    # hatch. They can never override loopback/private/link-local/metadata
    # classifications above.
    if _is_fake_ip_address(address):
        return "public"
    if any(address in network for network in DOCUMENTATION_RANGES):
        return "documentation"
    for category, networks in NON_PUBLIC_RANGES:
        if any(address in network for network in networks):
            return category
    # The standard-library global flag is the final fail-closed gate. It
    # catches reserved/future-use ranges that are not worth enumerating.
    if not address.is_global:
        return "non_global"
    return "public"


AddressResolver = Callable[[str], list[str]]


def system_resolver(host: str) -> list[str]:
    """Resolve a hostname to every address (IPv4 + IPv6) without reordering."""
    import socket

    try:
        results = socket.getaddrinfo(host, None)
    except OSError:
        return []
    return sorted({item[4][0] for item in results})


def _classify_all(addresses: Iterable[str]) -> list[tuple[str, str]]:
    classified = [(address, classify_ip_address(address)) for address in addresses]
    if not classified:
        raise EgressPolicyDenied("dns_no_addresses")
    forbidden = [item for item in classified if item[1] != "public"]
    if forbidden:
        raise EgressPolicyDenied("dns_address_classified_forbidden", details={"forbidden_answers": forbidden})
    return classified


@dataclass(frozen=True)
class PolicyHost:
    host: str
    ports: tuple[int, ...] = (443,)
    protocols: tuple[str, ...] = (PROTOCOL_HTTPS,)


@dataclass(frozen=True)
class EgressPolicy:
    """Validated, immutable reviewed outbound policy for one workspace.

    ``digest`` is the SHA-256 of the canonical policy JSON and is the identity a
    sandbox carries in its environment so the proxy and audit trail agree on
    exactly which policy revision was in force. ``allow_all_public`` (opt-in
    no-interception mode) skips the exact-host allowlist at CONNECT time while
    still requiring every resolved address to classify as public.
    """

    workspace_id: str
    approval_id: str
    issued_at: datetime
    expires_at: datetime
    hosts: tuple[PolicyHost, ...]
    issuer: str
    digest: str
    raw: dict[str, Any]
    allow_all_public: bool = False
    capability: NetworkCapability = NetworkCapability.RESTRICTED_EGRESS
    max_requests: int | None = None
    max_bytes: int | None = None
    max_concurrency: int | None = None

    def is_expired(self, *, now: datetime | None = None) -> bool:
        current = now or _utc_now()
        return current > self.expires_at


def parse_policy_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise EgressPolicyInvalid("policy_datetime_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EgressPolicyInvalid("policy_datetime_invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def validate_egress_policy(data: Any, *, now: datetime | None = None) -> EgressPolicy:
    """Strictly validate a reviewed policy document.

    Rejects wildcard/suffix hosts, direct IP literals, non-HTTPS protocols,
    empty host lists (unless ``allow_all_public``), missing approval/expiry
    fields, expired policies, and any ambiguity. The digest is computed over
    the canonical form so the same document always yields the same revision
    identity.
    """
    if not isinstance(data, dict):
        raise EgressPolicyInvalid("policy_must_be_object")
    workspace_id = data.get("workspace_id")
    approval_id = data.get("approval_id")
    issuer = data.get("issuer")
    allow_all_public = data.get("allow_all_public") is True
    capability = normalize_network_capability(
        data.get("capability", NetworkCapability.RESTRICTED_EGRESS.value)
    )
    issued_at = parse_policy_datetime(data.get("issued_at"))
    expires_at = parse_policy_datetime(data.get("expires_at"))
    if not isinstance(workspace_id, str) or not workspace_id:
        raise EgressPolicyInvalid("policy_missing_workspace")
    if not isinstance(approval_id, str) or not approval_id:
        raise EgressPolicyInvalid("policy_missing_approval")
    if not isinstance(issuer, str) or not issuer:
        raise EgressPolicyInvalid("policy_missing_issuer")
    if expires_at <= issued_at:
        raise EgressPolicyInvalid("policy_expiry_before_issue")
    if expires_at <= (now or _utc_now()):
        raise EgressPolicyInvalid("policy_expired")

    limits: dict[str, int | None] = {}
    for field_name in ("max_requests", "max_bytes", "max_concurrency"):
        raw_limit = data.get(field_name)
        if raw_limit is None:
            limits[field_name] = None
            continue
        if (
            not isinstance(raw_limit, int)
            or isinstance(raw_limit, bool)
            or raw_limit <= 0
        ):
            raise EgressPolicyInvalid(f"policy_{field_name}_invalid")
        limits[field_name] = raw_limit

    raw_hosts = data.get("hosts")
    if not isinstance(raw_hosts, list):
        raise EgressPolicyInvalid("policy_hosts_must_be_list")
    if not raw_hosts and not allow_all_public:
        raise EgressPolicyInvalid("policy_empty_hosts")
    if capability is NetworkCapability.OFFLINE and (raw_hosts or allow_all_public):
        raise EgressPolicyInvalid("policy_offline_has_hosts")

    hosts: list[PolicyHost] = []
    seen_hosts: set[str] = set()
    for entry in raw_hosts:
        if not isinstance(entry, dict):
            raise EgressPolicyInvalid("policy_host_must_be_object")
        host = normalize_hostname(str(entry.get("host") or ""))
        if host in seen_hosts:
            raise EgressPolicyInvalid("policy_duplicate_host")
        seen_hosts.add(host)
        raw_ports = entry.get("ports", [DEFAULT_PORT])
        if not isinstance(raw_ports, list) or not raw_ports:
            raise EgressPolicyInvalid("policy_host_no_ports")
        ports: list[int] = []
        for port in raw_ports:
            if not isinstance(port, int) or not (1 <= port <= 65535):
                raise EgressPolicyInvalid("policy_port_invalid")
            if port not in {DEFAULT_HTTP_PORT, DEFAULT_PORT}:
                raise EgressPolicyInvalid("policy_port_must_be_http_https")
            ports.append(port)
        raw_protocols = entry.get("protocols", [PROTOCOL_HTTPS])
        if not isinstance(raw_protocols, list) or not raw_protocols:
            raise EgressPolicyInvalid("policy_host_no_protocols")
        protocols: list[str] = []
        for protocol in raw_protocols:
            if protocol not in ALLOWED_PROTOCOL_PORTS:
                raise EgressPolicyInvalid("policy_protocol_not_http_https")
            protocols.append(protocol)
        if any(ALLOWED_PROTOCOL_PORTS[protocol] not in ports for protocol in protocols):
            raise EgressPolicyInvalid("policy_protocol_port_mismatch")
        if any(ALLOWED_PORT_PROTOCOLS[port] not in protocols for port in ports):
            raise EgressPolicyInvalid("policy_protocol_port_mismatch")
        hosts.append(
            PolicyHost(host=host, ports=tuple(sorted(set(ports))), protocols=tuple(protocols))
        )

    digest = hashlib.sha256(_canonical_json(data)).hexdigest()
    return EgressPolicy(
        workspace_id=workspace_id,
        approval_id=approval_id,
        issued_at=issued_at,
        expires_at=expires_at,
        hosts=tuple(hosts),
        issuer=issuer,
        digest=digest,
        raw=dict(data),
        allow_all_public=allow_all_public,
        capability=capability,
        max_requests=limits["max_requests"],
        max_bytes=limits["max_bytes"],
        max_concurrency=limits["max_concurrency"],
    )


def authorize_connect(
    policy: EgressPolicy,
    host: str,
    port: int,
    *,
    protocol: str = PROTOCOL_HTTPS,
    resolver: AddressResolver = system_resolver,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """Authorize one outbound connection attempt, or raise ``EgressPolicyDenied``.

    Returns the resolved *public* address the proxy should connect to plus an
    audit payload. Resolution happens at connection time and every answer is
    re-classified, so DNS rebinding to a private address is refused.
    """
    audit: dict[str, Any] = {
        "policy_digest": policy.digest,
        "approval_id": policy.approval_id,
        "workspace_id": policy.workspace_id,
        "capability": policy.capability.value,
        "protocol": protocol,
    }
    if policy.is_expired(now=now):
        raise EgressPolicyDenied("policy_expired", details=audit)

    if policy.capability is NetworkCapability.OFFLINE:
        raise EgressPolicyDenied("capability_offline", details=audit)
    expected_port = ALLOWED_PROTOCOL_PORTS.get(protocol)
    if expected_port is None or port != expected_port:
        raise EgressPolicyDenied(
            "port_protocol_not_allowed",
            details={**audit, "requested_port": port, "allowed_ports": [expected_port] if expected_port else []},
        )
    try:
        normalized = normalize_hostname(host)
    except EgressPolicyInvalid as exc:
        raise EgressPolicyDenied("host_not_normalizable", details={**audit, "host": host, "reason": exc.reason}) from exc

    audit["host"] = normalized
    rule: PolicyHost | None = None
    if not policy.allow_all_public:
        rule = next(
            (candidate for candidate in policy.hosts if candidate.host == normalized),
            None,
        )
        if rule is None:
            raise EgressPolicyDenied(
                "host_not_in_allowlist",
                details={**audit, "requested_port": port},
            )
    else:
        # No-interception mode: any public DNS host is accepted, but every
        # resolved address is still re-classified below so private, loopback,
        # link-local, multicast and cloud-metadata targets stay denied.
        rule = None

    if rule is not None and (
        port not in rule.ports or protocol not in rule.protocols
    ):
        raise EgressPolicyDenied(
            "port_protocol_not_allowed",
            details={
                **audit,
                "requested_port": port,
                "allowed_ports": list(rule.ports),
                "allowed_protocols": list(rule.protocols),
            },
        )

    addresses = resolver(normalized)
    classified = _classify_all(addresses)
    audit["resolved_addresses"] = [address for address, _ in classified]
    return classified[0][0], audit


def load_workspace_policy_file(policy_dir: str | Path, workspace_id: str, *, now: datetime | None = None) -> EgressPolicy | None:
    """Load and validate the reviewed policy for one workspace.

    Missing files return ``None`` (the sandbox stays offline). Malformed or
    expired policy files also return ``None`` and are logged loudly so a stale
    review cannot silently widen access — absent a valid policy, egress is
    denied.
    """
    directory = Path(policy_dir)
    policy_path = directory / f"{workspace_id}.json"
    try:
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.error(
            "Sandbox egress policy file %s is unreadable; egress denied for workspace %s: %s",
            policy_path,
            workspace_id,
            exc,
        )
        return None
    try:
        policy = validate_egress_policy(raw, now=now)
    except EgressPolicyInvalid as exc:
        logger.error(
            "Sandbox egress policy %s is invalid; egress denied for workspace %s: %s",
            policy_path,
            workspace_id,
            exc.reason,
        )
        return None
    if policy.capability is NetworkCapability.OFFLINE:
        logger.error(
            "Sandbox egress policy %s is OFFLINE; generic egress denied for workspace %s",
            policy_path,
            workspace_id,
        )
        return None
    return policy


def derive_egress_policy_for_fetch(
    *,
    workspace_id: str,
    allowed_domains: Iterable[str],
    ttl_seconds: int = WEB_FETCH_POLICY_DEFAULT_TTL_SECONDS,
    allow_all_public: bool = False,
    max_requests: int | None = None,
    max_bytes: int | None = None,
    max_concurrency: int | None = None,
    now: datetime | None = None,
) -> EgressPolicy:
    """Derive a narrow, short-lived egress policy from the unified fetch allowlist.

    The shared ``access.allowlist.allowed_domains`` list is the single source of
    truth; this function turns it into an ``EgressPolicy`` limited to public
    HTTP/HTTPS ports 80/443, records ``issuer=web_fetch_policy`` so the egress
    proxy and audit trail can distinguish it from a separately-reviewed generic
    policy, and carries its request/byte/concurrency budget. An empty or invalid
    allowlist fails closed unless ``allow_all_public`` opts into no-interception
    mode.
    """
    if not isinstance(workspace_id, str) or not workspace_id:
        raise EgressPolicyInvalid("policy_missing_workspace")
    if (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or not 0 < ttl_seconds <= WEB_FETCH_POLICY_MAX_TTL_SECONDS
    ):
        raise EgressPolicyInvalid("policy_ttl_invalid")
    domains = list(
        dict.fromkeys(normalize_hostname(str(value)) for value in allowed_domains)
    )
    if not domains and not allow_all_public:
        raise EgressPolicyInvalid("policy_empty_hosts")
    issued = now or _utc_now()
    data: dict[str, Any] = {
        "workspace_id": workspace_id,
        "approval_id": WEB_FETCH_POLICY_APPROVAL_ID,
        "issuer": WEB_FETCH_POLICY_ISSUER,
        "capability": NetworkCapability.FETCH.value,
        "issued_at": issued.isoformat(),
        "expires_at": (issued + timedelta(seconds=ttl_seconds)).isoformat(),
        "hosts": [
            {
                "host": domain,
                "ports": [DEFAULT_HTTP_PORT, DEFAULT_PORT],
                "protocols": [PROTOCOL_HTTP, PROTOCOL_HTTPS],
            }
            for domain in domains
        ],
    }
    for name, value in (
        ("max_requests", max_requests),
        ("max_bytes", max_bytes),
        ("max_concurrency", max_concurrency),
    ):
        if value is not None:
            data[name] = value
    if allow_all_public:
        data["allow_all_public"] = True
    return validate_egress_policy(data, now=now)


def store_workspace_fetch_policy_file(policy_dir: str | Path, policy: EgressPolicy) -> Path:
    """Atomically persist the derived fetch policy to its own workspace file."""
    directory = Path(policy_dir)
    directory.mkdir(parents=True, exist_ok=True)
    policy_path = directory / f"{policy.workspace_id}{WEB_FETCH_POLICY_FILE_SUFFIX}"
    temporary = policy_path.with_name(
        f".{policy_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(policy.raw, ensure_ascii=False, sort_keys=True) + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, policy_path)
    finally:
        temporary.unlink(missing_ok=True)
    return policy_path


def refresh_workspace_fetch_policy_file(
    policy_dir: str | Path,
    workspace_id: str,
    allowed_domains: Iterable[str],
    *,
    allow_all_public: bool = False,
    ttl_seconds: int = WEB_FETCH_POLICY_DEFAULT_TTL_SECONDS,
    max_requests: int | None = None,
    max_bytes: int | None = None,
    max_concurrency: int | None = None,
    now: datetime | None = None,
) -> bool:
    """Idempotently refresh the derived fetch egress policy file.

    Returns ``True`` when the file was (re)written, ``False`` when the on-disk
    policy is still valid and semantically identical. The warm container pool
    reuses fetch containers without re-deriving the envelope, so without a
    refresh here the short-lived policy (default 600s TTL) would silently
    expire and the egress proxy would deny every CONNECT for the workspace
    until the next container creation — this is the fix for that gap: refresh
    on every fetch, but skip the disk write when the existing policy is
    unexpired and unchanged.
    """
    policy = derive_egress_policy_for_fetch(
        workspace_id=workspace_id,
        allowed_domains=allowed_domains,
        ttl_seconds=ttl_seconds,
        allow_all_public=allow_all_public,
        max_requests=max_requests,
        max_bytes=max_bytes,
        max_concurrency=max_concurrency,
        now=now,
    )
    policy_path = Path(policy_dir) / f"{workspace_id}{WEB_FETCH_POLICY_FILE_SUFFIX}"
    try:
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
        existing = validate_egress_policy(raw, now=now)  # raises when expired/invalid
    except (OSError, ValueError, EgressPolicyInvalid):
        existing = None
    if (
        existing is not None
        and existing.allow_all_public == policy.allow_all_public
        and set(existing.hosts) == set(policy.hosts)
        and existing.capability is policy.capability
        and existing.max_requests == policy.max_requests
        and existing.max_bytes == policy.max_bytes
        and existing.max_concurrency == policy.max_concurrency
    ):
        return False
    store_workspace_fetch_policy_file(policy_dir, policy)
    return True


def store_workspace_policy_file(policy_dir: str | Path, policy: EgressPolicy) -> Path:
    """Atomically persist a derived generic Agent egress policy.

    This writes the same ``{workspace_id}.json`` slot the sandbox envelope and
    generic egress proxy read, so approval-derived hosts take effect for new
    sandbox sessions without changing the proxy contract.
    """
    directory = Path(policy_dir)
    directory.mkdir(parents=True, exist_ok=True)
    policy_path = directory / f"{policy.workspace_id}.json"
    temporary = policy_path.with_name(
        f".{policy_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(policy.raw, ensure_ascii=False, sort_keys=True) + "\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, policy_path)
    finally:
        temporary.unlink(missing_ok=True)
    return policy_path


def agent_egress_policy_ttl_seconds(
    absolute_session_ttl_seconds: int | None,
    container_ttl_seconds: int | None = None,
) -> int:
    """Default lifetime for a derived generic Agent egress policy snapshot.

    A snapshot is only rewritten when a sandbox container is created
    (``_egress_envelope``), so it has to outlive every container that can hold
    its digest. Two independent knobs bound such a container: the execution
    container's absolute TTL (``sandbox_container_absolute_ttl_seconds``, used by
    both the per-chat sweep and the pooled instance) and the session's workspace
    TTL (``sandbox_workspace_absolute_ttl_seconds``, clamped by
    ``_touch_session``). The snapshot lifetime is derived from the larger of the
    two plus a margin, so raising either knob cannot silently make a live
    container's permission expire first.

    This does not widen the authorization window: the digest is only ever handed
    to containers this API creates, and their lifetime stays bounded by those
    same knobs, so nothing else can reach a longer-lived snapshot.
    Deployment-reviewed baselines and ``allow_once`` leases still clamp the
    result to their own earlier expiry (see
    ``EgressApprovalService.ensure_agent_egress_policy``).
    """
    bounds: list[int] = []
    for value in (absolute_session_ttl_seconds, container_ttl_seconds):
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            bounds.append(parsed)
    base = max(bounds) if bounds else AGENT_EGRESS_POLICY_DEFAULT_TTL_SECONDS
    return max(
        60,
        min(
            AGENT_EGRESS_POLICY_MAX_TTL_SECONDS,
            base + AGENT_EGRESS_POLICY_TTL_MARGIN_SECONDS,
        ),
    )


def derive_egress_policy_for_agent(
    *,
    workspace_id: str,
    allowed_hosts: Iterable[str],
    ttl_seconds: int = AGENT_EGRESS_POLICY_DEFAULT_TTL_SECONDS,
    allow_all_public: bool = False,
    max_requests: int | None = None,
    max_bytes: int | None = None,
    max_concurrency: int | None = None,
    now: datetime | None = None,
) -> EgressPolicy:
    """Derive a generic Agent egress policy from the durable approval allowlist.

    The workspace ``agent_egress`` allowlist plus active ``allow_once`` leases
    is the source of truth; this function turns it into an HTTPS-443-only
    ``EgressPolicy`` with ``issuer=agent_egress_authorization``. An empty host
    set fails closed (the caller should leave the policy file absent) unless
    ``allow_all_public`` opts into no-interception mode. The resulting digest is
    stable for the same canonical document, so sandbox envelopes and proxy
    registries can agree on the revision identity.
    """
    if not isinstance(workspace_id, str) or not workspace_id:
        raise EgressPolicyInvalid("policy_missing_workspace")
    if (
        not isinstance(ttl_seconds, int)
        or isinstance(ttl_seconds, bool)
        or not 0 < ttl_seconds <= AGENT_EGRESS_POLICY_MAX_TTL_SECONDS
    ):
        raise EgressPolicyInvalid("policy_ttl_invalid")
    hosts = list(
        dict.fromkeys(normalize_hostname(str(value)) for value in allowed_hosts)
    )
    if not hosts and not allow_all_public:
        raise EgressPolicyInvalid("policy_empty_hosts")
    issued = now or _utc_now()
    data: dict[str, Any] = {
        "workspace_id": workspace_id,
        "approval_id": AGENT_EGRESS_POLICY_APPROVAL_ID,
        "issuer": AGENT_EGRESS_POLICY_ISSUER,
        "capability": NetworkCapability.RESTRICTED_EGRESS.value,
        "issued_at": issued.isoformat(),
        "expires_at": (issued + timedelta(seconds=ttl_seconds)).isoformat(),
        "hosts": [
            {"host": host, "ports": [DEFAULT_PORT], "protocols": [PROTOCOL_HTTPS]}
            for host in hosts
        ],
    }
    for name, value in (
        ("max_requests", max_requests),
        ("max_bytes", max_bytes),
        ("max_concurrency", max_concurrency),
    ):
        if value is not None:
            data[name] = value
    if allow_all_public:
        data["allow_all_public"] = True
    return validate_egress_policy(data, now=now)


def load_workspace_fetch_policy_file(
    policy_dir: str | Path,
    workspace_id: str,
    *,
    now: datetime | None = None,
) -> EgressPolicy | None:
    """Load and validate the derived fetch policy for one workspace.

    This reads a file *separate* from the generic reviewed policy
    (``{workspace_id}.web_fetch.json``) so fetch approvals never widen generic
    Agent egress. Missing, malformed, expired, or wrong-provenance files return
    ``None`` and the fetch container stays offline.
    """
    directory = Path(policy_dir)
    policy_path = directory / f"{workspace_id}{WEB_FETCH_POLICY_FILE_SUFFIX}"
    try:
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.error(
            "Sandbox web_fetch policy %s is unreadable; fetch egress denied for workspace %s: %s",
            policy_path,
            workspace_id,
            exc,
        )
        return None
    try:
        policy = validate_egress_policy(raw, now=now)
    except EgressPolicyInvalid as exc:
        logger.error(
            "Sandbox web_fetch policy %s is invalid; fetch egress denied for workspace %s: %s",
            policy_path,
            workspace_id,
            exc.reason,
        )
        return None
    if policy.capability is not NetworkCapability.FETCH:
        logger.error(
            "Sandbox web_fetch policy %s has unexpected capability %r; fetch egress denied for workspace %s",
            policy_path,
            policy.capability.value,
            workspace_id,
        )
        return None
    if policy.issuer != WEB_FETCH_POLICY_ISSUER:
        logger.error(
            "Sandbox web_fetch policy %s has unexpected issuer %r; fetch egress denied for workspace %s",
            policy_path,
            policy.issuer,
            workspace_id,
        )
        return None
    return policy
