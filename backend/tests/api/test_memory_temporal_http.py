"""HTTP temporal CRUD and occurrence filtering, independent from wall-clock time."""
from datetime import datetime, timezone


def test_temporal_crud_preserves_expression_anchor_and_revision(client, register_user, auth_headers, monkeypatch):
    from app.services import memory

    monkeypatch.setattr(memory, "utc_now", lambda: datetime(2026, 11, 24, 2, tzinfo=timezone.utc))
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    response = client.post("/api/v1/memory", headers=headers, json={
        "title": "完成索引实验", "content": "完成了数据库索引实验", "time_expression": "  昨天  ",
        "timezone_name": "Asia/Shanghai", "structured_payload": {"temporal_status": "completed"},
    })
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["temporal_semantics"]["original_expression"] == "  昨天  "
    assert datetime.fromisoformat(created["event_at"]) == datetime(2026, 11, 22, 16, tzinfo=timezone.utc)
    assert created["valid_until"] is None  # A past fact is not an expiring plan.
    mid = created["id"]
    monkeypatch.setattr(memory, "utc_now", lambda: datetime(2026, 11, 28, 2, tzinfo=timezone.utc))
    response = client.patch(f"/api/v1/memory/{mid}", headers=headers, json={"title": "索引实验完成"})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["event_at"] == created["event_at"]
    assert updated["temporal_semantics"] == created["temporal_semantics"]
    response = client.patch(f"/api/v1/memory/{mid}", headers=headers, json={"timezone_name": "America/Los_Angeles"})
    assert response.status_code == 200, response.text
    assert response.json()["temporal_semantics"]["observed_at"] == created["temporal_semantics"]["observed_at"]
    assert datetime.fromisoformat(response.json()["event_at"]) == datetime(2026, 11, 22, 8, tzinfo=timezone.utc)
    response = client.patch(f"/api/v1/memory/{mid}", headers=headers, json={"expected_revision": 1, "time_expression": "前天"})
    assert response.status_code == 409, response.text
    response = client.patch(f"/api/v1/memory/{mid}", headers=headers, json={"time_expression": "前天"})
    assert response.status_code == 200, response.text
    assert datetime.fromisoformat(response.json()["temporal_semantics"]["observed_at"]) == datetime(2026, 11, 28, 2, tzinfo=timezone.utc)
    response = client.patch(f"/api/v1/memory/{mid}", headers=headers, json={"time_expression": None})
    assert response.status_code == 200, response.text
    assert response.json()["temporal_semantics"] is None
    assert response.json()["event_at"] is None


def test_timeline_and_views_filter_occurrence_not_last_update(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    ids = []
    for day in ("2026年10月18日", "2026年10月19日"):
        response = client.post("/api/v1/memory", headers=headers, json={"title": day, "content": "完成索引实验", "time_expression": day})
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
    params = {"time_query": "2026年10月18日", "timezone_name": "Asia/Shanghai"}
    response = client.get("/api/v1/memory/timeline", headers=headers, params=params)
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["memories"]] == ids[:1]
    assert response.json()["episodes"] == []
    response = client.get("/api/v1/memory/views", headers=headers, params={"start_at": "2026-10-18T00:00:00+08:00", "end_at": "2026-10-19T00:00:00+08:00"})
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()] == ids[:1]
    other_token, other_ws, _, _ = register_user("temporal-other")
    response = client.get("/api/v1/memory/timeline", headers=auth_headers(other_token, other_ws), params=params)
    assert response.status_code == 200, response.text
    assert response.json()["memories"] == []
    response = client.get("/api/v1/memory/timeline", headers=headers, params={"time_query": "2020年1月1日"})
    assert response.json()["memories"] == []


def test_timeline_rejects_missing_invalid_or_reversed_window(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    for params in ({}, {"time_query": "not-a-date"}, {"start_at": "2026-10-20T00:00:00Z", "end_at": "2026-10-18T00:00:00Z"}):
        response = client.get("/api/v1/memory/timeline", headers=headers, params=params)
        assert response.status_code == 422, response.text
