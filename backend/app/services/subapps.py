from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import hmac
import json
import secrets
from typing import Any

from jsonschema import SchemaError, ValidationError
from jsonschema.validators import validator_for
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import (
    ComponentManifestVersion,
    SubAppInteractionEvent,
    SubAppSession,
    SubAppState,
    utc_now,
)
from app.domain.schemas.subapps import (
    SubAppEventIngestRequest,
    SubAppEventListView,
    SubAppInteractionEventView,
    SubAppSessionCreateRequest,
    SubAppSessionCreatedView,
    SubAppSessionEventAcceptedView,
    SubAppSessionEventRequest,
    SubAppSessionView,
    SubAppStateListView,
    SubAppStateView,
)
from app.repositories.audit import AuditRepository
from app.services.component_renderer_protocol import (
    MAX_SUBAPP_EVENTS_PER_SESSION,
    MAX_SUBAPP_STATE_RATE_PER_MINUTE,
    MAX_SUBAPP_STATES_PER_SESSION,
    RENDERER_UNLOCK_EVENT,
    build_renderer_message,
    validate_renderer_state_payload,
)


MAX_SUBAPP_EVENT_PAYLOAD_BYTES = 16 * 1024
MAX_SUBAPP_EVENT_LIST_LIMIT = 100
MAX_SUBAPP_STATE_LIST_LIMIT = 100


def _canonical_json(value: dict[str, Any]) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise AppError(
            422,
            "subapp_event_payload_invalid",
            "Event payload must be finite JSON data",
        ) from exc


def _payload_json_and_hash(payload: dict[str, Any]) -> tuple[str, str]:
    canonical_payload = _canonical_json(payload)
    encoded = canonical_payload.encode("utf-8")
    if len(encoded) > MAX_SUBAPP_EVENT_PAYLOAD_BYTES:
        raise AppError(
            422,
            "subapp_event_payload_too_large",
            "Event payload exceeds the 16384-byte limit",
            {"max_bytes": MAX_SUBAPP_EVENT_PAYLOAD_BYTES},
        )
    return canonical_payload, hashlib.sha256(encoded).hexdigest()


def _event_view(event: SubAppInteractionEvent) -> SubAppInteractionEventView:
    # payload_json is written canonically by this service. Treat unexpected legacy
    # null values as an empty object rather than exposing an invalid response.
    payload = json.loads(event.payload_json) if event.payload_json is not None else {}
    return SubAppInteractionEventView(
        id=event.id,
        workspace_id=event.workspace_id,
        session_id=event.session_id,
        actor_id=event.actor_id,
        chat_session_id=event.chat_session_id,
        artifact_version_id=event.artifact_version_id,
        event_type=event.event_type,
        payload=payload,
        payload_sha256=event.payload_sha256 or "",
        client_event_id=event.client_event_id,
        sequence=event.sequence,
        schema_version=event.schema_version,
        occurred_at=event.occurred_at,
        bundle_id=event.bundle_id,
        component_id=event.component_id,
        component_version=event.component_version,
        source=event.source,
        privacy_class=event.privacy_class,
        created_at=event.created_at,
    )


