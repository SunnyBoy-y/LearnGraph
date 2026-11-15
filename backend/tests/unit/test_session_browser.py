from datetime import datetime, timezone
import base64
import json

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import Principal
from app.domain.models import ChatSession, Membership, ResourceACL, Workspace
from app.services.session_browser import SessionBrowser


@pytest.fixture
def browser():
    engine = create_engine('sqlite://')
    for model in (Workspace, ChatSession, ResourceACL, Membership):
        model.__table__.create(engine)
    with Session(engine) as db:
        workspace = Workspace(id='w', tenant_id='t', owner_user_id='u', name='Test')
        db.add(workspace)
        db.commit()
        yield SessionBrowser(db, workspace, Principal(user_id='u', username='test', tenant_id='t', session_id='auth'))
    engine.dispose()


def add(browser, id, **kwargs):
    record = ChatSession(id=id, workspace_id='w', title=id, updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc), **kwargs)
    browser.db.add(record)
    browser.db.flush()
    return record


def test_stable_bidirectional_paging_and_small_payload(browser):
    for i in range(65):
        add(browser, f's{i:03}', pinned=i % 11 == 0, model_snapshot={'huge': 'x' * 5000}, context_capsule={'huge': 'x' * 5000})
    first = browser.page(limit=20)
    second = browser.page(limit=20, cursor=first.next_cursor)
    previous = browser.page(limit=20, cursor=second.previous_cursor, direction='previous')
    assert [x.id for x in previous.items] == [x.id for x in first.items]
    seen = [x.id for x in first.items]
    page = first
    while page.has_more:
        page = browser.page(limit=20, cursor=page.next_cursor)
        seen.extend(x.id for x in page.items)
    assert len(seen) == len(set(seen)) == 65
    assert 'model_snapshot' not in first.items[0].model_dump()
    assert 'context_capsule' not in first.items[0].model_dump()
    assert all(x.pinned for x in first.items[:6])


def test_recent_order_does_not_treat_an_old_pin_as_latest_activity(browser):
    add(browser, 'old-pin', pinned=True)
    latest = add(browser, 'newest')
    latest.updated_at = datetime(2026, 11, 1, tzinfo=timezone.utc)
    browser.db.flush()
    first = browser.page(limit=1, pinned_first=False)
    assert first.items[0].id == 'newest'
    assert browser.page(limit=1, pinned_first=False, cursor=first.next_cursor).items[0].id == 'old-pin'
    assert browser.page(limit=1).items[0].id == 'old-pin'


def test_roots_children_orphans_and_hidden_groups(browser):
    add(browser, 'parent', project_id='p')
    add(browser, 'child', project_id='p', parent_session_id='parent')
    add(browser, 'grandchild', project_id='p', parent_session_id='child')
    add(browser, 'cross', parent_session_id='parent')
    add(browser, 'archived', status='archived')
    add(browser, 'orphan', parent_session_id='archived')
    roots = browser.page(project_id='p')
    assert [x.id for x in roots.items] == ['parent']
    assert roots.items[0].has_children
    assert [x.id for x in browser.page(parent_session_id='parent').items] == ['child']
    assert set(x.id for x in browser.page(ungrouped=True).items) == {'cross', 'orphan'}
    with pytest.raises(AppError):
        browser.page(parent_session_id='parent', ungrouped=True)


@pytest.mark.parametrize('cursor', ['garbage', 'W10', 'bnVsbA', base64.urlsafe_b64encode(b'{}').decode()])
def test_invalid_cursor_is_controlled_422(browser, cursor):
    with pytest.raises(AppError) as error:
        browser.page(cursor=cursor)
    assert error.value.status_code == 422


def test_cursor_cannot_cross_query_boundary(browser):
    for i in range(3):
        add(browser, f's{i}')
    cursor = browser.page(limit=1).next_cursor
    for scope in ({'ungrouped': True}, {'query': 's'}, {'include_archived': True}):
        with pytest.raises(AppError):
            browser.page(limit=1, cursor=cursor, **scope)


def test_acl_before_limit_exact_permission_and_revoke(browser):
    browser.owner = False
    browser.readable = True
    for i in range(12):
        record = add(browser, f'z{i:02}')
        browser.db.add(ResourceACL(id=f'acl{i}', workspace_id='w', tenant_id='t', resource_type='session', resource_id=record.id,
            grantee_type='user', grantee_id='other', permissions=['read'], granted_by='u'))
    add(browser, 'a1')
    add(browser, 'a2')
    add(browser, 'a3')
    browser.db.flush()
    page = browser.page(limit=2)
    assert [x.id for x in page.items] == ['a3', 'a2']
    assert page.has_more
    with pytest.raises(AppError):
        browser.detail('z01')
    acl = browser.db.get(ResourceACL, 'acl1')
    acl.grantee_id, acl.permissions = 'u', ['read_write']
    browser.db.flush()
    assert 'z01' not in [x.id for x in browser.page().items]
    acl.permissions = ['read']
    browser.db.flush()
    assert browser.detail('z01').id == 'z01'
    acl.grantee_id = 'other'
    acl.revoked_at = datetime.now(timezone.utc)
    browser.db.flush()
    assert browser.detail('z01').id == 'z01'


