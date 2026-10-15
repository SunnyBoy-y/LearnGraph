"""Agent-published bidirectional sub-application (T2.6) — contract validation,
bundle publication, end-to-end session instantiation, and Agent tool routing.

Covers the chain added for "sandbox_publish_web_app interaction_contract":

1. Contract validation rejects unsafe / incomplete schemas before publication.
2. ``SubAppBundleService.publish`` with a valid contract returns
   ``subapp_mode=true`` and an ``artifact_version_id``, persists the contract
   snapshot and the manifest link on the bundle row.
3. That ``artifact_version_id`` drives ``SubAppService.create_session`` to
   instantiate a live interactive session (event/state schema snapshot, token
   rotation, CAS state writes).
4. Without a contract, publication stays a static preview (no manifest).
5. The Agent dispatcher forwards the contract through
   ``sandbox_publish_web_app`` and preserves the artifact data for the frontend
   ``subappSessionTrigger`` instantiate branch.
"""

from __future__ import annotations

import os
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.errors import AppError
from app.domain import models as m
from app.domain.schemas.subapps import (
    SubAppEventIngestRequest,
    SubAppSessionCreateRequest,
    SubAppSessionEventRequest,
)
from app.services.component_renderer_protocol import MAX_SUBAPP_STATE_RATE_PER_MINUTE
from app.services.subapp_bundles import SubAppBundleService
from app.services.subapps import SubAppService
from app.services import subapp_event_agent as sea

WORKSPACE = "ws-subapp-agent"
ACTOR = "user-subapp-agent"
CHAT_SESSION_ID = "chat-subapp-agent"

CLOSED_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["question_id", "selected"],
    "properties": {
        "question_id": {"type": "string"},
        "selected": {"type": "string"},
    },
}
STATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["view", "answers"],
    "properties": {
        "view": {"type": "string"},
        "answers": {"type": "object"},
    },
}
VALID_CONTRACT = {"event_schema": CLOSED_SCHEMA, "state_schema": STATE_SCHEMA}