def _token_sha256(token: str) -> str:
    """Digest a session capability token secret (never canonical-JSON encoded)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _state_sha256(state: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(state).encode("utf-8")).hexdigest()


def _validate_against_schema(
    value: Any,
    schema: dict[str, Any],
    *,
    error_code: str,
    label: str,
) -> None:
    try:
        validator_for(schema).check_schema(schema)
        validator_for(schema)(schema).validate(value)
    except (ValidationError, SchemaError):
        raise AppError(
            422,
            error_code,
            f"{label} failed the session's contract schema",
        ) from None


def _session_view(session: SubAppSession) -> SubAppSessionView:
    return SubAppSessionView(
        id=session.id,
        workspace_id=session.workspace_id,
        actor_id=session.actor_id,
        chat_session_id=session.chat_session_id,
        artifact_version_id=session.artifact_version_id,
        event_schema=session.event_schema,
        state_schema=session.state_schema,
        status=session.status,
        state_version=session.state_version,
        state_sha256=session.state_sha256,
        terminated_at=session.terminated_at,
        created_at=session.created_at,
        updated_at=session.updated_at,
        agent_triggers=session.agent_triggers or [],
        analytics=session.analytics or None,
        agent_status=session.agent_status,
        agent_job_id=session.agent_job_id,
        agent_error=session.agent_error,
        agent_updated_at=session.agent_updated_at,
        last_processed_event_id=session.last_processed_event_id,
        agent_consent=session.agent_consent,
    )


def _state_view(state: SubAppState) -> SubAppStateView:
    # state_json is a SQLAlchemy JSON column; tolerate a legacy null as {}.
    raw = state.state_json
    return SubAppStateView(
        id=state.id,
        session_id=state.session_id,
        version=state.version,
        sha256=state.sha256,
        state=raw if isinstance(raw, dict) else {},
        created_at=state.created_at,
    )


class SubAppService:
    """Persist workspace-scoped sub-application events and sessions.

    P1 stores legacy workspace-scoped host-relayed events only when they are
    not bound to a session. T2.4 provides instantiated sessions guarded by a
    rotating session-level capability token and immutable, CAS-versioned state
    snapshots; session-scoped events must redeem the current token. The agent
    may only write state through :meth:`propose_state`; iframes never receive
    host credentials.
    """

    def __init__(self, db: Session, workspace_id: str, actor_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.audit = AuditRepository(db, workspace_id)

    def ingest(self, payload: SubAppEventIngestRequest) -> SubAppInteractionEventView:
        if payload.session_id is not None:
            raise AppError(
                400,
                "subapp_session_requires_token",
                "Session-scoped subapp events must use "
                "POST /subapps/sessions/{session_id}/events with the rotating "
                "session capability token",
            )
        payload_json, payload_sha256 = _payload_json_and_hash(payload.payload)
        event = SubAppInteractionEvent(
            workspace_id=self.workspace_id,
            session_id=payload.session_id,
            actor_id=self.actor_id,
            chat_session_id=payload.chat_session_id,
            artifact_version_id=payload.artifact_version_id,
            event_type=payload.event_type,
            payload_json=payload_json,
            payload_sha256=payload_sha256,
        )
        self.db.add(event)
        self.db.flush()
        self.audit.record(
            actor_id=self.actor_id,
            action="subapp.event_ingested",
            resource_type="subapp_interaction_event",
            resource_id=event.id,
            details={
                "event_type": event.event_type,
                "session_id": event.session_id,
                "payload_sha256": payload_sha256,
            },
        )
        self.db.commit()
        self.db.refresh(event)
        return _event_view(event)

    def list_events(
        self,
        *,
        session_id: str | None,
        event_type: str | None,
        created_after: datetime | None,
        created_before: datetime | None,
        offset: int,
        limit: int,
    ) -> SubAppEventListView:
        query = select(SubAppInteractionEvent).where(
            SubAppInteractionEvent.workspace_id == self.workspace_id
        )
        count_query = select(func.count()).select_from(SubAppInteractionEvent).where(
            SubAppInteractionEvent.workspace_id == self.workspace_id
        )
        if session_id is not None:
            condition = SubAppInteractionEvent.session_id == session_id
            query = query.where(condition)
            count_query = count_query.where(condition)
        if event_type is not None:
            condition = SubAppInteractionEvent.event_type == event_type
            query = query.where(condition)
            count_query = count_query.where(condition)
        if created_after is not None:
            condition = SubAppInteractionEvent.created_at >= created_after
            query = query.where(condition)
            count_query = count_query.where(condition)
        if created_before is not None:
            condition = SubAppInteractionEvent.created_at <= created_before
            query = query.where(condition)
            count_query = count_query.where(condition)
        total = self.db.scalar(count_query) or 0
        events = self.db.scalars(
            query.order_by(
                SubAppInteractionEvent.created_at.desc(),
                SubAppInteractionEvent.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
        return SubAppEventListView(
            items=[_event_view(event) for event in events],
            offset=offset,
            limit=limit,
            total=total,
        )

    # ------------------------------------------------------------------ #
    # T2.4 sessions
    # ------------------------------------------------------------------ #

    def _get_session(self, session_id: str) -> SubAppSession:
        session = self.db.scalar(
            select(SubAppSession).where(
                SubAppSession.id == session_id,
                SubAppSession.workspace_id == self.workspace_id,
            )
        )
        if session is None:
            raise AppError(404, "subapp_session_not_found", "Sub-application session not found")
        return session

    def create_session(self, payload: SubAppSessionCreateRequest) -> SubAppSessionCreatedView:
        """Instantiate a published sub-application version as a live session.

        The interaction contract (``event_schema``/``state_schema``) is snapshotted
        from the workspace-scoped manifest version so later version edits cannot
        change an already-instantiated session. The raw capability token is
        returned exactly once; only its SHA-256 digest and an 8-char prefix are
        persisted.
        """
        manifest = self.db.scalar(
            select(ComponentManifestVersion).where(
                ComponentManifestVersion.id == payload.artifact_version_id,
                ComponentManifestVersion.workspace_id == self.workspace_id,
            )
        )
        if manifest is None:
            raise AppError(
                404,
                "subapp_artifact_not_found",
                "Sub-application artifact version not found in this workspace",
            )
        contract = manifest.interaction_contract or {}
        event_schema = contract.get("event_schema")
        state_schema = contract.get("state_schema")
        if not isinstance(event_schema, dict) or not isinstance(state_schema, dict):
            raise AppError(
                422,
                "subapp_contract_missing",
                "Artifact version has no complete interaction contract",
            )
        # Contract schemas become immutable session snapshots; require them to
        # be well-formed JSON Schemas before persisting.
        try:
            validator_for(event_schema).check_schema(event_schema)
            validator_for(state_schema).check_schema(state_schema)
        except (ValidationError, SchemaError):
            raise AppError(
                422,
                "subapp_contract_invalid",
                "Interaction contract contains an invalid JSON Schema",
            ) from None

        raw_token = secrets.token_urlsafe(32)
        token_hash = _token_sha256(raw_token)
        token_prefix = raw_token[:8]
        session = SubAppSession(
            workspace_id=self.workspace_id,
            actor_id=self.actor_id,
            chat_session_id=payload.chat_session_id,
            artifact_version_id=payload.artifact_version_id,
            event_schema=event_schema,
            state_schema=state_schema,
            agent_triggers=contract.get("agent_triggers", []) or [],
            analytics=contract.get("analytics") or None,
            status="active",
            current_token_hash=token_hash,
            current_token_prefix=token_prefix,
            state_version=0,
            state_sha256=None,
            terminated_at=None,
        )
        self.db.add(session)
        self.db.flush()
        render_ref = session.id
        unlock = build_renderer_message(
            event_type=RENDERER_UNLOCK_EVENT,
            payload={
                "token": raw_token,
                "component_id": manifest.component_id,
                "render_ref": render_ref,
            },
            token=raw_token,
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="subapp.session_created",
            resource_type="subapp_session",
            resource_id=session.id,
            details={
                "artifact_version_id": payload.artifact_version_id,
                "chat_session_id": payload.chat_session_id,
                "token_prefix": token_prefix,
            },
        )
        self.db.commit()
        self.db.refresh(session)
        return SubAppSessionCreatedView(
            session_id=session.id,
            status=session.status,
            state_version=session.state_version,
            state_sha256=session.state_sha256,
            token=raw_token,
            token_prefix=token_prefix,
            component_id=manifest.component_id,
            render_ref=render_ref,
            artifact_version_id=session.artifact_version_id,
            chat_session_id=session.chat_session_id,
            event_schema=session.event_schema,
            state_schema=session.state_schema,
            unlock_message=unlock,
        )

    def get_session(self, session_id: str) -> SubAppSessionView:
        return _session_view(self._get_session(session_id))

    def accept_session_event(
        self,
        session_id: str,
        payload: SubAppSessionEventRequest,
    ) -> SubAppSessionEventAcceptedView:
        """Redeem the current capability token and persist one user event.

        The token is constant-time compared against the persisted digest and is
        rotated on success, so a replayed old token is rejected on the next
        attempt. No agent work is triggered here (202); the host polls state.
        """
        session = self._get_session(session_id)
        if session.status != "active":
            raise AppError(
                409,
                "subapp_session_not_active",
                "Sub-application session is not accepting events",
            )
        if not session.current_token_hash or not hmac.compare_digest(
            _token_sha256(payload.token),
            session.current_token_hash,
        ):
            raise AppError(
                401,
                "subapp_token_invalid",
                "Current session capability token is missing, stale, or invalid",
            )

        payload_json, payload_sha256 = _payload_json_and_hash(payload.payload)
        _validate_against_schema(
            payload.payload,
            session.event_schema,
            error_code="subapp_event_schema_rejected",
            label="Event payload",
        )

        event_count = self.db.scalar(
            select(func.count())
            .select_from(SubAppInteractionEvent)
            .where(
                SubAppInteractionEvent.workspace_id == self.workspace_id,
                SubAppInteractionEvent.session_id == session.id,
            )
        ) or 0
        if event_count >= MAX_SUBAPP_EVENTS_PER_SESSION:
            raise AppError(
                429,
                "subapp_event_budget_exceeded",
                "Session event budget exhausted",
                {"max": MAX_SUBAPP_EVENTS_PER_SESSION},
            )

        # Rotate the capability token: the presented token is spent immediately
        # and a fresh one is issued so old-token replays cannot succeed.
        next_token = secrets.token_urlsafe(32)
        next_hash = _token_sha256(next_token)
        next_prefix = next_token[:8]
        event = SubAppInteractionEvent(
            workspace_id=self.workspace_id,
            session_id=session.id,
            actor_id=self.actor_id,
            chat_session_id=session.chat_session_id,
            artifact_version_id=session.artifact_version_id,
            event_type=payload.event_type,
            payload_json=payload_json,
            payload_sha256=payload_sha256,
        )
        self.db.add(event)
        session.current_token_hash = next_hash
        session.current_token_prefix = next_prefix
        self.db.flush()
        self.audit.record(
            actor_id=self.actor_id,
            action="subapp.session_event_accepted",
            resource_type="subapp_session",
            resource_id=session.id,
            details={
                "event_type": payload.event_type,
                "event_id": event.id,
                "token_prefix": next_prefix,
            },
        )
        self.db.commit()
        self.db.refresh(event)
        from app.services.subapp_event_agent import (
            maybe_enqueue_subapp_event_agent,
        )

        agent_info = maybe_enqueue_subapp_event_agent(
            session_id=session.id,
            event_id=event.id,
            workspace_id=self.workspace_id,
            actor_id=self.actor_id,
            db=self.db,
        )
        return SubAppSessionEventAcceptedView(
            session_id=session.id,
            event=_event_view(event),
            next_token=next_token,
            next_token_prefix=next_prefix,
            agent=agent_info,
        )

    def list_states(
        self,
        session_id: str,
        *,
        offset: int,
        limit: int,
    ) -> SubAppStateListView:
        session = self._get_session(session_id)
        base = [
            SubAppState.session_id == session.id,
            SubAppState.workspace_id == self.workspace_id,
        ]
        total = self.db.scalar(
            select(func.count()).select_from(SubAppState).where(*base)
        ) or 0
        states = self.db.scalars(
            select(SubAppState)
            .where(*base)
            .order_by(SubAppState.version.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return SubAppStateListView(
            items=[_state_view(state) for state in states],
            offset=offset,
            limit=limit,
            total=total,
        )

    def get_state(self, session_id: str, version: int) -> SubAppStateView:
        session = self._get_session(session_id)
        state = self.db.scalar(
            select(SubAppState).where(
                SubAppState.session_id == session.id,
                SubAppState.workspace_id == self.workspace_id,
                SubAppState.version == version,
            )
        )
        if state is None:
            raise AppError(
                404,
                "subapp_state_not_found",
                "Sub-application state version not found",
            )
        return _state_view(state)

    def pause_session(self, session_id: str) -> SubAppSessionView:
        session = self._get_session(session_id)
        if session.status not in {"active", "paused"}:
            raise AppError(
                409,
                "subapp_session_not_pausable",
                "Sub-application session cannot be paused in its current status",
            )
        session.status = "paused"
        self.db.flush()
        self.audit.record(
            actor_id=self.actor_id,
            action="subapp.session_paused",
            resource_type="subapp_session",
            resource_id=session.id,
        )
        self.db.commit()
        self.db.refresh(session)
        return _session_view(session)

    def terminate_session(self, session_id: str) -> SubAppSessionView:
        session = self._get_session(session_id)
        if session.status != "terminated":
            session.status = "terminated"
            session.terminated_at = utc_now()
            # Clear the capability so no further event can be redeemed.
            session.current_token_hash = ""
            session.current_token_prefix = ""
            self.db.flush()
            self.audit.record(
                actor_id=self.actor_id,
                action="subapp.session_terminated",
                resource_type="subapp_session",
                resource_id=session.id,
            )
            self.db.commit()
            self.db.refresh(session)
        return _session_view(session)

    def propose_state(
        self,
        session_id: str,
        state: dict[str, Any],
        *,
        expected_version: int,
    ) -> int:
        """Host->iframe state push: validate, CAS-version, and snapshot one state.

        This is the only server-side write path for sub-application state (the
        agent tool calls this method). ``expected_version`` is the optimistic
        lock: the session advances only if its current ``state_version`` equals
        the caller's expectation, otherwise a 409 ``subapp_state_version_conflict``
        is raised. Returns the newly written version number.
        """
        session = self._get_session(session_id)
        if session.status != "active":
            raise AppError(
                409,
                "subapp_session_not_active",
                "Sub-application session is not accepting state pushes",
            )
        if session.state_version != expected_version:
            raise AppError(
                409,
                "subapp_state_version_conflict",
                "Sub-application state version moved; retry with the current version",
                {"current": session.state_version, "expected": expected_version},
            )
        recent_state_count = self.db.scalar(
            select(func.count())
            .select_from(SubAppState)
            .where(
                SubAppState.session_id == session.id,
                SubAppState.workspace_id == self.workspace_id,
                SubAppState.created_at >= utc_now() - timedelta(minutes=1),
            )
        ) or 0
        if recent_state_count >= MAX_SUBAPP_STATE_RATE_PER_MINUTE:
            raise AppError(
                429,
                "subapp_state_rate_exceeded",
                "Sub-application state write rate exceeded",
                {
                    "max_per_minute": MAX_SUBAPP_STATE_RATE_PER_MINUTE,
                    "current_per_minute": recent_state_count,
                },
            )
        new_version = expected_version + 1
        sha256 = _state_sha256(state)
        message_payload = {
            "state_version": new_version,
            "state_sha256": sha256,
            "state": state,
        }
        validated = validate_renderer_state_payload(
            message_payload,
            state_schema=session.state_schema,
        )
        if isinstance(validated, str):
            raise AppError(
                422,
                "subapp_state_rejected",
                "State failed protocol or contract validation",
                {"reason": validated},
            )

        state_count = self.db.scalar(
            select(func.count())
            .select_from(SubAppState)
            .where(
                SubAppState.session_id == session.id,
                SubAppState.workspace_id == self.workspace_id,
            )
        ) or 0
        if state_count >= MAX_SUBAPP_STATES_PER_SESSION:
            raise AppError(
                429,
                "subapp_state_budget_exceeded",
                "Session state budget exhausted",
                {"max": MAX_SUBAPP_STATES_PER_SESSION},
            )

        # Optimistic-lock UPDATE: only a row still on the expected version (and
        # still active) advances; rowcount != 1 means a concurrent writer won.
        result = self.db.execute(
            update(SubAppSession)
            .where(
                SubAppSession.id == session.id,
                SubAppSession.workspace_id == self.workspace_id,
                SubAppSession.state_version == expected_version,
                SubAppSession.status == "active",
            )
            .values(state_version=new_version, state_sha256=sha256)
        )
        if result.rowcount != 1:
            raise AppError(
                409,
                "subapp_state_version_conflict",
                "Sub-application state version moved; retry with the current version",
            )
        self.db.flush()

        state_row = SubAppState(
            workspace_id=self.workspace_id,
            session_id=session.id,
            version=new_version,
            sha256=sha256,
            state_json=state,
        )
        self.db.add(state_row)
        self.db.flush()
        self.audit.record(
            actor_id=self.actor_id,
            action="subapp.state_proposed",
            resource_type="subapp_session",
            resource_id=session.id,
            details={"version": new_version, "sha256": sha256},
        )
        self.db.commit()
        return new_version
