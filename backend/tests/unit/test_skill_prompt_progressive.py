"""Prompt-level progressive disclosure for Agent Skill packages.

Covers the three 2026-08 progressive-disclosure refinements:
1. LRU ordering — packages most recently announced via lg_skill_used sort
   before older / never-used ones (cold start falls back to install order).
2. Catalog-limit truncation notice — when more authorized packages exist than
   the catalog max, the injected text says how many were omitted so the model
   never mistakes the catalog for the full set.
3. last_used_at touch — lg_skill_used persists a purely informational LRU
   timestamp (never an authorization input).

Runs entirely against an in-memory scratch DB; no network, no ports.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.core.security import Principal
from app.domain import models as m
from app.domain.extension_models import ExtensionPermissionGrant, SkillRecord
from app.domain.models import utc_now
from app.services.mcp_skills import MCPAndSkillService

WORKSPACE = "ws-skill-progressive"
ACTOR = "user-skill-progressive"
TENANT = "tenant-unit"


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
            m.Workspace(
                id=WORKSPACE,
                tenant_id=TENANT,
                owner_user_id=ACTOR,
                name="progressive disclosure",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def _add_package_skill(
    db: Session,
    skill_key: str,
    instructions: str,
    *,
    last_used_at=None,
) -> SkillRecord:
    skill = SkillRecord(
        id=f"sk-{skill_key}",
        workspace_id=WORKSPACE,
        skill_key=skill_key,
        name=skill_key,
        source="unit-test",
        version="1.0.0",
        generated_by="user_import",
        kind="agent_skill_package",
        package_format="skill_md_v1",
        manifest_json={"description": f"desc {skill_key}"},
        manifest_hash="0" * 64,
        instructions_markdown=instructions,
        required_tools=[],
        required_permissions=[],
        status="enabled",
        enabled=True,
        last_used_at=last_used_at,
    )
    db.add(skill)
    db.flush()
    return skill


def _grant(db: Session, skill: SkillRecord) -> None:
    db.add(
        ExtensionPermissionGrant(
            workspace_id=WORKSPACE,
            subject_type="skill",
            subject_id=skill.id,
            decision="always",
            status="active",
            permissions=[],
            authorization_hash=MCPAndSkillService._skill_authorization_hash(skill),
            decided_by=ACTOR,
            reason="unit-test",
        )
    )
    db.flush()


def _service(db: Session, settings: Settings) -> MCPAndSkillService:
    principal = Principal(
        user_id=ACTOR,
        username="tester",
        tenant_id=TENANT,
        session_id="s1",
    )
    return MCPAndSkillService(
        db,
        WORKSPACE,
        ACTOR,
        settings,
        workspace=db.get(m.Workspace, WORKSPACE),
        principal=principal,
    )


def test_lru_orders_recently_used_first(db: Session) -> None:
    now = utc_now()
    _add_package_skill(db, "alpha", "alpha instructions")
    _add_package_skill(db, "bravo", "bravo instructions", last_used_at=now)
    _add_package_skill(db, "charlie", "charlie instructions", last_used_at=now - timedelta(hours=1))
    for skill in db.query(SkillRecord).all():
        _grant(db, skill)
    service = _service(db, _settings())
    text = service.agent_skill_package_instructions()
    for key in ("alpha", "bravo", "charlie"):
        assert f"### Skill: {key}" in text
    assert text.index("### Skill: bravo") < text.index("### Skill: charlie")
    assert text.index("### Skill: charlie") < text.index("### Skill: alpha")


def test_catalog_truncation_notice(db: Session) -> None:
    settings = _settings(
        skill_prompt_catalog_max_entries=2,
        skill_prompt_inline_char_limit=50,
    )
    for index in range(5):
        _add_package_skill(db, f"skill-{index}", "x" * 600)
    for skill in db.query(SkillRecord).all():
        _grant(db, skill)
    service = _service(db, settings)
    text = service.agent_skill_package_instructions()
    assert "### Skill catalog" in text
    # only the 2 newest (install order) appear; 3 are omitted with a notice
    assert text.count("- `skill-") == 2
    assert "3 more authorized skills exceed the catalog" in text


def test_announce_usage_touches_last_used_at(db: Session) -> None:
    settings = _settings(skill_prompt_inline_char_limit=50)
    _add_package_skill(db, "alpha", "x" * 600)
    _add_package_skill(db, "bravo", "x" * 600)
    for skill in db.query(SkillRecord).all():
        _grant(db, skill)
    service = _service(db, settings)
    # install order: alpha first
    text0 = service.agent_skill_package_instructions()
    assert text0.index("- `alpha`") < text0.index("- `bravo`")
    # announce bravo -> last_used_at persisted
    result = service.invoke_agent_function("lg_skill_used", {"skill_key": "bravo"})
    assert result["status"] == "succeeded"
    db.refresh(service._resolve_skill_by_key("bravo"))
    assert service._resolve_skill_by_key("bravo").last_used_at is not None
    # LRU now puts bravo first
    text1 = service.agent_skill_package_instructions()
    assert text1.index("- `bravo`") < text1.index("- `alpha`")