AGENT_CONTRACT = {
    "event_schema": CLOSED_SCHEMA,
    "state_schema": STATE_SCHEMA,
    "agent_triggers": [
        {
            "event_type": "quiz.submit",
            "max_per_minute": 10,
            "allow_text_reply": False,
        }
    ],
}


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session:
        session.add(
            m.ChatSession(
                workspace_id=WORKSPACE,
                id=CHAT_SESSION_ID,
                title="subapp agent test",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def _settings() -> SimpleNamespace:
    return SimpleNamespace(storage_root=Path(tempfile.mkdtemp(prefix="lg-subapp-agent-")))


def _seed_validation(db: Session, *, entry_path: str = "dist/index.html") -> str:
    manifest_json = {
        "version": 1,
        "entry_path": entry_path,
        "files": [
            {
                "path": entry_path,
                "sha256": "a" * 64,
                "mime_type": "text/html",
                "size_bytes": 120,
            }
        ],
    }
    validation = m.SubAppBundleValidation(
        workspace_id=WORKSPACE,
        id="val-subapp-agent",
        owner_user_id=ACTOR,
        chat_session_id=CHAT_SESSION_ID,
        output_root="dist",
        entry_path=entry_path,
        manifest_json=manifest_json,
        manifest_sha256="b" * 64,
        report={"status": "passed", "files": 1, "bytes": 120, "missing_assets": []},
        status="passed",
        expires_at=m.utc_now() + timedelta(minutes=10),
    )
    db.add(validation)
    db.commit()
    return validation.id


def _publish(
    db: Session,
    *,
    validation_id: str,
    interaction_contract: dict | None = VALID_CONTRACT,
) -> dict:
    return SubAppBundleService(
        db, WORKSPACE, ACTOR, _settings()
    ).publish(
        validation_id=validation_id,
        chat_session_id=CHAT_SESSION_ID,
        sandbox_session_id=None,
        title="行程规划器",
        interaction_contract=interaction_contract,
    )


class TestContractValidation:
    def test_rejects_open_schema(self, db: Session) -> None:
        validation_id = _seed_validation(db)
        contract = {
            "event_schema": {
                "type": "object",
                "properties": {"question_id": {"type": "string"}},
            },
            "state_schema": STATE_SCHEMA,
        }
        with pytest.raises(AppError) as excinfo:
            _publish(db, validation_id=validation_id, interaction_contract=contract)
        assert excinfo.value.code == "subapp_interaction_contract_invalid"

    def test_rejects_executable_field(self, db: Session) -> None:
        validation_id = _seed_validation(db)
        contract = {
            "event_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"dangerouslySetInnerHTML": {"type": "string"}},
            },
            "state_schema": STATE_SCHEMA,
        }
        with pytest.raises(AppError) as excinfo:
            _publish(db, validation_id=validation_id, interaction_contract=contract)
        assert excinfo.value.code == "subapp_interaction_contract_invalid"

    def test_rejects_missing_state_schema(self, db: Session) -> None:
        validation_id = _seed_validation(db)
        with pytest.raises(AppError) as excinfo:
            _publish(
                db,
                validation_id=validation_id,
                interaction_contract={"event_schema": CLOSED_SCHEMA},
            )
        assert excinfo.value.code == "subapp_interaction_contract_incomplete"

    def test_rejects_non_object_contract(self, db: Session) -> None:
        validation_id = _seed_validation(db)
        with pytest.raises(AppError) as excinfo:
            _publish(db, validation_id=validation_id, interaction_contract=[])
        assert excinfo.value.code == "subapp_interaction_contract_invalid"


class TestPublishWithContract:
    def test_publish_returns_subapp_mode_and_manifest(self, db: Session) -> None:
        validation_id = _seed_validation(db)
        result = _publish(db, validation_id=validation_id)
        assert result["subapp_mode"] is True
        assert result["artifact_version_id"] is not None

        bundle = db.scalar(
            select(m.SubAppBundle).where(m.SubAppBundle.validation_id == validation_id)
        )
        assert bundle is not None
        assert bundle.interaction_contract == {
            **VALID_CONTRACT,
            "agent_triggers": [],
            "analytics": None,
        }
        assert bundle.component_manifest_id == result["artifact_version_id"]

        manifest = db.get(m.ComponentManifestVersion, result["artifact_version_id"])
        assert manifest is not None
        assert manifest.interaction_contract == bundle.interaction_contract
        assert manifest.event_schema == CLOSED_SCHEMA
        assert manifest.source == "agent_subapp"
        assert manifest.renderer == "sandbox"

    def test_part_data_carries_instantiate_marker(self, db: Session) -> None:
        validation_id = _seed_validation(db)
        result = _publish(db, validation_id=validation_id)
        part_data = result["part"]["data"]
        assert part_data["subapp_mode"] is True
        assert part_data["artifact_version_id"] == result["artifact_version_id"]
        assert part_data["bundle_id"] == result["bundle_id"]


class TestEndToEndInstantiation:
    def test_create_session_snapshots_contract(self, db: Session) -> None:
        validation_id = _seed_validation(db)
        result = _publish(db, validation_id=validation_id)
        artifact_version_id = result["artifact_version_id"]

        created = SubAppService(db, WORKSPACE, ACTOR).create_session(
            SubAppSessionCreateRequest(
                artifact_version_id=artifact_version_id,
                chat_session_id=CHAT_SESSION_ID,
            )
        )
        assert created.state_version == 0
        assert created.event_schema == CLOSED_SCHEMA
        assert created.state_schema == STATE_SCHEMA
        assert created.component_id.startswith("bundle_")

        session = db.scalar(
            select(m.SubAppSession).where(m.SubAppSession.id == created.session_id)
        )
        assert session is not None
        assert session.artifact_version_id == artifact_version_id
        assert session.event_schema == CLOSED_SCHEMA

    def test_agent_patch_state_updates_renderer(self, db: Session) -> None:
        validation_id = _seed_validation(db)
        result = _publish(db, validation_id=validation_id)
        created = SubAppService(db, WORKSPACE, ACTOR).create_session(
            SubAppSessionCreateRequest(
                artifact_version_id=result["artifact_version_id"],
                chat_session_id=CHAT_SESSION_ID,
            )
        )
        svc = SubAppService(db, WORKSPACE, ACTOR)
        version = svc.propose_state(
            created.session_id,
            {"view": "result", "answers": {"q1": "B"}},
            expected_version=0,
        )
        assert version == 1
        state = svc.get_state(created.session_id, 1)
        assert state.state["view"] == "result"

    def test_schema_invalid_event_rejected(self, db: Session) -> None:
        validation_id = _seed_validation(db)
        result = _publish(db, validation_id=validation_id)
        created = SubAppService(db, WORKSPACE, ACTOR).create_session(
            SubAppSessionCreateRequest(
                artifact_version_id=result["artifact_version_id"],
                chat_session_id=CHAT_SESSION_ID,
            )
        )
        svc = SubAppService(db, WORKSPACE, ACTOR)
        with pytest.raises(AppError) as excinfo:
            svc.propose_state(
                created.session_id,
                {"view": "x"},
                expected_version=0,
            )
        assert excinfo.value.code == "subapp_state_rejected"


class TestDegradationWithoutContract:
    def test_publish_without_contract_stays_static(self, db: Session) -> None:
        validation_id = _seed_validation(db)
        result = _publish(db, validation_id=validation_id, interaction_contract=None)
        assert result["subapp_mode"] is False
        assert result["artifact_version_id"] is None
        assert "subapp_mode" not in result.get("part", {}).get("data", {}) or result["part"]["data"]["subapp_mode"] is False
        bundle = db.scalar(
            select(m.SubAppBundle).where(m.SubAppBundle.validation_id == validation_id)
        )
        assert bundle is not None
        assert bundle.interaction_contract is None
        assert bundle.component_manifest_id is None


class TestSessionTokenEnforcement:
    def _create_session(self, db: Session):
        validation_id = _seed_validation(db)
        result = _publish(db, validation_id=validation_id)
        return SubAppService(db, WORKSPACE, ACTOR).create_session(
            SubAppSessionCreateRequest(
                artifact_version_id=result["artifact_version_id"],
                chat_session_id=CHAT_SESSION_ID,
            )
        )

    def test_accept_event_rotates_token_and_rejects_replay(self, db: Session) -> None:
        created = self._create_session(db)
        svc = SubAppService(db, WORKSPACE, ACTOR)
        accepted = svc.accept_session_event(
            created.session_id,
            SubAppSessionEventRequest(
                token=created.token,
                event_type="quiz.submit",
                payload={"question_id": "q1", "selected": "B"},
            ),
        )
        assert accepted.next_token != created.token
        assert accepted.event.event_type == "quiz.submit"

        with pytest.raises(AppError) as excinfo:
            svc.accept_session_event(
                created.session_id,
                SubAppSessionEventRequest(
                    token=created.token,
                    event_type="quiz.submit",
                    payload={"question_id": "q1", "selected": "B"},
                ),
            )
        assert excinfo.value.code == "subapp_token_invalid"

        accepted2 = svc.accept_session_event(
            created.session_id,
            SubAppSessionEventRequest(
                token=accepted.next_token,
                event_type="quiz.submit",
                payload={"question_id": "q1", "selected": "A"},
            ),
        )
        assert accepted2.next_token != accepted.next_token

    def test_schema_invalid_event_rejected(self, db: Session) -> None:
        created = self._create_session(db)
        with pytest.raises(AppError) as excinfo:
            SubAppService(db, WORKSPACE, ACTOR).accept_session_event(
                created.session_id,
                SubAppSessionEventRequest(
                    token=created.token,
                    event_type="quiz.submit",
                    payload={"bad": 1},
                ),
            )
        assert excinfo.value.code == "subapp_event_schema_rejected"

    def test_p1_ingest_rejects_session_scoped_event(self, db: Session) -> None:
        created = self._create_session(db)
        with pytest.raises(AppError) as excinfo:
            SubAppService(db, WORKSPACE, ACTOR).ingest(
                SubAppEventIngestRequest(
                    session_id=created.session_id,
                    chat_session_id=CHAT_SESSION_ID,
                    artifact_version_id=created.artifact_version_id,
                    event_type="quiz.submit",
                    payload={"question_id": "q1", "selected": "B"},
                )
            )
        assert excinfo.value.code == "subapp_session_requires_token"

    def test_paused_and_terminated_sessions_reject_events(self, db: Session) -> None:
        created = self._create_session(db)
        svc = SubAppService(db, WORKSPACE, ACTOR)
        svc.pause_session(created.session_id)
        with pytest.raises(AppError) as excinfo:
            svc.accept_session_event(
                created.session_id,
                SubAppSessionEventRequest(
                    token=created.token,
                    event_type="quiz.submit",
                    payload={"question_id": "q1", "selected": "B"},
                ),
            )
        assert excinfo.value.code == "subapp_session_not_active"

        svc.terminate_session(created.session_id)
        with pytest.raises(AppError) as excinfo:
            svc.accept_session_event(
                created.session_id,
                SubAppSessionEventRequest(
                    token=created.token,
                    event_type="quiz.submit",
                    payload={"question_id": "q1", "selected": "B"},
                ),
            )
        assert excinfo.value.code == "subapp_session_not_active"

    def test_state_rate_limit_enforced(self, db: Session) -> None:
        created = self._create_session(db)
        svc = SubAppService(db, WORKSPACE, ACTOR)
        for index in range(MAX_SUBAPP_STATE_RATE_PER_MINUTE):
            svc.propose_state(
                created.session_id,
                {"view": f"v{index}", "answers": {}},
                expected_version=index,
            )
        with pytest.raises(AppError) as excinfo:
            svc.propose_state(
                created.session_id,
                {"view": "overflow", "answers": {}},
                expected_version=MAX_SUBAPP_STATE_RATE_PER_MINUTE,
            )
        assert excinfo.value.code == "subapp_state_rate_exceeded"


class TestEventDrivenAgentConsent:
    @pytest.fixture()
    def agent_db(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        with SessionLocal() as session:
            session.add(
                m.ChatSession(
                    workspace_id=WORKSPACE,
                    id=CHAT_SESSION_ID,
                    title="subapp agent event test",
                )
            )
            session.add(
                m.Workspace(
                    id=WORKSPACE,
                    tenant_id="local-tenant",
                    owner_user_id=ACTOR,
                    name="subapp agent workspace",
                )
            )
            session.commit()
            yield session, SessionLocal
        engine.dispose()

    def _seed_agent_session(self, db: Session):
        validation_id = _seed_validation(db)
        result = _publish(db, validation_id=validation_id, interaction_contract=AGENT_CONTRACT)
        return SubAppService(db, WORKSPACE, ACTOR).create_session(
            SubAppSessionCreateRequest(
                artifact_version_id=result["artifact_version_id"],
                chat_session_id=CHAT_SESSION_ID,
            )
        )

    def _settings(self, enabled: bool = True):
        return SimpleNamespace(
            subapp_event_agent_enabled=enabled,
            subapp_event_agent_max_attempts=3,
            subapp_event_agent_idle_seconds=1,
            subapp_event_agent_poll_reconcile=True,
            durable_queue_lease_seconds=30,
            durable_queue_max_attempts=3,
        )

    def test_trigger_declared_without_consent_asks_user(
        self, agent_db, monkeypatch
    ) -> None:
        db, SessionLocal = agent_db
        monkeypatch.setattr(sea, "SessionLocal", SessionLocal)
        monkeypatch.setattr(sea, "get_settings", lambda: self._settings(True))
        created = self._seed_agent_session(db)
        accepted = SubAppService(db, WORKSPACE, ACTOR).accept_session_event(
            created.session_id,
            SubAppSessionEventRequest(
                token=created.token,
                event_type="quiz.submit",
                payload={"question_id": "q1", "selected": "B"},
            ),
        )
        assert accepted.agent["consent_required"] is True
        assert accepted.agent["pending_consent_id"]
        pending = db.scalar(
            select(m.SubAppAgentConsentRequest).where(
                m.SubAppAgentConsentRequest.event_id == accepted.event.id
            )
        )
        assert pending is not None and pending.status == "pending"

    def test_allow_session_enqueues_event(
        self, agent_db, monkeypatch
    ) -> None:
        db, SessionLocal = agent_db
        monkeypatch.setattr(sea, "SessionLocal", SessionLocal)
        monkeypatch.setattr(sea, "get_settings", lambda: self._settings(True))
        created = self._seed_agent_session(db)
        svc = SubAppService(db, WORKSPACE, ACTOR)
        accepted = svc.accept_session_event(
            created.session_id,
            SubAppSessionEventRequest(
                token=created.token,
                event_type="quiz.submit",
                payload={"question_id": "q1", "selected": "B"},
            ),
        )
        result = sea.decide_subapp_agent_consent(
            session_id=created.session_id,
            token=accepted.next_token,
            decision="allow_session",
            actor_id=ACTOR,
        )
        assert result["triggered"] is True
        job = db.scalar(
            select(m.DurableJob).where(
                m.DurableJob.kind == sea.JOB_KIND,
                m.DurableJob.workspace_id == WORKSPACE,
            )
        )
        assert job is not None

    def test_global_allow_bypasses_consent(
        self, agent_db, monkeypatch
    ) -> None:
        db, SessionLocal = agent_db
        monkeypatch.setattr(sea, "SessionLocal", SessionLocal)
        monkeypatch.setattr(sea, "get_settings", lambda: self._settings(True))
        workspace = db.get(m.Workspace, WORKSPACE)
        workspace.subapp_agent_consent = "allow"
        db.commit()
        created = self._seed_agent_session(db)
        accepted = SubAppService(db, WORKSPACE, ACTOR).accept_session_event(
            created.session_id,
            SubAppSessionEventRequest(
                token=created.token,
                event_type="quiz.submit",
                payload={"question_id": "q1", "selected": "B"},
            ),
        )
        assert accepted.agent["triggered"] is True
        assert accepted.agent["consent_required"] is False
        job = db.scalar(
            select(m.DurableJob).where(
                m.DurableJob.kind == sea.JOB_KIND,
                m.DurableJob.workspace_id == WORKSPACE,
            )
        )
        assert job is not None

    def test_disabled_flag_never_queues(
        self, agent_db, monkeypatch
    ) -> None:
        db, SessionLocal = agent_db
        monkeypatch.setattr(sea, "SessionLocal", SessionLocal)
        monkeypatch.setattr(sea, "get_settings", lambda: self._settings(False))
        created = self._seed_agent_session(db)
        accepted = SubAppService(db, WORKSPACE, ACTOR).accept_session_event(
            created.session_id,
            SubAppSessionEventRequest(
                token=created.token,
                event_type="quiz.submit",
                payload={"question_id": "q1", "selected": "B"},
            ),
        )
        assert accepted.agent["disabled"] is True
        assert accepted.agent["triggered"] is False


class TestAgentToolRouting:
    def test_publish_web_app_forwards_contract(self, db: Session) -> None:
        from app.core.config import get_settings
        from app.services.sandbox import SandboxAgentWorkspaceService

        validation_id = _seed_validation(db)
        service = SandboxAgentWorkspaceService(
            db,
            WORKSPACE,
            ACTOR,
            get_settings(),
        )
        result = service.execute_agent_tool(
            "sandbox_publish_web_app",
            {
                "validation_id": validation_id,
                "title": "行程规划器",
                "interaction_contract": VALID_CONTRACT,
            },
            chat_session_id=CHAT_SESSION_ID,
            agent_authorized=True,
        )
        assert result["subapp_mode"] is True
        assert result["artifact_version_id"] is not None
        part_data = result["part"]["data"]
        assert part_data["subapp_mode"] is True
        assert part_data["artifact_version_id"] == result["artifact_version_id"]
