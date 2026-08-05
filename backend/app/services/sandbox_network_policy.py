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
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

HOSTNAME_MAX_LENGTH = 253
LABEL_MAX_LENGTH = 63
PROTOCOL_HTTPS = "https"
DEFAULT_PORT = 443

# Derived fetch egress: the unified ``web_fetch.policy`` allowlist is the single
# source of truth, and fetch egress is derived from it into a *separate* policy
# file with its own provenance. This keeps the generic per-workspace reviewed
# policy (and therefore generic Agent egress) untouched by fetch approvals.
WEB_FETCH_POLICY_ISSUER = "web_fetch_policy"
WEB_FETCH_POLICY_APPROVAL_ID = "web_fetch_policy"
WEB_FETCH_POLICY_DEFAULT_TTL_SECONDS = 600
WEB_FETCH_POLICY_MAX_TTL_SECONDS = 86400
WEB_FETCH_POLICY_FILE_SUFFIX = ".web_fetch.json"

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
    value = host.strip().strip(".")
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


def classify_ip_address(value: str) -> str:
    """Return a coarse classification: 'public' or a forbidden category name."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return "invalid"
    normalized = str(address)
    if normalized in KNOWN_METADATA_ADDRESSES:
        return "metadata"
    for category, networks in FORBIDDEN_FAMILIES:
        if any(address in network for network in networks):
            return category
    if any(address in network for network in DOCUMENTATION_RANGES):
        return "documentation"
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
    exactly which policy revision was in force.
    """

    workspace_id: str
    approval_id: str
    issued_at: datetime
    expires_at: datetime
    hosts: tuple[PolicyHost, ...]
    issuer: str
    digest: str
    raw: dict[str, Any]

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
    empty host lists, missing approval/expiry fields, expired policies, and any
    ambiguity. The digest is computed over the canonical form so the same
    document always yields the same revision identity.
    """
    if not isinstance(data, dict):
        raise EgressPolicyInvalid("policy_must_be_object")
    workspace_id = data.get("workspace_id")
    approval_id = data.get("approval_id")
    issuer = data.get("issuer")
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

    raw_hosts = data.get("hosts")
    if not isinstance(raw_hosts, list) or not raw_hosts:
        raise EgressPolicyInvalid("policy_empty_hosts")

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
            ports.append(port)
        raw_protocols = entry.get("protocols", [PROTOCOL_HTTPS])
        if not isinstance(raw_protocols, list) or not raw_protocols:
            raise EgressPolicyInvalid("policy_host_no_protocols")
        protocols: list[str] = []
        for protocol in raw_protocols:
            if protocol != PROTOCOL_HTTPS:
                raise EgressPolicyInvalid("policy_non_https_protocol")
            protocols.append(protocol)
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
    )


def authorize_connect(
    policy: EgressPolicy,
    host: str,
    port: int,
    *,
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
    }
    if policy.is_expired(now=now):
        raise EgressPolicyDenied("policy_expired", details=audit)

    try:
        normalized = normalize_hostname(host)
    except EgressPolicyInvalid as exc:
        raise EgressPolicyDenied("host_not_normalizable", details={**audit, "host": host, "reason": exc.reason}) from exc

    audit["host"] = normalized
    rule = next((candidate for candidate in policy.hosts if candidate.host == normalized), None)
    if rule is None:
        raise EgressPolicyDenied("host_not_in_allowlist", details={**audit, "requested_port": port})

    if port not in rule.ports:
        raise EgressPolicyDenied("port_not_allowed", details={**audit, "requested_port": port, "allowed_ports": list(rule.ports)})

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
        return validate_egress_policy(raw, now=now)
    except EgressPolicyInvalid as exc:
        logger.error(
            "Sandbox egress policy %s is invalid; egress denied for workspace %s: %s",
            policy_path,
            workspace_id,
            exc.reason,
        )
        return None


def derive_egress_policy_for_fetch(
    *,
    workspace_id: str,
    allowed_domains: Iterable[str],
    ttl_seconds: int = WEB_FETCH_POLICY_DEFAULT_TTL_SECONDS,
    now: datetime | None = None,
) -> EgressPolicy:
    """Derive a narrow, short-lived egress policy from the unified fetch allowlist.

    The shared ``web_fetch.policy.allowed_domains`` list is the single source of
    truth; this function turns it into an ``EgressPolicy`` that is HTTPS-443-only,
    expires quickly (so a stale derivation cannot outlive an allowlist change),
    and records ``issuer=web_fetch_policy`` so the egress proxy and audit trail
    can distinguish it from a separately-reviewed generic policy. An empty or
    invalid allowlist fails closed.
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
    if not domains:
        raise EgressPolicyInvalid("policy_empty_hosts")
    issued = now or _utc_now()
    data = {
        "workspace_id": workspace_id,
        "approval_id": WEB_FETCH_POLICY_APPROVAL_ID,
        "issuer": WEB_FETCH_POLICY_ISSUER,
        "issued_at": issued.isoformat(),
        "expires_at": (issued + timedelta(seconds=ttl_seconds)).isoformat(),
        "hosts": [
            {"host": domain, "ports": [DEFAULT_PORT], "protocols": [PROTOCOL_HTTPS]}
            for domain in domains
        ],
    }
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
    if policy.issuer != WEB_FETCH_POLICY_ISSUER:
        logger.error(
            "Sandbox web_fetch policy %s has unexpected issuer %r; fetch egress denied for workspace %s",
            policy_path,
            policy.issuer,
            workspace_id,
        )
        return None
    return policy
