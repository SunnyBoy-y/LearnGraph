from __future__ import annotations

"""Generic Agent egress approval queue — durable control-plane foundation.

D2.1: this is the persistence/API basis for *generic* Agent egress approvals,
parallel to (and deliberately separate from) the web_fetch authorization
machinery. See ``doc/md-D2-1_通用审批通道设计.md``.

Two non-negotiable contracts:

* Contract A — the only authorization resource is a canonical exact hostname.
  This service never saves or matches commands, argv, prompts, URL paths or
  request bodies. ``request_context`` is display/audit context only.

* Contract B — a user decision only adds a host to the allowlist. It never
  writes IPs, CIDRs, ``allow_private`` or classifier exceptions. The runtime
  ``SandboxEgressProxy.authorize_connect`` still resolves every CONNECT and
  re-classifies addresses, so an approved host that later resolves to a
  private/loopback/metadata address is still denied. This implementation does
  NOT bypass the classifier.

This module does not touch ``web_fetch.policy`` or ``UserWebFetchPolicy``. It
derives the generic Agent egress policy from ``host_authorization_grants`` in
the ``agent_egress`` capability namespace into ``{workspace_id}.json`` so the
sandbox envelope and egress proxy see approval-derived hosts; it never shares
the web_fetch namespace.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import (
    EGRESS_APPROVAL_CAPABILITY,
    EXTERNAL_ACQUISITION_CAPABILITY,
    EGRESS_APPROVAL_DEFAULT_TTL_SECONDS,
    EGRESS_APPROVAL_MAX_TTL_SECONDS,
    EgressAuthorizationRequest,
    HostAuthorizationGrant,
)
from app.repositories.audit import AuditRepository
from app.services.sandbox_network_policy import EgressPolicyInvalid, normalize_hostname

ALLOWED_DECISIONS = frozenset({"allow_once", "allow_always", "deny"})

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes for ``DateTime(timezone=True)`` columns;
    normalize before comparing with a tz-aware ``now``."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class EgressApprovalService:
    """Workspace-scoped control plane for the generic egress approval queue."""

    def __init__(
        self,
        db: Session,
        workspace_id: str,
        settings: Settings | None = None,
        *,
        capability: str = EGRESS_APPROVAL_CAPABILITY,
    ) -> None:
        if capability not in {EGRESS_APPROVAL_CAPABILITY, EXTERNAL_ACQUISITION_CAPABILITY}:
            raise ValueError("Unsupported egress approval capability")
        self.db = db
        self.workspace_id = workspace_id
        self.settings = settings
        self.capability = capability
        self.audit = AuditRepository(db, workspace_id)

    # -- internal helpers ----------------------------------------------------

    def _load(self, request_id: str) -> EgressAuthorizationRequest:
        request = self.db.scalar(
            select(EgressAuthorizationRequest).where(
                EgressAuthorizationRequest.id == request_id,
                EgressAuthorizationRequest.workspace_id == self.workspace_id,
            )
        )
        if request is None:
            raise AppError(
                404,
                "egress_authorization_not_found",
                "Egress authorization request was not found",
            )
        return request

    def get_request(self, request_id: str) -> EgressAuthorizationRequest:
        """Public read of one request (404 when missing / out of workspace)."""
        return self._load(request_id)

    def ensure_agent_egress_policy(self, *, now: datetime | None = None):
        """Derive and persist the generic Agent egress policy for this workspace.

        Source of truth: workspace-scoped ``agent_egress`` grants, active
        ``allow_once`` leases, plus the unified ``access.allowlist`` domains
        (whitelisted hosts bypass the approval queue). When the workspace opted
        into no-interception mode (``access.allowlist.allow_all``), an
        allow-all public policy is derived instead — the proxy still rejects
        private/loopback/metadata targets at CONNECT time. A deployment-reviewed
        baseline file is preserved and unioned so approvals only add hosts.
        Returns the effective policy or ``None`` (offline).
        """
        from app.providers.factory import access_allow_all, access_allowlist_domains
        from app.services.sandbox_network_policy import (
            AGENT_EGRESS_POLICY_DEFAULT_TTL_SECONDS,
            AGENT_EGRESS_POLICY_ISSUER,
            AGENT_EGRESS_POLICY_MAX_TTL_SECONDS,
            derive_egress_policy_for_agent,
            load_workspace_policy_file,
            store_workspace_policy_file,
        )

        current = now or _utc_now()
        policy_dir = self.settings.sandbox_egress_policy_dir
        baseline = load_workspace_policy_file(
            policy_dir, self.workspace_id, now=current
        )
        # A file this service wrote itself is a derived snapshot, not a
        # deployment baseline. Unioning it back in would resurrect consumed
        # allow_once leases, so only a reviewed baseline with a different
        # issuer is preserved.
        baseline_is_reviewed = baseline is not None and baseline.issuer != AGENT_EGRESS_POLICY_ISSUER
        hosts: set[str] = set()
        expirations: list[datetime] = []
        if baseline_is_reviewed:
            hosts.update(item.host for item in baseline.hosts)
            expirations.append(_as_utc(baseline.expires_at))
        grants = self.db.scalars(
            select(HostAuthorizationGrant).where(
                HostAuthorizationGrant.workspace_id == self.workspace_id,
                HostAuthorizationGrant.capability == EGRESS_APPROVAL_CAPABILITY,
                HostAuthorizationGrant.subject_type == "workspace",
                HostAuthorizationGrant.subject_id == self.workspace_id,
                HostAuthorizationGrant.revoked_at.is_(None),
            )
        ).all()
        hosts.update(grant.hostname for grant in grants)
        leases = self.db.scalars(
            select(EgressAuthorizationRequest).where(
                EgressAuthorizationRequest.workspace_id == self.workspace_id,
                EgressAuthorizationRequest.capability == EGRESS_APPROVAL_CAPABILITY,
                EgressAuthorizationRequest.status == "approved",
                EgressAuthorizationRequest.decision == "allow_once",
                EgressAuthorizationRequest.consumed_at.is_(None),
            )
        ).all()
        for lease in leases:
            lease_expires = _as_utc(lease.expires_at)
            if lease_expires <= current:
                continue
            hosts.add(lease.hostname)
            expirations.append(lease_expires)
        allow_all = access_allow_all(self.db, self.workspace_id)
        if not allow_all:
            hosts.update(access_allowlist_domains(self.db, self.workspace_id))
        if not hosts and not allow_all:
            # No active approvals: preserve a reviewed baseline if present;
            # otherwise remove any stale approval-derived file so the sandbox
            # fails closed.
            if baseline_is_reviewed:
                return baseline
            path = Path(policy_dir) / f"{self.workspace_id}.json"
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    raw = None
                if isinstance(raw, dict) and raw.get("issuer") == AGENT_EGRESS_POLICY_ISSUER:
                    path.unlink(missing_ok=True)
            return None
        ttl = AGENT_EGRESS_POLICY_DEFAULT_TTL_SECONDS
        if expirations:
            remaining = int((min(expirations) - current).total_seconds())
            ttl = max(60, min(AGENT_EGRESS_POLICY_MAX_TTL_SECONDS, remaining))
        try:
            policy = derive_egress_policy_for_agent(
                workspace_id=self.workspace_id,
                allowed_hosts=hosts,
                ttl_seconds=ttl,
                allow_all_public=allow_all,
                now=current,
            )
            store_workspace_policy_file(policy_dir, policy)
        except EgressPolicyInvalid as exc:
            logger.warning(
                "agent egress policy derivation failed for workspace %s: %s",
                self.workspace_id,
                exc.reason,
            )
            return baseline
        return policy

    def _upsert_workspace_grant(
        self,
        *,
        request: EgressAuthorizationRequest,
        granted_by: str,
        now: datetime,
    ) -> None:
        """Persist (or re-grant) a workspace-scoped ``agent_egress`` host grant.

        ``allow_always`` writes the *workspace* baseline for capability
        ``agent_egress`` only; it never touches ``web_fetch.policy`` or
        ``UserWebFetchPolicy``. Upserting keeps one row per
        (workspace, capability, subject, hostname) so a revoke -> re-approval
        cycle is idempotent (design doc §4.2).
        """
        grant = self.db.scalar(
            select(HostAuthorizationGrant).where(
                HostAuthorizationGrant.workspace_id == self.workspace_id,
                HostAuthorizationGrant.capability == request.capability,
                HostAuthorizationGrant.subject_type == "workspace",
                HostAuthorizationGrant.subject_id == self.workspace_id,
                HostAuthorizationGrant.hostname == request.hostname,
            )
        )
        if grant is None:
            grant = HostAuthorizationGrant(
                workspace_id=self.workspace_id,
                capability=request.capability,
                subject_type="workspace",
                subject_id=self.workspace_id,
                hostname=request.hostname,
                ports=[443],
                protocols=["https"],
                source_request_id=request.id,
                granted_by=granted_by,
                granted_at=now,
            )
            self.db.add(grant)
        else:
            grant.revoked_at = None
            grant.revoked_by = None
            grant.source_request_id = request.id
            grant.granted_by = granted_by
            grant.granted_at = now
        self.db.flush()

    # -- queue operations -----------------------------------------------------

    def create_request(
        self,
        *,
        hostname: str,
        requested_by: str,
        chat_session_id: str | None = None,
        purpose: str | None = None,
        request_context: dict[str, Any] | None = None,
        ttl_seconds: int = EGRESS_APPROVAL_DEFAULT_TTL_SECONDS,
        dedupe_key: str | None = None,
        now: datetime | None = None,
        assistant_message_id: str | None = None,
        user_message_id: str | None = None,
        tool_call_id: str | None = None,
        resume_payload: dict[str, Any] | None = None,
    ) -> EgressAuthorizationRequest:
        """Create (or return the existing) pending approval request.

        Only a canonical exact hostname is accepted (contract A): IP literals,
        URL forms and wildcards are rejected by ``normalize_hostname``. The
        request is idempotent per ``(workspace_id, hostname, source)`` for
        still-pending rows, so repeated tool calls do not duplicate cards.
        Pending never blocks the caller: it is a durable suspension, expired
        asynchronously once ``expires_at`` passes.
        """
        try:
            canonical = normalize_hostname(hostname)
        except EgressPolicyInvalid as exc:
            raise AppError(
                422,
                "egress_authorization_hostname_invalid",
                f"Approval hostname must be a canonical public DNS name: {exc.reason}",
            ) from exc
        if (
            not isinstance(ttl_seconds, int)
            or isinstance(ttl_seconds, bool)
            or not 0 < ttl_seconds <= EGRESS_APPROVAL_MAX_TTL_SECONDS
        ):
            ttl_seconds = EGRESS_APPROVAL_DEFAULT_TTL_SECONDS
        source = dedupe_key if dedupe_key is not None else (chat_session_id or "")
        current = now or _utc_now()
        existing = self.db.scalar(
            select(EgressAuthorizationRequest).where(
                EgressAuthorizationRequest.workspace_id == self.workspace_id,
                EgressAuthorizationRequest.capability == self.capability,
                EgressAuthorizationRequest.hostname == canonical,
                EgressAuthorizationRequest.dedupe_key == source,
                EgressAuthorizationRequest.status == "pending",
            )
        )
        if existing is not None:
            # Back-fill correlation fields on a reused pending request so a
            # repeated tool call still links the card to this assistant turn.
            if assistant_message_id and not existing.assistant_message_id:
                existing.assistant_message_id = assistant_message_id
            if user_message_id and not existing.user_message_id:
                existing.user_message_id = user_message_id
            if tool_call_id and not existing.tool_call_id:
                existing.tool_call_id = tool_call_id
            if resume_payload and not existing.resume_payload:
                existing.resume_payload = resume_payload
            self.db.commit()
            self.db.refresh(existing)
            return existing

        if self._auto_allowable(canonical):
            # Unified whitelist (or allow-all mode): the host is pre-approved
            # and never enters the pending queue — no interception.
            return self._auto_approve(
                hostname=canonical,
                requested_by=requested_by,
                chat_session_id=chat_session_id,
                purpose=purpose,
                request_context=request_context,
                ttl_seconds=ttl_seconds,
                dedupe_key=source,
                now=current,
                assistant_message_id=assistant_message_id,
                user_message_id=user_message_id,
                tool_call_id=tool_call_id,
                resume_payload=resume_payload,
            )

        context = dict(request_context or {})
        if purpose:
            context.setdefault("purpose", purpose)
        request = EgressAuthorizationRequest(
            workspace_id=self.workspace_id,
            hostname=canonical,
            capability=self.capability,
            requested_by=requested_by,
            chat_session_id=chat_session_id,
            request_context=context or None,
            status="pending",
            decision=None,
            allow_always=False,
            expires_at=current + timedelta(seconds=ttl_seconds),
            ttl_seconds=ttl_seconds,
            dedupe_key=source,
            assistant_message_id=assistant_message_id,
            user_message_id=user_message_id,
            tool_call_id=tool_call_id,
            resume_payload=resume_payload,
        )
        self.db.add(request)
        self.db.flush()
        self.audit.record(
            actor_id=requested_by,
            action="agent_egress.authorization_requested",
            resource_type="egress_authorization_request",
            resource_id=request.id,
            outcome="pending",
            details={"hostname": canonical, "capability": self.capability},
        )
        self.db.commit()
        self.db.refresh(request)
        return request

    def _auto_allowable(self, hostname: str) -> bool:
        """Whether ``hostname`` bypasses the approval queue under the unified allowlist."""
        from app.providers.factory import access_allow_all, access_allowlist_domains

        return access_allow_all(
            self.db, self.workspace_id
        ) or hostname in access_allowlist_domains(self.db, self.workspace_id)

    def _auto_approve(
        self,
        *,
        hostname: str,
        requested_by: str,
        chat_session_id: str | None,
        purpose: str | None,
        request_context: dict[str, Any] | None,
        ttl_seconds: int,
        dedupe_key: str,
        now: datetime,
        assistant_message_id: str | None,
        user_message_id: str | None,
        tool_call_id: str | None,
        resume_payload: dict[str, Any] | None,
    ) -> EgressAuthorizationRequest:
        """Record an auto-approved request for a unified-allowlist host.

        The host is already whitelisted (or the workspace opted into
        no-interception mode), so the request is created approved with an
        ``allow_always`` workspace grant instead of entering the pending queue.
        The grant is authored as ``system:allowlist`` — a policy decision, not a
        user decision — and never bypasses the egress proxy classifier.
        """
        context = dict(request_context or {})
        if purpose:
            context.setdefault("purpose", purpose)
        request = EgressAuthorizationRequest(
            workspace_id=self.workspace_id,
            hostname=hostname,
            capability=self.capability,
            requested_by=requested_by,
            chat_session_id=chat_session_id,
            request_context=context or None,
            status="approved",
            decision="allow_always",
            allow_always=True,
            decided_by="system:allowlist",
            decided_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            ttl_seconds=ttl_seconds,
            dedupe_key=dedupe_key,
            assistant_message_id=assistant_message_id,
            user_message_id=user_message_id,
            tool_call_id=tool_call_id,
            resume_payload=resume_payload,
        )
        self.db.add(request)
        self.db.flush()
        self._upsert_workspace_grant(
            request=request, granted_by="system:allowlist", now=now
        )
        from app.providers.factory import access_allow_all

        self.audit.record(
            actor_id=requested_by,
            action="agent_egress.authorization_auto_approved",
            resource_type="egress_authorization_request",
            resource_id=request.id,
            outcome="approved",
            details={
                "hostname": hostname,
                "capability": self.capability,
                "reason": "unified_allowlist" if not access_allow_all(self.db, self.workspace_id) else "allow_all",
            },
        )
        self.db.commit()
        self.db.refresh(request)
        if self.capability == EGRESS_APPROVAL_CAPABILITY:
            try:
                self.ensure_agent_egress_policy(now=now)
            except Exception:
                logger.exception(
                    "agent egress policy refresh failed after auto-approval for workspace %s",
                    self.workspace_id,
                )
        return request

    def decide(
        self,
        *,
        request_id: str,
        decision: str,
        actor_id: str,
        is_manager: bool = False,
        now: datetime | None = None,
    ) -> EgressAuthorizationRequest:
        """Record a user decision on a pending request.

        Visibility/authority (design doc §6.3): only the requesting user, or a
        workspace manager deciding on their behalf, may decide. ``allow_always``
        additionally requires ``workspace.manage`` and persists the host into
        the workspace ``agent_egress`` allowlist. A request past its pending
        deadline is expired, not decided. Re-deciding a terminal request is
        idempotent and returns the existing row.
        """
        request = self._load(request_id)
        if request.status != "pending":
            return request
        if decision not in ALLOWED_DECISIONS:
            raise AppError(
                422,
                "egress_authorization_decision_invalid",
                "decision must be one of: allow_once, allow_always, deny",
            )
        if actor_id != request.requested_by and not is_manager:
            raise AppError(
                403,
                "egress_authorization_not_decider",
                "Only the requesting user or a workspace manager may decide this request",
            )
        if decision == "allow_always" and not is_manager:
            raise AppError(
                403,
                "egress_authorization_require_manage",
                "allow_always requires the workspace.manage permission",
            )
        current = now or _utc_now()
        if _as_utc(request.expires_at) <= current:
            # Stale pending request: expired, not decided (async deadline).
            request.status = "expired"
            self.db.commit()
            self.db.refresh(request)
            return request

        request.decision = decision
        request.allow_always = decision == "allow_always"
        request.status = "approved" if decision != "deny" else "denied"
        request.decided_by = actor_id
        request.decided_at = current
        if decision == "allow_always":
            # Only a hostname is added to the allowlist (contract B): no IP,
            # CIDR, ``allow_private`` or classifier exception is ever written.
            self._upsert_workspace_grant(request=request, granted_by=actor_id, now=current)
        self.audit.record(
            actor_id=actor_id,
            action="agent_egress.authorization_decided",
            resource_type="egress_authorization_request",
            resource_id=request.id,
            outcome=request.status,
            details={
                "decision": decision,
                "hostname": request.hostname,
                "allow_always": request.allow_always,
            },
        )
        self.db.commit()
        self.db.refresh(request)
        if self.capability == EGRESS_APPROVAL_CAPABILITY:
            try:
                self.ensure_agent_egress_policy(now=current)
            except Exception:
                logger.exception(
                    "agent egress policy refresh failed after decision for workspace %s",
                    self.workspace_id,
                )
        return request

    def list_requests(
        self,
        *,
        actor_id: str,
        is_manager: bool = False,
        status: str | None = None,
        requested_by: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[EgressAuthorizationRequest], int]:
        """List workspace-visible approval requests (paged, status-filterable).

        Ordinary members only see their own requests; workspace managers see
        the whole workspace queue. Pending requests past ``expires_at`` are
        expired opportunistically so the UI never shows a stale card as
        actionable.
        """
        self.expire_stale()
        query = select(EgressAuthorizationRequest).where(
            EgressAuthorizationRequest.workspace_id == self.workspace_id
        )
        if not is_manager:
            query = query.where(EgressAuthorizationRequest.requested_by == actor_id)
        if requested_by:
            query = query.where(EgressAuthorizationRequest.requested_by == requested_by)
        if status:
            query = query.where(EgressAuthorizationRequest.status == status)
        total = int(
            self.db.scalar(
                select(func.count()).select_from(query.subquery())
            )
            or 0
        )
        rows = self.db.scalars(
            query.order_by(
                EgressAuthorizationRequest.created_at.desc(),
                EgressAuthorizationRequest.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return list(rows), total

    def expire_stale(self, *, now: datetime | None = None) -> int:
        """Flip pending requests past their deadline to ``expired``.

        Called opportunistically by the list/decision paths and intended to be
        invoked by a background sweep as well. A stale pending request never
        blocks a caller; it just stops being actionable. Idempotent.
        """
        current = now or _utc_now()
        result = self.db.execute(
            update(EgressAuthorizationRequest)
            .where(
                EgressAuthorizationRequest.workspace_id == self.workspace_id,
                EgressAuthorizationRequest.status == "pending",
                EgressAuthorizationRequest.expires_at <= current,
            )
            .values(status="expired")
            # The bulk UPDATE must not re-evaluate the WHERE predicate against
            # already-loaded ORM objects (naive-vs-aware datetime) or block on
            # stale identity-map rows; subsequent reads re-select fresh state.
            .execution_options(synchronize_session=False)
        )
        self.db.commit()
        return result.rowcount or 0

    def claim_once(
        self,
        *,
        request_id: str,
        actor_id: str,
        now: datetime | None = None,
    ) -> EgressAuthorizationRequest | None:
        """Atomically claim a single-use lease before its host may be used.

        The conditional UPDATE ``status='approved' AND decision='allow_once'
        AND consumed_at IS NULL`` is the concurrency gate: exactly one caller
        wins for one execution. The loser gets ``None`` and must not proceed.
        On success the row is marked ``claimed`` with the actor id; the caller
        finishes with ``consume_once`` (success) or ``release_once`` (failure).
        """
        current = now or _utc_now()
        result = self.db.execute(
            update(EgressAuthorizationRequest)
            .where(
                EgressAuthorizationRequest.id == request_id,
                EgressAuthorizationRequest.workspace_id == self.workspace_id,
                EgressAuthorizationRequest.status == "approved",
                EgressAuthorizationRequest.decision == "allow_once",
                EgressAuthorizationRequest.consumed_at.is_(None),
                EgressAuthorizationRequest.expires_at > current,
            )
            .values(status="claimed", claimed_by=actor_id, consumed_at=current)
            .execution_options(synchronize_session=False)
        )
        self.db.commit()
        if result.rowcount != 1:
            request = self._load(request_id)
            if request.status == "approved" and _as_utc(request.expires_at) <= current:
                request.status = "expired"
                self.db.commit()
                self.db.refresh(request)
            return None
        request = self._load(request_id)
        self.audit.record(
            actor_id=actor_id,
            action="agent_egress.authorization_claimed",
            resource_type="egress_authorization_request",
            resource_id=request.id,
            outcome="claimed",
            details={"hostname": request.hostname, "decision": "allow_once"},
        )
        self.db.commit()
        self.db.refresh(request)
        return request

    def release_once(
        self,
        *,
        request_id: str,
        now: datetime | None = None,
    ) -> EgressAuthorizationRequest:
        """Release a claimed lease back to ``approved`` after a failed execution."""
        request = self._load(request_id)
        if request.status == "claimed" and request.decision == "allow_once":
            request.status = "approved"
            request.claimed_by = None
            request.consumed_at = None
            self.audit.record(
                actor_id=request.requested_by,
                action="agent_egress.authorization_released",
                resource_type="egress_authorization_request",
                resource_id=request.id,
                outcome="approved",
                details={"hostname": request.hostname, "decision": "allow_once"},
            )
            self.db.commit()
            self.db.refresh(request)
        return request

    def consume_once(
        self,
        *,
        request_id: str,
        now: datetime | None = None,
    ) -> EgressAuthorizationRequest:
        """Consume an ``approved`` allow-once request after its host was used to
        derive a policy (T4.1 hook).

        ``allow_once`` is a single-use lease; once a derived policy consumed it,
        this marks it ``consumed`` so it cannot be reused. Idempotent and a
        no-op for ``allow_always`` (persistent grants are not leases). A
        ``claimed`` row is finalized here; unclaimed approvals are also consumed
        for callers that do not use the atomic claim path.
        """
        request = self._load(request_id)
        if request.decision != "allow_once":
            return request
        if request.status == "consumed":
            return request
        request.status = "consumed"
        request.consumed_at = request.consumed_at or now or _utc_now()
        self.audit.record(
            actor_id=request.requested_by,
            action="agent_egress.authorization_consumed",
            resource_type="egress_authorization_request",
            resource_id=request.id,
            outcome="consumed",
            details={"hostname": request.hostname, "decision": "allow_once"},
        )
        self.db.commit()
        self.db.refresh(request)
        if self.capability == EGRESS_APPROVAL_CAPABILITY:
            try:
                self.ensure_agent_egress_policy()
            except Exception:
                logger.exception(
                    "agent egress policy refresh failed after consume for workspace %s",
                    self.workspace_id,
                )
        return request

    # -- allowlist resolution (T4.1 / Phase 2 consumption point) --------------

    def effective_allowed_hosts(self, *, actor_id: str | None = None) -> frozenset[str]:
        """Resolve the effective exact-host set for one actor's Agent egress.

        ``workspace baseline ∪ the actor's personal agent_egress grants`` for
        capability ``agent_egress`` only. Never unions ``web_fetch.policy`` or
        ``UserWebFetchPolicy`` (design doc §3.2). This is the host set a future
        ``derive_egress_policy_for_agent()`` consumes; it does not itself create
        an ``EgressPolicy`` or bypass ``authorize_connect()``.
        """
        grants = self.db.scalars(
            select(HostAuthorizationGrant).where(
                HostAuthorizationGrant.workspace_id == self.workspace_id,
                HostAuthorizationGrant.capability == self.capability,
                HostAuthorizationGrant.revoked_at.is_(None),
            )
        ).all()
        hosts = {
            grant.hostname
            for grant in grants
            if grant.subject_type == "workspace"
            or (
                grant.subject_type == "user"
                and actor_id is not None
                and grant.subject_id == actor_id
            )
        }
        return frozenset(hosts)
