from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image
import pytest


def _png(size=(480, 240)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "#449988").save(output, format="PNG")
    return output.getvalue()


def _avatar_image(url: str) -> Image.Image:
    assert url.startswith("data:image/png;base64,")
    return Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1])))


def test_profile_rename_persists_across_login_without_changing_identity(client, register_user, auth_headers):
    token, workspace_id, username, password = register_user("profile_name")
    headers = auth_headers(token)
    before = client.get("/api/v1/auth/me", headers=headers).json()
    response = client.patch("/api/v1/auth/profile", headers=headers, json={"display_name": "  小林同学  "})
    assert response.status_code == 200, response.text
    assert response.json() == {"id": before["id"], "display_name": "小林同学", "avatar_url": None}
    after = client.get("/api/v1/auth/me", headers=headers).json()
    assert after["display_name"] == "小林同学"
    assert after["username"] == username
    login = client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()
    assert login["display_name"] == "小林同学"
    assert login["default_workspace_id"] == workspace_id


@pytest.mark.parametrize("name", ["", "   \n\t", "名" * 161, None])
def test_profile_rejects_invalid_names(client, register_user, auth_headers, name):
    token, _, _, _ = register_user("profile_invalid")
    headers = auth_headers(token)
    before = client.get("/api/v1/auth/me", headers=headers).json()["display_name"]
    response = client.patch("/api/v1/auth/profile", headers=headers, json={"display_name": name})
    assert response.status_code == 422
    assert client.get("/api/v1/auth/me", headers=headers).json()["display_name"] == before


def test_avatar_upload_and_generation_persist_and_isolate_accounts(client, register_user, auth_headers):
    token, _, username, password = register_user("profile_avatar")
    other_token, other_workspace, _, _ = register_user("profile_other")
    # A workspace header cannot select whose profile is edited.
    headers = auth_headers(token, other_workspace)
    response = client.post("/api/v1/auth/profile/avatar", headers=headers, files={"file": ("photo.png", _png(), "image/png")})
    assert response.status_code == 200, response.text
    uploaded = response.json()["avatar_url"]
    image = _avatar_image(uploaded)
    assert image.size == (256, 256)
    assert image.getpixel((128, 128))[:3] == (68, 153, 136)
    assert client.get("/api/v1/auth/me", headers=headers).json()["avatar_url"] == uploaded
    login = client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()
    assert login["avatar_url"] == uploaded
    generated = client.post("/api/v1/auth/profile/avatar/default", headers=headers)
    assert generated.status_code == 200, generated.text
    avatar = generated.json()["avatar_url"]
    assert avatar is None  # Restore the original color + initial fallback.
    assert client.get("/api/v1/auth/me", headers=auth_headers(login["access_token"])).json()["avatar_url"] == avatar
    assert client.get("/api/v1/auth/me", headers=auth_headers(other_token)).json()["avatar_url"] is None
    renamed = client.patch("/api/v1/auth/profile", headers=headers, json={"display_name": "新名字"})
    assert renamed.json()["avatar_url"] is None
    relogin = client.post("/api/v1/auth/login", json={"username": username, "password": password}).json()
    assert relogin["avatar_url"] is None
    assert relogin["display_name"] == "新名字"


def test_invalid_avatar_does_not_replace_saved_avatar(client, register_user, auth_headers):
    token, _, _, _ = register_user("profile_bad_image")
    headers = auth_headers(token)
    avatar = client.post("/api/v1/auth/profile/avatar", headers=headers, files={"file": ("photo.png", _png(), "image/png")}).json()["avatar_url"]
    for content, status in [(b"not an image", 422), (b"x" * (5 * 1024 * 1024 + 1), 413), (_png((4001, 4000)), 422)]:
        response = client.post("/api/v1/auth/profile/avatar", headers=headers, files={"file": ("fake.png", content, "image/png")})
        assert response.status_code == status, response.text
        assert client.get("/api/v1/auth/me", headers=headers).json()["avatar_url"] == avatar


@pytest.mark.parametrize("path,method", [("/auth/profile", "patch"), ("/auth/profile/avatar", "post"), ("/auth/profile/avatar/default", "post")])
def test_profile_requires_login(client, path, method):
    response = getattr(client, method)("/api/v1" + path, json={"display_name": "匿名"})
    assert response.status_code == 401


def test_user_avatar_migration_is_additive_and_repeatable():
    from sqlalchemy import create_engine, inspect
    from app.core.migrations import _user_avatar

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id VARCHAR(64) PRIMARY KEY, display_name VARCHAR(160))")
        connection.exec_driver_sql("INSERT INTO users VALUES ('legacy', '旧用户')")
        _user_avatar(connection)
        _user_avatar(connection)
        assert "avatar_url" in {column["name"] for column in inspect(connection).get_columns("users")}
        assert connection.exec_driver_sql("SELECT display_name, avatar_url FROM users").one() == ("旧用户", None)
    engine.dispose()
