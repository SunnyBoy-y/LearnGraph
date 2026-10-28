"""Conditional timeline reads preserve auth, pagination and mutation visibility."""
from __future__ import annotations

from datetime import timedelta

from app.core.database import SessionLocal
from app.domain.models import Message, utc_now


def _session(client, headers, title="cached timeline"):
    response = client.post("/api/v1/sessions", headers=headers, json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _messages(workspace_id, session_id, count=4):
    now = utc_now()
    with SessionLocal() as db:
        rows = [
            Message(
                workspace_id=workspace_id,
                session_id=session_id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"message {index}",
                status="completed",
                created_at=now + timedelta(seconds=index),
            )
            for index in range(count)
        ]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def _assert_cache_headers(response):
    assert response.headers["etag"].startswith('"')
    assert response.headers["etag"].endswith('"')
    assert response.headers["cache-control"] == "private, no-cache"
    assert {item.strip().lower() for item in response.headers["vary"].split(",")} >= {
        "authorization", "x-workspace-id",
    }


def test_sessions_revalidate_and_invalidate_after_rename_and_delete(
    client, register_user, auth_headers,
):
    token, workspace, _, _ = register_user("etag_sessions")
    headers = auth_headers(token, workspace)
    session_id = _session(client, headers)
    response = client.get("/api/v1/sessions", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert response.json()[0]["id"] == session_id
    _assert_cache_headers(response)
    initial_etag = response.headers["etag"]

    cached = client.get(
        "/api/v1/sessions", headers={**headers, "If-None-Match": initial_etag},
    )
    assert cached.status_code == 304
    assert cached.content == b""
    assert cached.headers["etag"] == initial_etag
    _assert_cache_headers(cached)

    renamed = client.patch(
        f"/api/v1/sessions/{session_id}", headers=headers, json={"title": "renamed"},
    )
    assert renamed.status_code == 200, renamed.text
    changed = client.get(
        "/api/v1/sessions", headers={**headers, "If-None-Match": initial_etag},
    )
    assert changed.status_code == 200
    assert changed.json()[0]["title"] == "renamed"
    assert changed.headers["etag"] != initial_etag

    deleted = client.post(
        f"/api/v1/sessions/{session_id}/delete", headers=headers,
        json={"confirmation_text": "renamed"},
    )
    assert deleted.status_code == 200, deleted.text
    after_delete = client.get(
        "/api/v1/sessions", headers={**headers, "If-None-Match": changed.headers["etag"]},
    )
    assert after_delete.status_code == 200
    assert after_delete.json() == []
    assert after_delete.headers["etag"] != changed.headers["etag"]


def test_message_revalidation_hashes_content_and_detects_deletion(
    client, register_user, auth_headers,
):
    token, workspace, _, _ = register_user("etag_content")
    headers = auth_headers(token, workspace)
    session_id = _session(client, headers)
    message_ids = _messages(workspace, session_id)
    url = f"/api/v1/sessions/{session_id}/messages"
    initial = client.get(url, headers=headers)
    assert initial.status_code == 200
    assert initial.json()["total_count"] == 4
    _assert_cache_headers(initial)
    cached = client.get(url, headers={**headers, "If-None-Match": initial.headers["etag"]})
    assert cached.status_code == 304 and cached.content == b""
    _assert_cache_headers(cached)

    # A correction can change a message without touching the session timestamp.
    # Hashing only counts or updated_at would silently reuse the stale body.
    with SessionLocal() as db:
        db.get(Message, message_ids[0]).content = "corrected history"
        db.commit()
    edited = client.get(url, headers={**headers, "If-None-Match": initial.headers["etag"]})
    assert edited.status_code == 200
    assert edited.json()["items"][0]["content"] == "corrected history"
    assert edited.headers["etag"] != initial.headers["etag"]

    with SessionLocal() as db:
        db.delete(db.get(Message, message_ids[-1]))
        db.commit()
    deleted = client.get(url, headers={**headers, "If-None-Match": edited.headers["etag"]})
    assert deleted.status_code == 200
    assert deleted.json()["total_count"] == 3
    assert deleted.json()["newest_id"] == message_ids[-2]
    assert deleted.headers["etag"] != edited.headers["etag"]


def test_message_pages_and_detail_mode_have_separate_validators(
    client, register_user, auth_headers,
):
    token, workspace, _, _ = register_user("etag_pages")
    headers = auth_headers(token, workspace)
    session_id = _session(client, headers)
    message_ids = _messages(workspace, session_id)
    url = f"/api/v1/sessions/{session_id}/messages"
    newest = client.get(url, headers=headers, params={"limit": 2})
    assert newest.status_code == 200
    assert [item["id"] for item in newest.json()["items"]] == message_ids[-2:]
    etag = newest.headers["etag"]
    older = client.get(
        url, headers={**headers, "If-None-Match": etag},
        params={"limit": 2, "before_id": message_ids[-2]},
    )
    assert older.status_code == 200
    assert [item["id"] for item in older.json()["items"]] == message_ids[:2]
    assert older.headers["etag"] != etag
    assert client.get(
        url, headers={**headers, "If-None-Match": older.headers["etag"]},
        params={"limit": 2, "before_id": message_ids[-2]},
    ).status_code == 304
    detailed = client.get(
        url, headers={**headers, "If-None-Match": etag},
        params={"limit": 2, "compact": "false"},
    )
    assert detailed.status_code == 200
    assert detailed.headers["etag"] != etag
    invalid = client.get(
        url, headers={**headers, "If-None-Match": "*"},
        params={"limit": 2, "before_id": "missing"},
    )
    assert invalid.status_code == 404


def test_conditionals_require_authorization_before_returning_not_modified(
    client, register_user, auth_headers,
):
    owner_token, owner_workspace, _, _ = register_user("etag_owner")
    other_token, other_workspace, _, _ = register_user("etag_other")
    owner_headers = auth_headers(owner_token, owner_workspace)
    other_headers = auth_headers(other_token, other_workspace)
    session_id = _session(client, owner_headers)
    for url in ("/api/v1/sessions", f"/api/v1/sessions/{session_id}/messages"):
        original = client.get(url, headers=owner_headers)
        assert original.status_code == 200
        validator = original.headers["etag"]
        assert client.get(url, headers={"If-None-Match": validator}).status_code == 401
        forbidden = client.get(
            url, headers={**auth_headers(other_token, owner_workspace), "If-None-Match": "*"},
        )
        assert forbidden.status_code == 403
    missing = client.get(
        f"/api/v1/sessions/{session_id}/messages",
        headers={**other_headers, "If-None-Match": "*"},
    )
    assert missing.status_code == 404


def test_identical_empty_lists_are_scoped_and_accept_weak_etag_lists(
    client, register_user, auth_headers,
):
    token, workspace, _, _ = register_user("etag_empty_a")
    other_token, other_workspace, _, _ = register_user("etag_empty_b")
    headers = auth_headers(token, workspace)
    first = client.get("/api/v1/sessions", headers=headers)
    assert first.json() == []
    etag = first.headers["etag"]
    other = client.get(
        "/api/v1/sessions",
        headers={**auth_headers(other_token, other_workspace), "If-None-Match": etag},
    )
    assert other.status_code == 200 and other.json() == []
    assert other.headers["etag"] != etag
    weak = client.get(
        "/api/v1/sessions", headers={**headers, "If-None-Match": f'"old", W/{etag}'},
    )
    assert weak.status_code == 304 and weak.content == b""
    assert client.get(
        "/api/v1/sessions", headers={**headers, "If-None-Match": "*"},
    ).status_code == 304
