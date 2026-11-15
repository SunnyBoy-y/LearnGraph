"""Cache behavior is tested with local providers and isolated databases only."""
from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import OpeningPromptCache
from app.domain.schemas.opening_prompts import OpeningPromptRequest
from app.services import opening_prompt_cache as cache
from app.services.opening_prompts import OpeningPromptService
from tests.unit.test_opening_prompts import env, seed_project, seed_memory


def test_cached_pool_is_persistent_and_shared_between_service_instances(env):
    seed_project(env)
    first = env.service.generate(OpeningPromptRequest(count=1))
    second = OpeningPromptService(env.db, env.context, env.settings).generate(OpeningPromptRequest(count=9))
    assert first.prompts == second.prompts[:1]
    assert first.pool == second.pool
    assert len(second.prompts) == 9
    assert len(env.prompts) == 1


def test_interval_and_hourly_budget_reuse_existing_pool(env, monkeypatch):
    seed_project(env)
    now = [cache.time.time()]
    monkeypatch.setattr(cache.time, "time", lambda: now[0])
    first = env.service.generate(OpeningPromptRequest())
    for _ in range(20):
        assert env.service.generate(OpeningPromptRequest()).metadata["cached"]
    assert len(env.prompts) == 1
    now[0] += cache.GENERATION_INTERVAL + 1
    second = env.service.generate(OpeningPromptRequest(exhausted_pool_id=first.metadata["request_id"]))
    assert first.pool != second.pool
    now[0] += cache.GENERATION_INTERVAL + 1
    third = env.service.generate(OpeningPromptRequest(exhausted_pool_id=second.metadata["request_id"]))
    now[0] += cache.GENERATION_INTERVAL + 1
    limited = env.service.generate(OpeningPromptRequest(exhausted_pool_id=third.metadata["request_id"]))
    assert limited.metadata["cached"]
    assert limited.metadata["refresh_after_seconds"] > 0
    assert len(env.prompts) == cache.MAX_GENERATIONS
    now[0] += cache.GENERATION_WINDOW
    assert env.service.generate(OpeningPromptRequest(exhausted_pool_id=third.metadata["request_id"])).pool != third.pool
    assert len(env.prompts) == cache.MAX_GENERATIONS + 1


def test_unused_pool_survives_time_and_reentry_without_regeneration(env, monkeypatch):
    seed_project(env)
    first = env.service.generate(OpeningPromptRequest())
    now = [cache.time.time()]
    monkeypatch.setattr(cache.time, "time", lambda: now[0])
    for elapsed in [601, 3601, 86400]:
        now[0] += elapsed
        response = OpeningPromptService(env.db, env.context, env.settings).generate(OpeningPromptRequest())
        assert response.pool == first.pool
        assert response.metadata["cached"]
    assert len(env.prompts) == 1
    assert env.service.generate(OpeningPromptRequest(exhausted_pool_id="another-pool")).pool == first.pool
    assert len(env.prompts) == 1


def test_scope_changes_cannot_bypass_budget_or_return_the_previous_scope(env):
    seed_project(env)
    env.service.generate(OpeningPromptRequest())
    with pytest.raises(AppError) as error:
        env.service.generate(OpeningPromptRequest(model_id="different"))
    assert error.value.code == "opening_prompt_rate_limited"
    assert error.value.details["retry_after_seconds"] > 0
    assert len(env.prompts) == 1


def test_cache_revalidates_context_before_serving(env):
    seed_memory(env)
    env.service.generate(OpeningPromptRequest())
    from app.domain.models import MemoryRecord
    env.db.get(MemoryRecord, "memory").state = "deleted"
    env.db.commit()
    assert not env.service.generate(OpeningPromptRequest()).prompts
    assert len(env.prompts) == 1


def test_new_memories_do_not_replace_an_unused_pool(env):
    seed_memory(env)
    first = env.service.generate(OpeningPromptRequest())
    seed_memory(env, key="new-memory", content="我也在学习网络协议", title="网络协议学习")
    assert env.service.generate(OpeningPromptRequest()).pool == first.pool
    assert len(env.prompts) == 1


