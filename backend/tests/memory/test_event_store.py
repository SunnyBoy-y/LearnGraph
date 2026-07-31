from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-event-tests-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'events.db').as_posix()}"
os.environ["LEARNGRAPH_ENABLE_DEMO_SEED"] = "false"
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ["LEARNGRAPH_MEMORY_EVENT_MASTER_KEY"] = "test-memory-event-master-key"

from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.memory_event_models import (  # noqa: E402
    MemoryPayloadKey,
    MemoryScopeContext,
)
from app.services.memory_crypto import MemoryPayloadCipher  # noqa: E402
from app.services.memory_event_store import AppendEvent, MemoryEventStore  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


def scope() -> MemoryScopeContext:
    return MemoryScopeContext(
        tenant_id="tenant-a",
        principal_user_id="user-a",
        workspace_id="workspace-a",
    )


def test_append_cas_idempotency_and_payload_encryption() -> None:
    with SessionLocal() as db:
        store = MemoryEventStore(db, MemoryPayloadCipher("secret"))
        command = AppendEvent(
            event_type="memory.atom_created",
            payload={"memory_id": "mem-a", "content": "你好"},
            idempotency_key="create-mem-a",
            actor_id="user-a",
        )
        first = store.append(
            scope(),
            aggregate_type="memory_atom",
            aggregate_id="mem-a",
            expected_version=0,
            event=command,
        )
        db.commit()
        assert first.event.stream_version == 1
        assert first.event.payload_ciphertext
        assert b"\xe4\xbd\xa0" not in first.event.payload_ciphertext
        assert store.read_payload(scope(), first.event.event_id)["content"] == "你好"

        replay = store.append(
            scope(),
            aggregate_type="memory_atom",
            aggregate_id="mem-a",
            expected_version=0,
            event=command,
        )
        assert replay.idempotent_replay is True
        assert replay.event.event_id == first.event.event_id

        with pytest.raises(Exception) as conflict:
            store.append(
                scope(),
                aggregate_type="memory_atom",
                aggregate_id="mem-a",
                expected_version=0,
                event=AppendEvent(
                    event_type="memory.atom_corrected",
                    payload={"memory_id": "mem-a", "content": "new"},
                    idempotency_key="update-mem-a",
                    actor_id="user-a",
                ),
            )
        assert getattr(conflict.value, "code", "") == "memory_stream_version_conflict"


def test_forget_destroys_data_keys_and_keeps_envelope() -> None:
    with SessionLocal() as db:
        store = MemoryEventStore(db, MemoryPayloadCipher("secret"))
        result = store.append(
            scope(),
            aggregate_type="memory_atom",
            aggregate_id="mem-a",
            expected_version=0,
            event=AppendEvent(
                event_type="memory.atom_created",
                payload={"memory_id": "mem-a", "content": "secret memory"},
                idempotency_key="create-forget-a",
                actor_id="user-a",
            ),
        )
        store.destroy_stream_payloads(
            scope(), result.event.stream_id, actor_id="user-a", reason="test"
        )
        db.commit()
        key = db.get(MemoryPayloadKey, result.event.payload_key_id)
        assert key is not None
        assert key.status == "destroyed"
        assert key.wrapped_dek is None
        with pytest.raises(Exception):
            store.read_payload(scope(), result.event.event_id)
