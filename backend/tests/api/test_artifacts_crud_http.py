"""产物与分享 CRUD 补测：产物/版本的更新、删除与分享令牌级联撤销。"""
from __future__ import annotations

import io


def _create_artifact(client, headers, name="路线图", description="初版"):
    r = client.post(
        "/api/v1/artifacts",
        headers=headers,
        json={"name": name, "description": description},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _upload_file(client, headers, filename="plan.pdf", content=b"pdf-bytes"):
    r = client.post(
        "/api/v1/files",
        headers=headers,
        files={"file": (filename, io.BytesIO(content), "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _publish_version(client, headers, artifact_id, file_id, notes="v1"):
    r = client.post(
        f"/api/v1/artifacts/{artifact_id}/versions",
        headers=headers,
        json={"file_id": file_id, "release_notes": notes},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _create_share_token(client, headers, version_id):
    r = client.post(
        f"/api/v1/artifacts/versions/{version_id}/share-tokens",
        headers=headers,
        json={"label": "给朋友"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_artifact_update(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    artifact = _create_artifact(client, headers)

    r = client.patch(
        f"/api/v1/artifacts/{artifact['id']}",
        headers=headers,
        json={"name": "新名字", "description": "新描述"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "新名字"
    assert r.json()["description"] == "新描述"

    # 部分更新：只改描述
    r = client.patch(
        f"/api/v1/artifacts/{artifact['id']}",
        headers=headers,
        json={"description": "仅描述"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "新名字"
    assert r.json()["description"] == "仅描述"

    # 列表反映更新
    r = client.get("/api/v1/artifacts", headers=headers)
    assert r.status_code == 200
    row = next(a for a in r.json() if a["id"] == artifact["id"])
    assert row["name"] == "新名字"


def test_artifact_update_validation_and_404(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    artifact = _create_artifact(client, headers)

    r = client.patch(
        f"/api/v1/artifacts/{artifact['id']}",
        headers=headers,
        json={"name": ""},
    )
    assert r.status_code == 422, r.text

    r = client.patch("/api/v1/artifacts/does-not-exist", headers=headers, json={"name": "x"})
    assert r.status_code == 404, r.text

    # 不同工作区不可见
    token2, ws2, _, _ = register_user()
    r = client.patch(
        f"/api/v1/artifacts/{artifact['id']}",
        headers=auth_headers(token2, ws2),
        json={"name": "越权"},
    )
    assert r.status_code == 404, r.text


def test_artifact_delete_revokes_share_tokens(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    artifact = _create_artifact(client, headers)
    version = _publish_version(client, headers, artifact["id"], _upload_file(client, headers))
    created = _create_share_token(client, headers, version["id"])
    raw_token = created["token"]
    token_id = created["id"]

    # 删除前分享可用
    r = client.get(f"/api/v1/artifact-share/{raw_token}")
    assert r.status_code == 200

    r = client.delete(f"/api/v1/artifacts/{artifact['id']}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "deleted"

    # 产物不再出现在列表
    r = client.get("/api/v1/artifacts", headers=headers)
    assert all(a["id"] != artifact["id"] for a in r.json())

    # 分享令牌被级联撤销
    r = client.get(f"/api/v1/artifact-share/{raw_token}")
    assert r.status_code == 404
    r = client.get(
        f"/api/v1/artifacts/versions/{version['id']}/share-tokens", headers=headers
    )
    assert r.status_code == 200, r.text
    revoked = next(t for t in r.json() if t["id"] == token_id)
    assert revoked["revoked_at"] is not None

    # 重复删除返回 404
    r = client.delete(f"/api/v1/artifacts/{artifact['id']}", headers=headers)
    assert r.status_code == 404, r.text

    # 删除后不可再发布版本
    r = client.post(
        f"/api/v1/artifacts/{artifact['id']}/versions",
        headers=headers,
        json={"file_id": _upload_file(client, headers, "b.txt", b"x")},
    )
    assert r.status_code == 404, r.text


def test_version_update_and_delete(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    artifact = _create_artifact(client, headers)
    version = _publish_version(client, headers, artifact["id"], _upload_file(client, headers))

    # 更新版本说明
    r = client.patch(
        f"/api/v1/artifacts/versions/{version['id']}",
        headers=headers,
        json={"release_notes": "修正内容"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["release_notes"] == "修正内容"

    # 删除版本并级联撤销分享
    created = _create_share_token(client, headers, version["id"])
    raw_token = created["token"]
    r = client.delete(f"/api/v1/artifacts/versions/{version['id']}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "deleted"

    r = client.get(f"/api/v1/artifact-share/{raw_token}")
    assert r.status_code == 404

    # 版本列表不再包含已删除版本
    r = client.get(f"/api/v1/artifacts/{artifact['id']}/versions", headers=headers)
    assert r.status_code == 200
    assert all(v["id"] != version["id"] for v in r.json())

    # 重复删除 / 更新已删除版本返回 404
    r = client.delete(f"/api/v1/artifacts/versions/{version['id']}", headers=headers)
    assert r.status_code == 404, r.text
    r = client.patch(
        f"/api/v1/artifacts/versions/{version['id']}",
        headers=headers,
        json={"release_notes": "x"},
    )
    assert r.status_code == 404, r.text
