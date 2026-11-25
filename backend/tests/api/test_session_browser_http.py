from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.domain.models import ChatSession


def test_browser_http_page_detail_summary_and_legacy(client, register_user, auth_headers):
    token, workspace, *_ = register_user('browser')
    headers = auth_headers(token, workspace)
    with SessionLocal() as db:
        for index in range(53):
            db.add(ChatSession(id=f'{workspace[:20]}-{index:03}', workspace_id=workspace, title=f'会话 {index}',
                pinned=index == 4, updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                model_snapshot={'large': 'x' * 2000}, context_capsule={'current_node_id': 'node'}))
        db.commit()
    response = client.get('/api/v1/sessions/page?ungrouped=true', headers=headers)
    assert response.status_code == 200, response.text
    page = response.json()
    assert len(page['items']) == 20 and page['has_more']
    assert page['items'][0]['pinned']
    assert 'model_snapshot' not in page['items'][0] and 'context_capsule' not in page['items'][0]
    second = client.get('/api/v1/sessions/page', params={'ungrouped': 'true', 'cursor': page['next_cursor']}, headers=headers)
    assert second.status_code == 200, second.text
    assert not set(x['id'] for x in second.json()['items']) & set(x['id'] for x in page['items'])
    record_id = page['items'][0]['id']
    detail = client.get(f'/api/v1/sessions/{record_id}', headers=headers)
    assert detail.status_code == 200 and detail.json()['current_node_id'] == 'node'
    assert detail.json()['model_snapshot']['large']
    counts = client.get('/api/v1/sessions/summary', headers=headers)
    assert counts.status_code == 200 and counts.json()['total'] == 53
    legacy = client.get('/api/v1/sessions', headers=headers)
    assert legacy.status_code == 200 and isinstance(legacy.json(), list) and len(legacy.json()) == 53
    assert client.get('/api/v1/sessions/page?limit=101', headers=headers).status_code == 422
    assert client.get('/api/v1/sessions/page?cursor=W10', headers=headers).status_code == 422
    assert client.get('/api/v1/sessions/page').status_code == 401


def test_archived_page_includes_branches_and_excludes_restored_sessions(client, register_user, auth_headers):
    token, workspace, *_ = register_user('browserarchives')
    _, other_workspace, *_ = register_user('browserarchiveother')
    headers = auth_headers(token, workspace)
    with SessionLocal() as db:
        parent = ChatSession(workspace_id=workspace, title='Parent', status='active')
        db.add(parent)
        db.flush()
        branch = ChatSession(workspace_id=workspace, title='Archived branch', status='archived', parent_session_id=parent.id)
        root = ChatSession(workspace_id=workspace, title='Archived root', status='archived')
        db.add_all([branch, root, ChatSession(workspace_id=other_workspace, title='Foreign', status='archived')])
        db.flush()
        branch_id, root_id = branch.id, root.id
        db.commit()

    path = '/api/v1/sessions/page'
    params = {'archived_only': 'true', 'roots_only': 'false', 'limit': 1}
    first = client.get(path, params=params, headers=headers)
    assert first.status_code == 200, first.text
    first_page = first.json()
    assert first_page['has_more']
    second = client.get(path, params={**params, 'cursor': first_page['next_cursor']}, headers=headers)
    assert second.status_code == 200, second.text
    assert not second.json()['has_more']
    assert {row['id'] for row in first_page['items'] + second.json()['items']} == {branch_id, root_id}
    assert client.get(path, params={**params, 'archived_only': 'false', 'cursor': first_page['next_cursor']}, headers=headers).status_code == 422
    assert client.get(f'/api/v1/sessions/{branch_id}', headers=headers).status_code == 200

    restored = client.post(f'/api/v1/sessions/{branch_id}/restore', headers=headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()['status'] == 'active'
    remaining = client.get(path, params=params, headers=headers).json()
    assert [row['id'] for row in remaining['items']] == [root_id]


def test_detail_cannot_cross_workspace(client, register_user, auth_headers):
    token, workspace, *_ = register_user('browserforeign')
    other_token, other_workspace, *_ = register_user('browserother')
    with SessionLocal() as db:
        db.add(ChatSession(id=f'{other_workspace[:20]}-private', workspace_id=other_workspace, title='Private'))
        db.commit()
    assert client.get(f'/api/v1/sessions/{other_workspace[:20]}-private', headers=auth_headers(token, workspace)).status_code == 404
    assert client.get(f'/api/v1/sessions/{other_workspace[:20]}-private', headers=auth_headers(other_token, other_workspace)).status_code == 200


def test_document_concept_branches_restore_only_readable_matches(client, register_user, auth_headers):
    token, workspace, *_ = register_user('documentbranch')
    other_token, other_workspace, *_ = register_user('documentbranchother')
    headers = auth_headers(token, workspace)
    upload = client.post('/api/v1/files', headers=headers, files={'file': ('notes.txt', b'notes', 'text/plain')})
    assert upload.status_code == 201, upload.text
    file_id = upload.json()['id']
    with SessionLocal() as db:
        for ws, kind, status, anchor in [
            (workspace, 'concept_branch', 'active', file_id),
            (workspace, 'concept_branch', 'active', 'another-file'),
            (workspace, 'concept_branch', 'archived', file_id),
            (workspace, 'standalone', 'active', file_id),
            (other_workspace, 'concept_branch', 'active', file_id),
        ]:
            db.add(ChatSession(workspace_id=ws, title='Saved branch', session_kind=kind,
                status=status, context_capsule={'anchor': {'file_id': anchor}}))
        db.commit()
    path = f'/api/v1/sessions/document-concept-branches/{file_id}'
    response = client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    [branch] = response.json()
    assert branch['title'] == 'Saved branch'
    assert branch['context_capsule']['anchor']['file_id'] == file_id
    assert client.get(path, headers=auth_headers(other_token, other_workspace)).status_code == 404
