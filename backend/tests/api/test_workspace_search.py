from uuid import uuid4

import pytest

from app.core.database import SessionLocal
from app.domain.models import ChatSession, FileRecord, FileTextChunk, Message


@pytest.fixture()
def search_data(register_user, auth_headers):
    token, workspace, _, _ = register_user("unified_search")
    with SessionLocal() as db:
        session = ChatSession(workspace_id=workspace, title="操作系统复习")
        db.add(session)
        db.flush()
        message = Message(workspace_id=workspace, session_id=session.id, role="assistant", content="进程调度采用时间片轮转，CPU utilization 80%_ready")
        file = FileRecord(workspace_id=workspace, original_name="复习笔记.txt", object_key=str(uuid4()), size_bytes=80, sha256="a" * 64)
        db.add_all([message, file])
        db.flush()
        db.add(FileTextChunk(workspace_id=workspace, file_id=file.id, ordinal=0, content="时间片轮转是抢占式调度", content_hash="b" * 64))
        db.commit()
        ids = {"session": session.id, "message": message.id, "file": file.id}
    return auth_headers(token, workspace), workspace, ids


def search(client, headers, **params):
    response = client.get("/api/v1/workspace/search", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_titles_messages_files_and_ids(client, search_data):
    headers, _, ids = search_data
    assert search(client, headers, q="操作系统")["items"][0]["id"] == ids["session"]
    items = search(client, headers, q="时间片轮转")["items"]
    assert {hit["kind"] for hit in items} == {"message", "file"}
    assert all("时间片轮转" in hit["snippet"] for hit in items)
    for kind, id in ids.items():
        hit = search(client, headers, q=id)["items"][0]
        assert (hit["kind"], hit["id"], hit["match_field"]) == (kind, id, "id")
    assert search(client, headers, q="CPU")["items"][0]["id"] == ids["message"]
    assert search(client, headers, q="utilization", kind="file")["items"] == []
    assert search(client, headers, q="复习笔记", kind="file")["items"][0]["id"] == ids["file"]


def test_recent_pagination_literal_wildcards_and_validation(client, search_data):
    headers, workspace, ids = search_data
    with SessionLocal() as db:
        db.add_all([ChatSession(workspace_id=workspace, title=f"复习 {i}") for i in range(5)])
        db.commit()
    first = search(client, headers, q="复习", limit=2)
    second = search(client, headers, q="复习", limit=2, offset=first["next_offset"])
    assert first["has_more"] and second["has_more"]
    assert not ({hit["id"] for hit in first["items"]} & {hit["id"] for hit in second["items"]})
    assert all(hit["kind"] == "session" for hit in search(client, headers, q="  ")["items"])
    assert [hit["id"] for hit in search(client, headers, q="%_ready")["items"]] == [ids["message"]]
    assert search(client, headers, q="does-not-exist")["items"] == []
    for params in [{"limit": 0}, {"offset": -1}, {"kind": "unknown"}, {"q": "a" * 241}]:
        assert client.get("/api/v1/workspace/search", headers=headers, params=params).status_code == 422


def test_search_requires_auth_and_isolates_workspaces(client, search_data, register_user, auth_headers):
    headers, workspace, ids = search_data
    token, other_workspace, _, _ = register_user("search_other")
    assert client.get("/api/v1/workspace/search").status_code == 401
    assert client.get("/api/v1/workspace/search", headers=auth_headers(token, workspace)).status_code == 403
    for id in ids.values():
        assert search(client, auth_headers(token, other_workspace), q=id)["items"] == []


def test_acl_filters_messages_and_files_before_pagination(client, search_data, monkeypatch):
    from app.services.authorization import AuthorizationService
    headers, workspace, ids = search_data
    with SessionLocal() as db:
        public = ChatSession(workspace_id=workspace, title="操作系统公开笔记")
        db.add(public)
        db.commit()
        public_id = public.id
    original = AuthorizationService.filter_accessible_ids
    def filtered(self, workspace, resource_type, resource_ids, permission):
        return original(self, workspace, resource_type, resource_ids, permission) - set(ids.values())
    monkeypatch.setattr(AuthorizationService, "filter_accessible_ids", filtered)
    assert search(client, headers, q="操作系统", limit=1)["items"][0]["id"] == public_id
    assert search(client, headers, q="时间片轮转")["items"] == []


def test_deleted_files_and_stale_chunks_are_not_searchable(client, search_data):
    headers, _, ids = search_data
    with SessionLocal() as db:
        file = db.get(FileRecord, ids["file"])
        file.active_revision_id = "new-revision"
        db.commit()
    assert search(client, headers, q="时间片轮转", kind="file")["items"] == []
    with SessionLocal() as db:
        db.get(FileRecord, ids["file"]).lifecycle_status = "deleted"
        db.commit()
    assert search(client, headers, q=ids["file"])["items"] == []


def test_search_hit_older_than_500_messages_can_be_reached(client, search_data, stub_chat_service):
    from datetime import timedelta
    from app.domain.models import utc_now
    headers, workspace, ids = search_data
    with SessionLocal() as db:
        start = utc_now()
        for index in range(560):
            db.add(Message(workspace_id=workspace, session_id=ids["session"], role="user",
                           content=f"后续消息 {index}", created_at=start + timedelta(seconds=index)))
        db.commit()
    assert search(client, headers, q=ids["message"])["items"][0]["id"] == ids["message"]
    before = None
    found = False
    seen = set()
    for _ in range(10):
        response = client.get(f'/api/v1/sessions/{ids["session"]}/messages', headers=headers,
                              params={"limit": 80, **({"before_id": before} if before else {})})
        assert response.status_code == 200, response.text
        page = response.json()
        page_ids = {message["id"] for message in page["items"]}
        assert not (seen & page_ids)
        seen.update(page_ids)
        if ids["message"] in page_ids:
            found = True
            break
        if not page["has_more_before"]:
            break
        before = page["oldest_id"]
    assert found, "An old search hit must remain reachable through message history paging"
