from __future__ import annotations

"""Reclaim *derived* egress-policy snapshots that can no longer authorize anything.

``LEARNGRAPH_SANDBOX_EGRESS_POLICY_DIR`` holds two derived files per workspace:
``{workspace_id}.json`` (generic Agent egress) and ``{workspace_id}.web_fetch.json``
(fetch egress). They are written by the API, read by the standalone egress proxy
and re-derived on demand, so the directory is a cache — but nothing used to own
its lifecycle, which is why snapshots of long-abandoned workspaces stayed behind
forever (and, before the proxy's state-driven logging, produced one warning per
refresh interval for as long as the process ran).

Deletion here is deliberately hard to trigger. A file is removed only when every
gate below holds:

1. its name is exactly ``{workspace_id}.json`` or ``{workspace_id}.web_fetch.json``
   with a safe workspace id;
2. the document parses, its ``workspace_id`` matches the filename, and its
   ``issuer`` is the *derived* one for that kind — a deployment-reviewed baseline
   uses a different issuer and is never touched;
3. it has been expired for at least ``grace_seconds`` (default 1h), which also
   implies nothing re-derived it recently: a rewrite happens on every fetch and
   on every sandbox container creation, and once expired a rewrite always
   changes the file;
4. the workspace has no live sandbox session (``STARTING``/``RUNNING``/
   ``WARM_IDLE``) — a live container could still be carrying this digest;
5. either the workspace row is gone, or re-deriving now would produce nothing:
   no unrevoked ``agent_egress`` grant, no unconsumed ``allow_once`` lease, no
   ``access.allowlist`` domains and neither the ``allow_all`` nor the
   sandbox-only ``allow_public_network`` switch (read straight from
   ``workspace_settings`` — never through the provider-plan cache, because this
   decision deletes a file);
6. the bytes are re-read and compared immediately before unlinking
   (compare-and-delete), so a concurrent rewrite is never removed.

Every deletion is logged with path, workspace, kind and reason. Workspace data
(database, memory, storage, sandbox workspace trees) is never touched.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.models import (
    EGRESS_APPROVAL_CAPABILITY,
    EgressAuthorizationRequest,
    HostAuthorizationGrant,
    SandboxSession,
    Workspace,
    WorkspaceSetting,
)
from app.providers.factory import ACCESS_ALLOWLIST_SETTING_KEY, SANDBOX_EGRESS_SETTING_KEY
from app.services.sandbox_network_policy import (
    AGENT_EGRESS_POLICY_ISSUER,
    WEB_FETCH_POLICY_FILE_SUFFIX,
    WEB_FETCH_POLICY_ISSUER,
    EgressPolicyInvalid,
    parse_policy_datetime,
    utc_now,
)

logger = logging.getLogger(__name__)

# Default grace after expiry before a provably dead snapshot may be removed.
DEFAULT_PRUNE_GRACE_SECONDS = 3600

# Lifecycle states that mean a container may still be running and holding the
# digest of the snapshot under consideration (mirrors ``_enforce_sandbox_capacity``).
LIVE_SESSION_STATES = ("STARTING", "RUNNING", "WARM_IDLE")

_SAFE_WORKSPACE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}")


@dataclass(frozen=True, slots=True)
class PrunedPolicyFile:
    path: str
    workspace_id: str
    kind: str
    reason: str


def prune_derived_egress_policies(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    grace_seconds: int = DEFAULT_PRUNE_GRACE_SECONDS,
) -> dict[str, Any]:
    """Remove derived egress-policy snapshots that are provably dead and unused.

    Returns counters plus the list of removed files. Never raises for a
    filesystem problem: an unreadable directory or a failed unlink is skipped,
    since the *only* purpose of this routine is to stop an inert cache from
    growing.
    """
    current = now or utc_now()
    directory = Path(settings.sandbox_egress_policy_dir)
    totals: dict[str, Any] = {"scanned": 0, "pruned": 0, "kept": 0, "skipped": 0}
    pruned: list[PrunedPolicyFile] = []
    try:
        entries = sorted(directory.glob("*.json"))
    except OSError as exc:
        logger.warning("Egress policy directory %s is unreadable: %s", directory, exc)
        return {**totals, "pruned_files": pruned}
    grace = timedelta(seconds=max(0, int(grace_seconds)))
    for path in entries:
        classified = _classify_policy_path(path)
        if classified is None:
            totals["skipped"] += 1
            continue
        totals["scanned"] += 1
        kind, workspace_id = classified
        snapshot = _read_own_snapshot(path, kind, workspace_id)
        if snapshot is None:
            # Unreadable, foreign issuer (deployment-reviewed baseline) or a
            # filename/document mismatch: not ours to remove.
            totals["skipped"] += 1
            continue
        raw_bytes, expires_at = snapshot
        if current - expires_at < grace:
            totals["kept"] += 1
            continue
        workspace_exists = bool(
            db.scalar(
                select(func.count())
                .select_from(Workspace)
                .where(Workspace.id == workspace_id)
            )
            or 0
        )
        if workspace_exists:
            if not _authorization_gone(db, workspace_id, kind):
                totals["kept"] += 1
                continue
            reason = "authorization_gone"
        else:
            reason = "workspace_missing"
        if _has_live_sandbox_session(db, workspace_id):
            totals["kept"] += 1
            continue
        if not _delete_if_unchanged(path, raw_bytes):
            totals["skipped"] += 1
            continue
        logger.info(
            "Pruned derived egress policy snapshot %s (workspace=%s kind=%s reason=%s expired_at=%s)",
            path,
            workspace_id,
            kind,
            reason,
            expires_at.isoformat(),
        )
        pruned.append(
            PrunedPolicyFile(
                path=str(path),
                workspace_id=workspace_id,
                kind=kind,
                reason=reason,
            )
        )
        totals["pruned"] += 1
    return {**totals, "pruned_files": pruned}


def _classify_policy_path(path: Path) -> tuple[str, str] | None:
    """``(kind, workspace_id)`` for an owned filename, else ``None``."""
    name = path.name
    if name.endswith(WEB_FETCH_POLICY_FILE_SUFFIX):
        kind, base = "web_fetch", name[: -len(WEB_FETCH_POLICY_FILE_SUFFIX)]
    elif name.endswith(".json"):
        kind, base = "agent", name[: -len(".json")]
    else:
        return None
    if not _SAFE_WORKSPACE_ID.fullmatch(base or ""):
        return None
    return kind, base


def _read_own_snapshot(
    path: Path,
    kind: str,
    workspace_id: str,
) -> tuple[bytes, datetime] | None:
    """Return ``(bytes, expires_at)`` for a snapshot this API derived itself."""
    expected_issuer = (
        AGENT_EGRESS_POLICY_ISSUER if kind == "agent" else WEB_FETCH_POLICY_ISSUER
    )
    try:
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("issuer") != expected_issuer:
        return None
    if raw.get("workspace_id") != workspace_id:
        return None
    try:
        expires_at = parse_policy_datetime(raw.get("expires_at"))
    except EgressPolicyInvalid:
        return None
    return raw_bytes, expires_at


def _authorization_gone(db: Session, workspace_id: str, kind: str) -> bool:
    """Whether re-deriving this snapshot right now would yield nothing.

    Mirrors the derivation sources (``EgressApprovalService.ensure_agent_egress_policy``
    for ``agent``, ``derive_egress_policy_for_fetch`` for ``web_fetch``) so a
    snapshot that is still authorized is always kept.
    """
    settings_value = _workspace_setting(db, workspace_id, ACCESS_ALLOWLIST_SETTING_KEY)
    if isinstance(settings_value, dict) and settings_value.get("allow_all") is True:
        return False
    domains = settings_value.get("allowed_domains") if isinstance(settings_value, dict) else None
    if isinstance(domains, list) and any(
        isinstance(item, str) and item.strip() for item in domains
    ):
        return False
    if kind == "web_fetch":
        # Fetch egress is derived from the unified allowlist alone.
        return True
    sandbox_value = _workspace_setting(db, workspace_id, SANDBOX_EGRESS_SETTING_KEY) or {}
    if sandbox_value.get("allow_public_network") is True:
        return False
    grants = (
        db.scalar(
            select(func.count())
            .select_from(HostAuthorizationGrant)
            .where(
                HostAuthorizationGrant.workspace_id == workspace_id,
                HostAuthorizationGrant.capability == EGRESS_APPROVAL_CAPABILITY,
                HostAuthorizationGrant.subject_type == "workspace",
                HostAuthorizationGrant.subject_id == workspace_id,
                HostAuthorizationGrant.revoked_at.is_(None),
            )
        )
        or 0
    )
    if grants:
        return False
    leases = (
        db.scalar(
            select(func.count())
            .select_from(EgressAuthorizationRequest)
            .where(
                EgressAuthorizationRequest.workspace_id == workspace_id,
                EgressAuthorizationRequest.capability == EGRESS_APPROVAL_CAPABILITY,
                EgressAuthorizationRequest.status == "approved",
                EgressAuthorizationRequest.decision == "allow_once",
                EgressAuthorizationRequest.consumed_at.is_(None),
            )
        )
        or 0
    )
    return not leases


def _workspace_setting(db: Session, workspace_id: str, key: str) -> dict | None:
    setting = db.scalar(
        select(WorkspaceSetting).where(
            WorkspaceSetting.workspace_id == workspace_id,
            WorkspaceSetting.key == key,
        )
    )
    value = setting.value if setting is not None else None
    return value if isinstance(value, dict) else None


def _has_live_sandbox_session(db: Session, workspace_id: str) -> bool:
    count = (
        db.scalar(
            select(func.count())
            .select_from(SandboxSession)
            .where(
                SandboxSession.workspace_id == workspace_id,
                SandboxSession.lifecycle_state.in_(LIVE_SESSION_STATES),
                SandboxSession.cleanup_status != "cleaned",
            )
        )
        or 0
    )
    return count > 0


def _delete_if_unchanged(path: Path, expected: bytes) -> bool:
    """Compare-and-delete: never remove a snapshot that changed underneath us."""
    try:
        if path.read_bytes() != expected:
            return False
        path.unlink()
        return True
    except OSError as exc:
        logger.warning("Egress policy snapshot prune skipped %s: %s", path, exc)
        return False
