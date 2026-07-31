from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-context-shadow-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'shadow.db').as_posix()}"
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ.setdefault("LEARNGRAPH_MEMORY_EVENT_MASTER_KEY", "test-key")

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.schemas.context_builds import (  # noqa: E402
    ContextBuildView,
    ContextEvidenceView,
)
from app.services.context_builder import ContextBuilder, BuiltContext  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


def _mock_provider():
    provider = MagicMock()
    provider.provider_id = "test-provider"
    provider.model_id = "test-model"
    provider.context_window_tokens = 32000
    return provider


def _mock_builder(prompt_block: str = "<HOST_DATA>v2</HOST_DATA>"):
    view = ContextBuildView(
        context_build_id="ctx-test",
        trace_id="trace-test",
        memories=[
            ContextEvidenceView(
                kind="memory",
                target_id="m1",
                title="t",
                content="c",
                source_event_id="evt",
                scope="workspace:w1",
                confidence=0.9,
                status="active",
                retrieval_reason="test",
                trust="user_explicit",
                score=0.8,
            )
        ],
        package_hash="abc123",
        total_tokens=100,
        excluded={},
    )
    builder = MagicMock(spec=ContextBuilder)
    builder.build.return_value = BuiltContext(view, prompt_block)
    return builder


def test_v2_flag_off_returns_none():
    with SessionLocal() as db:
        from app.core.config import get_settings

        original = get_settings().memory_context_builder_v2
        try:
            get_settings().memory_context_builder_v2 = False  # type: ignore[misc]
            from app.services.chat import ChatService

            svc = ChatService(
                db=db,
                workspace_id="w1",
                actor_id="u1",
                model_provider=_mock_provider(),
                tenant_id="t1",
                context_builder=_mock_builder(),
            )
            block, telemetry = svc._build_v2_memory_context("s1", "hello")
            assert block is None
            assert telemetry == {}
        finally:
            get_settings().memory_context_builder_v2 = original  # type: ignore[misc]


def test_v2_events_mode_returns_prompt_block():
    with SessionLocal() as db:
        from app.core.config import get_settings

        orig_v2 = get_settings().memory_context_builder_v2
        orig_read = get_settings().memory_read_mode
        try:
            get_settings().memory_context_builder_v2 = True  # type: ignore[misc]
            get_settings().memory_read_mode = "events"  # type: ignore[misc]
            from app.services.chat import ChatService

            mock_b = _mock_builder("<HOST_DATA>events-v2</HOST_DATA>")
            svc = ChatService(
                db=db,
                workspace_id="w1",
                actor_id="u1",
                model_provider=_mock_provider(),
                tenant_id="t1",
                context_builder=mock_b,
            )
            block, telemetry = svc._build_v2_memory_context("s1", "hello")
            assert block == "<HOST_DATA>events-v2</HOST_DATA>"
            assert telemetry["read_mode"] == "events"
            assert telemetry["memory_count"] == 1
            mock_b.build.assert_called_once()
        finally:
            get_settings().memory_context_builder_v2 = orig_v2  # type: ignore[misc]
            get_settings().memory_read_mode = orig_read  # type: ignore[misc]


def test_v2_shadow_mode_returns_none_but_builds():
    with SessionLocal() as db:
        from app.core.config import get_settings

        orig_v2 = get_settings().memory_context_builder_v2
        orig_read = get_settings().memory_read_mode
        try:
            get_settings().memory_context_builder_v2 = True  # type: ignore[misc]
            get_settings().memory_read_mode = "shadow"  # type: ignore[misc]
            from app.services.chat import ChatService

            mock_b = _mock_builder()
            svc = ChatService(
                db=db,
                workspace_id="w1",
                actor_id="u1",
                model_provider=_mock_provider(),
                tenant_id="t1",
                context_builder=mock_b,
            )
            block, telemetry = svc._build_v2_memory_context("s1", "hello")
            assert block is None
            assert telemetry["read_mode"] == "shadow"
            mock_b.build.assert_called_once()
        finally:
            get_settings().memory_context_builder_v2 = orig_v2  # type: ignore[misc]
            get_settings().memory_read_mode = orig_read  # type: ignore[misc]


def test_v2_failure_degrades_to_none():
    with SessionLocal() as db:
        from app.core.config import get_settings

        orig_v2 = get_settings().memory_context_builder_v2
        orig_read = get_settings().memory_read_mode
        try:
            get_settings().memory_context_builder_v2 = True  # type: ignore[misc]
            get_settings().memory_read_mode = "events"  # type: ignore[misc]
            from app.services.chat import ChatService

            broken = MagicMock(spec=ContextBuilder)
            broken.build.side_effect = RuntimeError("simulated failure")
            svc = ChatService(
                db=db,
                workspace_id="w1",
                actor_id="u1",
                model_provider=_mock_provider(),
                tenant_id="t1",
                context_builder=broken,
            )
            block, telemetry = svc._build_v2_memory_context("s1", "hello")
            assert block is None
            assert telemetry.get("degraded") is True
        finally:
            get_settings().memory_context_builder_v2 = orig_v2  # type: ignore[misc]
            get_settings().memory_read_mode = orig_read  # type: ignore[misc]