def test_cache_cannot_serve_changed_project_context(env):
    seed_project(env)
    env.service.generate(OpeningPromptRequest())
    from app.domain.models import Project
    env.db.get(Project, "project").title = "另一个项目主题"
    env.db.commit()
    with pytest.raises(AppError) as error:
        env.service.generate(OpeningPromptRequest())
    assert error.value.code == "opening_prompt_rate_limited"
    assert len(env.prompts) == 1


def test_user_and_workspace_pools_are_isolated(env):
    seed_project(env)
    env.service.generate(OpeningPromptRequest())
    original = cache.OpeningPromptCacheStore(env.db, env.context)
    context = SimpleNamespace(workspace_id="ws", principal=SimpleNamespace(tenant_id="tenant", user_id="bob"))
    assert original.id != cache.OpeningPromptCacheStore(env.db, context).id
    context.workspace_id = "other-ws"
    context.principal.user_id = "alice"
    assert original.id != cache.OpeningPromptCacheStore(env.db, context).id


def test_failed_generation_has_a_bounded_backoff_and_releases_lease(env, monkeypatch):
    seed_project(env)
    now = [cache.time.time()]
    monkeypatch.setattr(cache.time, "time", lambda: now[0])
    from app.services.chat import ChatService
    generate = ChatService._generate_suggested_prompt_text
    monkeypatch.setattr(ChatService, "_generate_suggested_prompt_text", lambda *a: "")
    with pytest.raises(AppError):
        env.service.generate(OpeningPromptRequest())
    row = cache.OpeningPromptCacheStore(env.db, env.context).row()
    assert row.lease_token is None
    assert row.generation_count == 1
    with pytest.raises(AppError) as error:
        env.service.generate(OpeningPromptRequest())
    assert error.value.status_code == 429
    now[0] += cache.FAILURE_BACKOFF + 1
    monkeypatch.setattr(ChatService, "_generate_suggested_prompt_text", generate)
    assert len(env.service.generate(OpeningPromptRequest()).pool) == 9


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_concurrent_claims_have_one_owner_and_expired_leases_recover(tmp_path, backend):
    if backend == "postgresql":
        url = os.environ.get("OPENING_PROMPT_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Isolated PostgreSQL test database not configured")
        engine = create_engine(url)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'claims.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    from app.core.migrations import _opening_prompt_cache
    with engine.begin() as connection:
        _opening_prompt_cache(connection)
        _opening_prompt_cache(connection)
    context = SimpleNamespace(workspace_id="ws", principal=SimpleNamespace(tenant_id="tenant", user_id=str(uuid4())))
    def claim(_):
        with Session(engine) as db:
            return cache.OpeningPromptCacheStore(db, context).claim()
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            assert list(executor.map(claim, range(4))).count(True) == 1
        with Session(engine) as db:
            store = cache.OpeningPromptCacheStore(db, context)
            row = store.row()
            assert row.generation_count == 1
            row.lease_until = 0
            row.next_generation_at = 0
            db.commit()
            assert store.claim()
            from app.domain.schemas.opening_prompts import OpeningPromptResponse
            response = OpeningPromptResponse(metadata={"request_id": "stored-batch"})
            store.save("scope", "hash", response)
        with Session(engine) as db:
            store = cache.OpeningPromptCacheStore(db, context)
            assert store.read(store.row(), "scope", "hash")["response"]["metadata"]["request_id"] == "stored-batch"
            assert not store.claim()
    finally:
        engine.dispose()


@pytest.mark.parametrize("dialect_name", ["sqlite", "postgresql"])
def test_migration_is_portable_and_idempotent(dialect_name):
    from sqlalchemy import create_mock_engine
    from app.core.migrations import _opening_prompt_cache
    statements = []
    engine = create_mock_engine(f"{dialect_name}://", lambda sql, *a, **kw: statements.append(str(sql.compile(dialect=engine.dialect))))
    _opening_prompt_cache(engine)
    assert any("CREATE TABLE opening_prompt_cache" in sql for sql in statements)