def test_organization_and_role_acl_match_exactly(browser):
    browser.owner = False
    browser.readable = True
    browser.workspace.organization_id = 'organization'
    browser.membership = Membership(role_id='reader-role', organization_id='organization', user_id='u', tenant_id='t')
    add(browser, 'role-session')
    add(browser, 'org-session')
    for id, kind, grantee in [('role-session', 'role', 'reader-role'), ('org-session', 'organization', 'organization')]:
        browser.db.add(ResourceACL(workspace_id='w', tenant_id='t', resource_type='session', resource_id=id,
            grantee_type=kind, grantee_id=grantee, permissions=['read'], granted_by='u'))
    browser.db.flush()
    assert {item.id for item in browser.page().items} == {'role-session', 'org-session'}
    browser.membership = None
    assert {item.id for item in browser.page().items} == {'org-session'}


def test_browse_index_migration_is_idempotent(browser):
    from sqlalchemy import inspect
    from app.core.migrations.session_browser import apply_session_browser
    connection = browser.db.connection()
    apply_session_browser(connection)
    apply_session_browser(connection)
    names = {index['name'] for index in inspect(connection).get_indexes('chat_sessions')}
    assert {'ix_session_browse_recent', 'ix_session_browse_project', 'ix_session_browse_children'} <= names


def test_activity_aggregation_respects_local_days_and_dst(browser):
    session = add(browser, 'summer')
    session.created_at = datetime(2026, 7, 1, 6, 30, tzinfo=timezone.utc)
    browser.db.flush()
    result = browser.activity(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc), 480, 'America/Los_Angeles')
    assert result == {'2026-06-30': 1}
    with pytest.raises(AppError):
        browser.activity(datetime(2026, 1, 1), datetime(2028, 1, 1), 0)


def test_query_is_bounded_and_does_not_select_large_json(browser):
    for i in range(1000):
        add(browser, f's{i:04}')
    statements = []
    def collect(_conn, _cursor, statement, _params, _ctx, _many):
        statements.append(statement)
    event.listen(browser.db.get_bind(), 'before_cursor_execute', collect)
    try:
        page = browser.page(limit=20)
    finally:
        event.remove(browser.db.get_bind(), 'before_cursor_execute', collect)
    assert len(page.items) == 20 and page.has_more
    assert len(statements) == 1
    assert 'LIMIT' in statements[0]
    assert 'model_snapshot' not in statements[0] and 'context_capsule' not in statements[0]


@pytest.mark.parametrize('filter', [{'query': 'needle'}, {'graph_id': 'graph-child'}, {'session_kind': 'side'}])
def test_filtered_children_of_filtered_out_parents_become_roots(browser, filter):
    add(browser, 'parent', graph_id='other', session_kind='main')
    child = add(browser, 'child', parent_session_id='parent', graph_id='graph-child', session_kind='side')
    child.title = 'needle'
    browser.db.flush()
    assert [item.id for item in browser.page(**filter).items] == ['child']


def test_children_and_ancestors_never_read_large_json(browser):
    add(browser, 'root', context_capsule={'large': 'x' * 10000})
    add(browser, 'child', parent_session_id='root')
    statements = []
    def collect(_conn, _cursor, sql, _params, _ctx, _many):
        statements.append(sql)
    event.listen(browser.db.get_bind(), 'before_cursor_execute', collect)
    try:
        assert len(browser.page(parent_session_id='root').items) == 1
        assert len(browser.ancestors('child')) == 1
    finally:
        event.remove(browser.db.get_bind(), 'before_cursor_execute', collect)
    assert all('model_snapshot' not in sql and 'context_capsule' not in sql for sql in statements)


def test_detail_workspace_boundary_counts_and_ancestors(browser):
    add(browser, 'root', memory_enabled=True)
    add(browser, 'child', parent_session_id='root')
    add(browser, 'archived', status='archived')
    browser.db.add(ChatSession(id='foreign', workspace_id='other', title='Private'))
    browser.db.flush()
    with pytest.raises(AppError):
        browser.detail('foreign')
    assert [x.id for x in browser.ancestors('child')] == ['root']
    assert browser.counts().model_dump() == {'total': 3, 'active': 2, 'archived': 1, 'memory_enabled': 1}
