from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-context-tests-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'context.db').as_posix()}"
os.environ["LEARNGRAPH_ENV"] = "test"

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.memory_event_models import MemoryScopeContext, MemorySearchDocument  # noqa: E402
from app.domain.schemas.context_builds import ContextBuildRequest  # noqa: E402
from app.services.context_builder import ContextBuilder  # noqa: E402
from app.services.memory_retrieval import MemoryHybridRetriever  # noqa: E402
from app.services.memory_router import MemoryRouter  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        try:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_search_fts USING fts5("
                "document_id UNINDEXED, tenant_id UNINDEXED, workspace_id UNINDEXED, "
                "subject, content, keywords, memory_type, entity_aliases, tokenize='trigram')"
            )
        except Exception:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_search_fts USING fts5("
                "document_id UNINDEXED, tenant_id UNINDEXED, workspace_id UNINDEXED, "
                "subject, content, keywords, memory_type, entity_aliases)"
            )
    yield


def test_scope_filter_and_deterministic_context_hash() -> None:
    with SessionLocal() as db:
        db.add_all(
            [
                MemorySearchDocument(
                    target_type="memory",
                    target_id="allowed",
                    memory_layer="L4",
                    memory_type="project_decision",
                    subject="Neo4j 决定",
                    content="先不用 Neo4j",
                    tenant_id="tenant-a",
                    subject_user_id="user-a",
                    workspace_id="workspace-a",
                    source_event_id="evt-a",
                    content_hash="a" * 64,
                    confidence=1.0,
                ),
                MemorySearchDocument(
                    target_type="memory",
                    target_id="forbidden",
                    memory_layer="L4",
                    memory_type="project_decision",
                    subject="另一个项目",
                    content="不能泄漏",
                    tenant_id="tenant-a",
                    subject_user_id="user-a",
                    workspace_id="workspace-b",
                    source_event_id="evt-b",
                    content_hash="b" * 64,
                    confidence=1.0,
                ),
            ]
        )
        db.commit()
        scope = MemoryScopeContext(
            tenant_id="tenant-a",
            principal_user_id="user-a",
            workspace_id="workspace-a",
        )
        builder = ContextBuilder(db, MemoryRouter(MemoryHybridRetriever(db)))
        request = ContextBuildRequest(query="为什么不用 Neo4j", token_budget=2000)
        first = builder.build(scope, request).view
        second = builder.build(scope, request).view
        assert first.package_hash == second.package_hash
        assert [item.target_id for item in first.memories] == ["allowed"]
        assert first.total_tokens <= request.token_budget
        assert first.excluded["out_of_scope"] == 0  # SQL scope filter prevents hydration.
