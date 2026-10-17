from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import SecretCipher
from app.services.site_backup import SiteBackupService, _members, read_backup
from app.services.site_restore import restore_offline

PASSWORD = "Backup-only-password-2026!"


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("LEARNGRAPH_"):
            monkeypatch.delenv(name)
    storage = tmp_path / "storage"
    memory = tmp_path / "memory"
    storage.mkdir()
    memory.mkdir()
    (storage / "a").mkdir()
    (storage / "a/lesson.txt").write_bytes(b"lesson contents")
    (memory / "b").mkdir()
    (memory / "b/profile.json").write_text('{"memory":"second workspace"}', encoding="utf-8")
    database = tmp_path / "site.db"
    import hashlib
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE users(id TEXT PRIMARY KEY, password_hash TEXT);
        CREATE TABLE workspaces(id TEXT PRIMARY KEY);
        CREATE TABLE provider_configs(id TEXT PRIMARY KEY, workspace_id TEXT REFERENCES workspaces(id), config TEXT);
        CREATE TABLE provider_secrets(provider_id TEXT PRIMARY KEY REFERENCES provider_configs(id), ciphertext TEXT);
        CREATE TABLE auth_sessions(id TEXT PRIMARY KEY, revoked_at TEXT);
        CREATE TABLE files(object_key TEXT, size_bytes INTEGER, sha256 TEXT);
        CREATE TABLE binary_payloads(id TEXT PRIMARY KEY, payload BLOB);
        INSERT INTO users VALUES ('admin', 'hash-a'), ('member', 'hash-b');
        INSERT INTO workspaces VALUES ('a'), ('b');
        INSERT INTO provider_configs VALUES ('p', 'b', '{"base_url":"http://127.0.0.1/v1","model":"actual-model"}');
        INSERT INTO auth_sessions VALUES ('old-session', NULL);
        """)
        connection.execute("INSERT INTO provider_secrets VALUES (?, ?)", ("p", SecretCipher("source-master").encrypt("sk-fixture-secret")))
        connection.execute("INSERT INTO files VALUES (?, ?, ?)", ("a/lesson.txt", 15, hashlib.sha256(b"lesson contents").hexdigest()))
        connection.execute("INSERT INTO binary_payloads VALUES ('blob', ?)", (b"\x00\xff\xfe",))
        connection.commit()
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database.as_posix()}", storage_root=storage, memory_root=memory, secret_provider="environment", master_key="source-master", max_backup_bytes=8 * 1024 * 1024)
    (tmp_path / "sandbox-runtime.json").write_text('{"image":"fixture-digest"}')
    service = SiteBackupService(SimpleNamespace(scalars=lambda _: []), settings)
    return service, settings, database


def test_full_site_roundtrip_preserves_users_keys_config_binary_and_files(site, tmp_path):
    service, settings, database = site
    payload = service.export(PASSWORD)
    assert b"sk-fixture-secret" not in payload and b"source-master" not in payload
    with ZipFile(BytesIO(payload)) as archive:
        assert set(archive.namelist()) == {"encryption.json", "payload.fernet"}
    members = read_backup(payload, PASSWORD, settings.max_backup_bytes)
    assert members["storage/a/lesson.txt"] == b"lesson contents"
    assert json.loads(members["config-files/sandbox-runtime.json"])["image"] == "fixture-digest"
    assert json.loads(members["configuration.json"])["settings"]["master_key"] == "source-master"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DELETE FROM users")
        connection.execute("UPDATE provider_secrets SET ciphertext = 'changed'")
        connection.commit()
    (settings.storage_root / "unrelated-new-file.txt").write_text("after backup")
    (settings.storage_root / "a/lesson.txt").write_bytes(b"changed")
    env = tmp_path / ".env"
    env.write_text("LEARNGRAPH_MASTER_KEY=wrong\n")
    result = restore_offline(payload, PASSWORD, settings, env)
    assert result["status"] == "restored"
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 2
        cipher = connection.execute("SELECT ciphertext FROM provider_secrets").fetchone()[0]
        assert SecretCipher("source-master").decrypt(cipher) == "sk-fixture-secret"
        assert connection.execute("SELECT payload FROM binary_payloads").fetchone()[0] == b"\x00\xff\xfe"
        assert connection.execute("SELECT revoked_at FROM auth_sessions").fetchone()[0] is not None
    assert (settings.storage_root / "a/lesson.txt").read_bytes() == b"lesson contents"
    assert not (settings.storage_root / "unrelated-new-file.txt").exists()
    restored_settings = Settings(_env_file=env)
    assert restored_settings.master_key == "source-master"
    assert restored_settings.database_url == settings.database_url
    assert restored_settings.storage_root == settings.storage_root
    assert (tmp_path / "memory/b/profile.json").exists()
    assert result["rollback_database"] and result["rollback_configuration"]


def test_wrong_password_and_corruption_do_not_touch_existing_data(site):
    service, settings, database = site
    payload = service.export(PASSWORD)
    before = database.read_bytes()
    with pytest.raises(AppError, match="密码错误"):
        service.stage_restore(payload, "wrong-password")
    assert database.read_bytes() == before
    assert not (database.parent / "site-restore").exists()
    with ZipFile(BytesIO(payload)) as archive:
        envelope = archive.read("encryption.json")
        ciphertext = bytearray(archive.read("payload.fernet"))
    ciphertext[100] ^= 1
    modified = BytesIO()
    with ZipFile(modified, "w") as archive:
        archive.writestr("encryption.json", envelope)
        archive.writestr("payload.fernet", ciphertext)
    with pytest.raises(AppError):
        read_backup(modified.getvalue(), PASSWORD, settings.max_backup_bytes)


def test_restore_upload_only_stages_encrypted_archive(site):
    service, _, database = site
    payload = service.export(PASSWORD)
    before = database.read_bytes()
    result = service.stage_restore(payload, PASSWORD)
    assert result["mode"] == "staged" and result["restart_required"]
    from pathlib import Path
    assert Path(result["restore_path"]).read_bytes() == payload
    assert database.read_bytes() == before


@pytest.mark.parametrize("name", ["../evil", "/absolute", "C:/evil", "storage\\evil", "storage/CON.txt", "storage/foo."])
def test_parser_rejects_unsafe_archive_paths(name):
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr(name.replace("\\", "/"), b"x")
    raw = data.getvalue()
    if "\\" in name:
        raw = raw.replace(name.replace("\\", "/").encode(), name.encode())
    with pytest.raises(AppError):
        _members(raw, 1000)


def test_parser_rejects_zip_bomb_and_duplicate_paths():
    data = BytesIO()
    with ZipFile(data, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("huge", b"x" * 100_000)
    with pytest.raises(AppError) as error:
        _members(data.getvalue(), 1000)
    assert error.value.status_code == 413
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr("file", b"one")
        archive.writestr("FILE", b"two")
    with pytest.raises(AppError):
        _members(data.getvalue(), 1000)


def test_missing_file_and_external_storage_fail_instead_of_partial_backup(site):
    service, settings, _ = site
    (settings.storage_root / "a/lesson.txt").unlink()
    with pytest.raises(AppError) as error:
        service.export(PASSWORD)
    assert error.value.code == "site_backup_file_invalid"
    service.db = SimpleNamespace(scalars=lambda _: [SimpleNamespace(capability="object_storage", provider_kind="minio", locator={})])
    with pytest.raises(AppError) as error:
        service.export(PASSWORD)
    assert error.value.code == "site_backup_storage_unsupported"


def test_failed_restore_rolls_back_database_files_and_configuration(site, tmp_path, monkeypatch):
    service, settings, database = site
    payload = service.export(PASSWORD)
    (settings.storage_root / "a/lesson.txt").write_bytes(b"current data")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("INSERT INTO users VALUES ('later-user', 'hash')")
        connection.commit()
    env = tmp_path / ".env"
    env.write_bytes(b"old configuration")
    from pathlib import Path
    replace = Path.replace
    def fail_config(path, target):
        if Path(target) == env:
            raise OSError("simulated disk failure")
        return replace(path, target)
    monkeypatch.setattr(Path, "replace", fail_config)
    with pytest.raises(OSError, match="disk failure"):
        restore_offline(payload, PASSWORD, settings, env)
    assert (settings.storage_root / "a/lesson.txt").read_bytes() == b"current data"
    assert env.read_bytes() == b"old configuration"
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 3


def test_full_http_export_download_parse_and_stage(site):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.deps import current_principal, get_db, get_settings
    from app.api.routers.migrations import router
    from app.core.errors import install_error_handlers
    from app.core.security import Principal, hash_password
    from uuid import uuid4
    service, settings, _ = site
    admin = SimpleNamespace(is_system_admin=True, status="active", password_hash=hash_password("Current-admin-password!"))
    service.db.get = lambda *_: admin
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    install_error_handlers(app)
    app.dependency_overrides[get_db] = lambda: service.db
    app.dependency_overrides[get_settings] = lambda: settings
    principal = Principal(str(uuid4()), "admin", "tenant", "session", is_system_admin=True)
    app.dependency_overrides[current_principal] = lambda: principal
    with TestClient(app) as client:
        response = client.post("/api/v1/migrations/backup", json={"current_password": "Current-admin-password!", "backup_password": PASSWORD})
        assert response.status_code == 200, response.text
        content = read_backup(response.content, PASSWORD, settings.max_backup_bytes)
        assert content["storage/a/lesson.txt"] == b"lesson contents"
        staged = client.post("/api/v1/migrations/restore", files={"backup": ("site.zip", response.content, "application/zip")}, data={"confirm": "true", "current_password": "Current-admin-password!", "backup_password": PASSWORD})
        assert staged.status_code == 200, staged.text
        assert staged.json()["mode"] == "staged"


def test_environment_conflict_refuses_before_modifying_data(site, tmp_path, monkeypatch):
    service, settings, database = site
    payload = service.export(PASSWORD)
    monkeypatch.setenv("LEARNGRAPH_MASTER_KEY", "different-active-environment")
    original = database.read_bytes()
    with pytest.raises(AppError) as failure:
        restore_offline(payload, PASSWORD, settings, tmp_path / ".env")
    assert failure.value.code == "site_restore_environment_conflict"
    assert database.read_bytes() == original
    assert "different-active-environment" not in failure.value.message


def test_rejects_external_database_and_legacy_workspace_zip(site):
    service, settings, _ = site
    service.settings = settings.model_copy(update={"database_url": "postgresql://example.invalid/db"})
    with pytest.raises(AppError) as failure:
        service.export(PASSWORD)
    assert failure.value.code == "site_backup_database_unsupported"
    legacy = BytesIO()
    with ZipFile(legacy, "w") as archive:
        archive.writestr("manifest.json", '{"format":"learngraph.workspace-export"}')
    with pytest.raises(AppError) as failure:
        read_backup(legacy.getvalue(), PASSWORD, settings.max_backup_bytes)
    assert failure.value.code == "site_backup_format_invalid"
