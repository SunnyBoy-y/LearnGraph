from __future__ import annotations

import os
import tempfile
from pathlib import Path

_SCRATCH = Path(tempfile.mkdtemp(prefix="lg-provider-failure-tests-"))
os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{(_SCRATCH / 'provider.db').as_posix()}"
os.environ["LEARNGRAPH_MEMORY_ROOT"] = str(_SCRATCH / "memory")
os.environ["LEARNGRAPH_ENV"] = "test"
os.environ["LEARNGRAPH_MEMORY_EVENT_MASTER_KEY"] = "provider-failure-test-key"
os.environ["LEARNGRAPH_MEMORY_WRITE_MODE"] = "dual"

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.domain import memory_event_models, models  # noqa: E402,F401
from app.domain.memory_event_models import MemoryProjectionOutbox  # noqa: E402
from app.domain.models import MemoryRecord, Workspace  # noqa: E402
from app.domain.schemas.management import MemoryCreateRequest  # noqa: E402
from app.providers.ports.memory import ProviderHealth  # noqa: E402
from app.services.memory import MemoryService  # noqa: E402


class FailingProvider:
    provider_id = "failing-remote"
    available = True
    remote_capability = True

    def health(self):
        return ProviderHealth(self.provider_id, True, "degraded", True)

    def upsert(self, *args, **kwargs):
        raise TimeoutError("provider unavailable")

    def delete(self, provider_record_id: str):
        raise TimeoutError("provider unavailable")


def test_provider_failure_does_not_rollback_canonical_memory() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    settings = get_settings()
    settings.memory_root.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        workspace = Workspace(
            id="workspace-provider-failure",
            tenant_id="tenant-a",
            owner_user_id="user-a",
            name="Provider Failure",
        )
        db.add(workspace)
        db.commit()
        view = MemoryService(
            db,
            workspace,
            "user-a",
            FailingProvider(),
            settings.memory_root,
        ).create(
            MemoryCreateRequest(
                title="DB first",
                content="This fact must survive provider timeout.",
                source="user",
            )
        )
        record = db.get(MemoryRecord, view.id)
        assert record is not None
        assert record.state == "active"
        assert record.head_event_id
        jobs = db.query(MemoryProjectionOutbox).filter_by(aggregate_id=record.id).all()
        assert jobs
        assert any(item.status == "queued" for item in jobs)
