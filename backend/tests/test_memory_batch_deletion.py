from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.errors import AppError
from app.domain.memory_event_models import MemoryScopeContext, MemorySearchDocument, MemoryStream
from app.domain.memory_event_types import MemoryEventType
from app.domain.models import MemoryEmbedding, MemoryRecord, Workspace
from app.domain.schemas.management import MemoryCreateRequest
from app.services.memory import MemoryService
from app.services.memory_event_store import AppendEvent
from app.services.memory_projector import MemoryProjector, ensure_memory_search_fts


@pytest.fixture
def service(tmp_path):
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        workspace = Workspace(id='w', tenant_id='t', owner_user_id='u', name='test')
        db.add(workspace)
        db.flush()
        ensure_memory_search_fts(db)
        db.commit()
        provider = MagicMock(provider_id='local_workspace_markdown', remote_capability=False)
        service = MemoryService(db, workspace, 'u', provider, tmp_path)
        with patch.object(service, '_project_after_commit'), patch.object(service, '_delete_projection_after_commit'):
            yield service
    engine.dispose()


def create(service, title):
    return service.create(MemoryCreateRequest(title=title, content=f'{title} content'))


def test_batch_delete_deduplicates_and_all_items_can_be_restored(service):
    first, second, keep = [create(service, title) for title in ('first', 'second', 'keep')]
    result = service.delete_many([first.id, first.id, second.id])
    assert result == {'deleted_count': 2, 'recoverable_count': 2}
    assert [item.id for item in service.list()] == [keep.id]
    assert {item.id for item in service.list(state='deleted')} == {first.id, second.id}
    for item in (first, second):
        restored = service.restore_deleted(item.id)
        assert restored.state == 'active'
        assert f'{item.title} content' in service.get(item.id).content


def test_batch_validates_every_target_before_deleting(service):
    first = create(service, 'first')
    with pytest.raises(AppError) as error:
        service.delete_many([first.id, 'outside-workspace'])
    assert error.value.status_code == 404
    assert service.db.get(MemoryRecord, first.id).state == 'active'


def test_batch_failure_rolls_back_earlier_items(service):
    first, second = create(service, 'first'), create(service, 'second')
    original = service.delete
    def fail_second(memory_id):
        if memory_id == second.id:
            raise RuntimeError('simulated failure')
        return original(memory_id)
    with patch.object(service, 'delete', side_effect=fail_second):
        with pytest.raises(RuntimeError):
            service.delete_many([first.id, second.id])
    service.db.expire_all()
    assert all(service.db.get(MemoryRecord, item.id).state == 'active' for item in (first, second))
    assert 'first content' in service.get(first.id).content
    assert not service._after_commit


def test_event_only_memory_is_forgotten_and_cannot_be_replayed(service):
    scope = MemoryScopeContext(tenant_id='t', workspace_id='w', principal_user_id='u')
    payload = {'memory_id': 'event-only', 'title': 'event secret', 'content': 'secret content'}
    event = service._event_store().append(
        scope, aggregate_type='memory_atom', aggregate_id='event-only', expected_version=0,
        event=AppendEvent(event_type=MemoryEventType.MEMORY_CREATED, payload=payload, idempotency_key='event'),
    ).event
    MemoryProjector(service.db).apply(event, payload)
    service.db.add(MemoryEmbedding(workspace_id='w', memory_id='event-only', model_key='test',
                                   content_hash='hash', vector=[1.0]))
    service.db.commit()
    assert any(item.id == 'event-only' for item in service.list_views())
    assert service.delete_many(['event-only']) == {'deleted_count': 1, 'recoverable_count': 0}
    assert service.db.scalar(select(MemorySearchDocument)) is None
    assert service.db.scalar(select(MemoryEmbedding)) is None
    assert service.db.scalar(text('SELECT count(*) FROM memory_search_fts')) == 0
    assert service.db.get(MemoryStream, event.stream_id).status == 'forgotten'
    MemoryProjector(service.db).apply(event, payload)
    assert service.db.scalar(select(MemorySearchDocument)) is None
