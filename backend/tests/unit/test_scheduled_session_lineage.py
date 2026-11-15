"""Scheduled result sessions link to a source without inheriting its history."""
from datetime import timedelta

import pytest

from test_scheduled_tasks import env
from app.core.errors import AppError
from app.domain.models import ChatSession, Message, utc_now
from app.services.chat import ChatService


def linked_chat(env, *, session_kind='scheduled_task', count=3):
    parent = ChatSession(id='source', workspace_id='ws', title='Source')
    child = ChatSession(
        id='result', workspace_id='ws', title='Result',
        session_kind=session_kind, parent_session_id=parent.id,
    )
    env.db.add_all([parent, child])
    env.db.flush()
    started = utc_now() - timedelta(hours=1)
    env.db.add(Message(
        id='parent-only', workspace_id='ws', session_id=parent.id,
        role='user', content='PARENT-CONTEXT', status='completed',
        created_at=started - timedelta(seconds=1),
    ))
    for index in range(count):
        env.db.add(Message(
            id=f'local-{index:04d}', workspace_id='ws', session_id=child.id,
            role='user', content=f'LOCAL-{index}', status='completed',
            created_at=started + timedelta(seconds=index),
        ))
    env.db.commit()
    chat = ChatService(env.db, 'ws', 'owner', None, settings=env.settings)
    return chat, child


@pytest.mark.parametrize('session_kind', ['scheduled_task', 'concept_branch', 'side', 'agent'])
def test_navigation_parent_does_not_require_branch_source_or_inherit_history(env, session_kind):
    chat, session = linked_chat(env, session_kind=session_kind)
    expected = [f'local-{index:04d}' for index in range(3)]
    assert session.source_message_id is None
    assert [message.id for message in chat.list_messages(session.id)] == expected
    page = chat.list_messages_page(session.id, limit=2)
    assert [message['id'] for message in page['items']] == expected[-2:]
    assert page['has_more_before'] is True
    older = chat.list_messages_page(session.id, limit=2, before_id=page['oldest_id'])
    assert [message['id'] for message in older['items']] == expected[:1]
    assert older['has_more_before'] is False
    with pytest.raises(AppError) as caught:
        chat.list_messages_page(session.id, before_id='parent-only')
    assert caught.value.code == 'message_not_found'


def test_scheduled_history_is_bounded_but_old_cursor_can_reach_local_messages(env):
    chat, session = linked_chat(env, count=505)
    timeline = chat.list_messages(session.id)
    assert len(timeline) == 500
    assert timeline[0].id == 'local-0005'
    assert timeline[-1].id == 'local-0504'
    older = chat.list_messages_page(session.id, limit=2, before_id='local-0002')
    assert [message['id'] for message in older['items']] == ['local-0000', 'local-0001']
    assert older['has_more_before'] is False


@pytest.mark.parametrize('parent_kind', ['main', 'scheduled_task'])
def test_edit_branch_still_inherits_only_prefix_before_source(env, parent_kind):
    chat, parent = linked_chat(env, session_kind=parent_kind)
    # A normal edit branch has no navigation parent of its own. A scheduled
    # parent keeps its navigation link; neither changes edit-branch semantics.
    if parent_kind == 'main':
        parent.parent_session_id = None
    branch = ChatSession(
        id='edit', workspace_id='ws', title='Edited',
        parent_session_id=parent.id, source_message_id='local-0001',
    )
    env.db.add(branch)
    env.db.flush()
    env.db.add(Message(
        id='replacement', workspace_id='ws', session_id=branch.id,
        role='user', content='Edited request', status='completed',
    ))
    env.db.commit()
    assert [message.id for message in chat.list_messages(branch.id)] == ['local-0000', 'replacement']


@pytest.mark.parametrize('source_message_id', [None, 'unavailable'])
def test_real_edit_branch_still_rejects_missing_source(env, source_message_id):
    chat, parent = linked_chat(env)
    branch = ChatSession(
        id='broken-edit', workspace_id='ws', title='Broken edit',
        parent_session_id=parent.id, source_message_id=source_message_id,
    )
    env.db.add(branch)
    env.db.commit()
    with pytest.raises(AppError) as caught:
        chat.list_messages(branch.id)
    assert caught.value.code == 'invalid_branch_lineage'
    assert caught.value.status_code == 409
