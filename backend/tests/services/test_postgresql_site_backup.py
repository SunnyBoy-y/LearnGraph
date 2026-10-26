"""Opt-in native client + PostgreSQL full-site recovery acceptance."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.database_routing import atomic_json, encrypt_url, postgres_engine, startup_url
from app.core.errors import AppError
from app.core.security import SecretCipher
from app.services.site_backup import SiteBackupService, read_backup
from app.services.site_restore import restore_offline

PASSWORD = "PostgreSQL-backup-test-only-2026!"


@pytest.fixture
def site(tmp_path, monkeypatch, request):
    value = os.environ.get("LEARNGRAPH_TEST_POSTGRES_URL")
    if not value:
        pytest.skip("requires disposable PostgreSQL and pg_dump/pg_restore/psql 18")
    from docker_test_safety import endpoint
    endpoint(value, "LEARNGRAPH_TEST_POSTGRES_CONTAINER", postgres=True)
    parsed = make_url(value)
    for key in list(os.environ):
        if key.startswith("LEARNGRAPH_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    base = postgres_engine(value)
    schema = "lgsite_" + uuid.uuid4().hex
    with base.begin() as db:
        db.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    url = parsed.update_query_dict({"options": f"-csearch_path={schema},public"}).render_as_string(hide_password=False)
    engine = postgres_engine(url)
    def cleanup():
        engine.dispose()
        with base.begin() as db:
            db.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        base.dispose()
    request.addfinalizer(cleanup)
    storage, memory = tmp_path / "storage", tmp_path / "memory"
    storage.mkdir(); memory.mkdir()
    (storage / "lesson.txt").write_bytes(b"lesson contents")
    (memory / "memory.txt").write_text("恢复记忆", encoding="utf-8")
    settings = Settings(_env_file=None, database_url=url, storage_root=storage, memory_root=memory,
        database_migration_state_dir=str(tmp_path / "migration"), secret_provider="environment", master_key="site-backup-test-key", max_backup_bytes=16*1024*1024)
    with engine.begin() as db:
        for statement in ["CREATE TABLE users(id TEXT PRIMARY KEY,password_hash TEXT)", "CREATE TABLE workspaces(id TEXT PRIMARY KEY)",
            "CREATE TABLE provider_configs(id TEXT PRIMARY KEY,workspace_id TEXT REFERENCES workspaces(id),config JSON)",
            "CREATE TABLE provider_secrets(provider_id TEXT PRIMARY KEY REFERENCES provider_configs(id),ciphertext TEXT)",
            "CREATE TABLE auth_sessions(id TEXT PRIMARY KEY,revoked_at TIMESTAMPTZ)",
            "CREATE TABLE files(object_key TEXT,size_bytes BIGINT,sha256 TEXT)",
            "CREATE TABLE binary_payloads(id TEXT PRIMARY KEY,payload BYTEA,at TIMESTAMPTZ,details JSON,money BIGINT)",
            "CREATE TABLE _lg_database_activation(singleton INTEGER PRIMARY KEY,job_id TEXT)",
            "CREATE TABLE _lg_database_copy(job_id TEXT PRIMARY KEY,applied_commit BIGINT)",
            "INSERT INTO users VALUES('admin','hash-a'),('member','hash-b')", "INSERT INTO workspaces VALUES('a'),('b')",
            "INSERT INTO provider_configs VALUES('p','b','{\"key\":null,\"duplicate\":1,\"duplicate\":2}')",
            "INSERT INTO auth_sessions VALUES('before',NULL)", "INSERT INTO _lg_database_activation VALUES(1,'fixture-activation')", "INSERT INTO _lg_database_copy VALUES('fixture-activation',7)"]:
            db.exec_driver_sql(statement)
        db.execute(text("INSERT INTO provider_secrets VALUES('p',:secret)"), {"secret": SecretCipher(settings.master_key).encrypt("provider-key-test-only")})
        db.execute(text("INSERT INTO files VALUES('lesson.txt',15,:hash)"), {"hash": hashlib.sha256(b"lesson contents").hexdigest()})
        db.execute(text("INSERT INTO binary_payloads VALUES('blob',:value,'2026-10-18T01:02:03.123456Z',CAST(:details AS json),9007199254740993)"), {"value": b"\x00\xff\xfe", "details": '{"a":null}'})
    atomic_json(Path(settings.database_migration_state_dir) / "active.json", {"phase": "postgresql_active", "job_id": "fixture-activation", "final_commit": 7, **encrypt_url(settings, url)})
    service = SiteBackupService(SimpleNamespace(scalars=lambda _: []), settings)
    yield service, settings, engine


def test_native_full_site_restore_preserves_facts_files_and_pg_identity(site, tmp_path):
    service, settings, engine = site
    payload = service.export(PASSWORD)
    assert b"provider-key-test-only" not in payload and settings.master_key.encode() not in payload
    members = read_backup(payload, PASSWORD, settings.max_backup_bytes)
    assert "database.postgresql.dump" in members and "database.sqlite3" not in members
    assert service.stage_restore(payload, PASSWORD)["mode"] == "staged"
    with engine.begin() as db:
        db.exec_driver_sql("UPDATE users SET password_hash='changed'")
        db.exec_driver_sql("UPDATE _lg_database_activation SET job_id='current-target-activation'")
        db.exec_driver_sql("CREATE TABLE after_backup(extra TEXT)")
        db.exec_driver_sql("INSERT INTO after_backup VALUES('must disappear')")
    active_path = Path(settings.database_migration_state_dir) / "active.json"
    atomic_json(active_path, {**json.loads(active_path.read_text()), "job_id": "current-target-activation", "final_commit": 9})
    (settings.storage_root / "lesson.txt").write_bytes(b"changed")
    (settings.memory_root / "memory.txt").write_text("changed")
    result = restore_offline(payload, PASSWORD, settings, tmp_path / ".env")
    assert result["status"] == "restored" and Path(result["rollback_database"]).exists()
    engine.dispose()
    with engine.connect() as db:
        assert db.exec_driver_sql("SELECT password_hash FROM users WHERE id='admin'").scalar_one() == "hash-a"
        assert SecretCipher(settings.master_key).decrypt(db.exec_driver_sql("SELECT ciphertext FROM provider_secrets").scalar_one()) == "provider-key-test-only"
        assert db.exec_driver_sql("SELECT payload,money FROM binary_payloads").one() == (b"\x00\xff\xfe", 9007199254740993)
        assert db.exec_driver_sql("SELECT revoked_at FROM auth_sessions").scalar_one() is not None
        assert db.exec_driver_sql("SELECT to_regclass('after_backup')").scalar_one() is None
        assert db.exec_driver_sql("SELECT job_id FROM _lg_database_activation").scalar_one() == "current-target-activation"
        assert db.exec_driver_sql("SELECT applied_commit FROM _lg_database_copy WHERE job_id='current-target-activation'").scalar_one() == 9
    assert (settings.storage_root / "lesson.txt").read_bytes() == b"lesson contents"
    assert (settings.memory_root / "memory.txt").read_text() == "恢复记忆"
    assert startup_url(settings).startswith("postgresql")


def test_failed_pre_journal_snapshot_removes_only_its_partial_recovery(site, tmp_path, monkeypatch):
    service, settings, engine = site
    payload = service.export(PASSWORD)
    import app.services.site_restore_postgresql as recovery
    def disk_failure(url, path, **kwargs):
        path.write_bytes(b'partial native dump')
        raise OSError('simulated full recovery disk')
    monkeypatch.setattr(recovery,'snapshot',disk_failure)
    with pytest.raises(OSError,match='full recovery disk'):
        restore_offline(payload,PASSWORD,settings,tmp_path/'.env')
    root = Path(settings.database_migration_state_dir)
    assert not list((root/'site-restore-recovery').iterdir())
    assert not (root/'site-restore.json').exists()
    with engine.connect() as db:
        assert db.exec_driver_sql("SELECT password_hash FROM users WHERE id='admin'").scalar_one()=='hash-a'


def test_native_sql_expansion_limit_does_not_change_database(site, tmp_path, monkeypatch):
    service, settings, engine = site
    payload = service.export(PASSWORD)
    import app.services.site_restore_postgresql as recovery
    original = recovery.restore
    def bounded(*args, **kwargs):
        return original(*args, **{**kwargs, 'max_sql_bytes': 16})
    monkeypatch.setattr(recovery,'restore',bounded)
    (settings.storage_root/'lesson.txt').write_bytes(b'current file')
    with pytest.raises(AppError) as failure:
        restore_offline(payload,PASSWORD,settings,tmp_path/'.env')
    assert failure.value.code == 'site_restore_sql_too_large'
    assert (settings.storage_root/'lesson.txt').read_bytes()==b'current file'
    with engine.connect() as db:
        assert db.exec_driver_sql("SELECT password_hash FROM users WHERE id='admin'").scalar_one()=='hash-a'


def test_database_only_snapshot_keeps_file_rows_without_building_file_inventory(site,tmp_path):
    service,settings,engine=site
    from app.services.postgresql_backup import snapshot,effective_url
    from app.services.site_backup import validate_backup_database
    archive=tmp_path/'database-only.dump'
    metadata=snapshot(effective_url(settings),archive,include_files=False)
    assert metadata['files']==[] and metadata['tables']['files'][0]==1
    # A database-only updater artifact must never masquerade as a complete site
    # backup that also promises to restore every referenced object file.
    with pytest.raises(AppError) as failure:
        validate_backup_database({'database.postgresql.dump':archive.read_bytes(),'database-metadata.json':json.dumps(metadata).encode()})
    assert failure.value.code=='site_backup_file_invalid'


def test_failed_row_verification_rolls_back_database_files_and_configuration(site, tmp_path, monkeypatch):
    from app.services import site_restore_postgresql as recovery
    service, settings, engine = site
    payload = service.export(PASSWORD)
    with engine.begin() as db:
        db.exec_driver_sql("UPDATE users SET password_hash='keep-current'")
    (settings.storage_root / "lesson.txt").write_bytes(b"keep-current")
    env = tmp_path / ".env"
    env.write_text("before-restore")
    original = recovery.restore
    def damaged(url, archive, metadata, **kwargs):
        metadata["tables"]["users"][0] += 1
        return original(url, archive, metadata, **kwargs)
    monkeypatch.setattr(recovery, "restore", damaged)
    with pytest.raises(AppError):
        restore_offline(payload, PASSWORD, settings, env)
    engine.dispose()
    with engine.connect() as db:
        assert db.exec_driver_sql("SELECT password_hash FROM users WHERE id='admin'").scalar_one() == "keep-current"
    assert env.read_text() == "before-restore"
    assert (settings.storage_root / "lesson.txt").read_bytes() == b"keep-current"
    assert json.loads((Path(settings.database_migration_state_dir) / "site-restore.json").read_text())["state"] == "ROLLED_BACK"
    assert startup_url(settings).startswith("postgresql")


def test_file_managed_target_key_is_restored_and_current_pg_route_reencrypted(site, tmp_path, monkeypatch):
    service, settings, engine = site
    payload = service.export(PASSWORD)
    old_key = settings.master_key
    current = settings.model_copy(update={"master_key": "new-target-generated-key"})
    key_file = settings.storage_root.parent / ".master-key"
    key_file.write_text(current.master_key)
    active_path = Path(settings.database_migration_state_dir) / "active.json"
    atomic_json(active_path, {**json.loads(active_path.read_text()), **encrypt_url(current, settings.database_url)})
    monkeypatch.setenv("LEARNGRAPH_MASTER_KEY", current.master_key)
    monkeypatch.setenv("_LEARNGRAPH_MASTER_KEY_FROM_FILE", "1")
    result = restore_offline(payload, PASSWORD, current, tmp_path / ".env")
    assert result["status"] == "restored" and key_file.read_text() == old_key
    restarted = current.model_copy(update={"master_key": key_file.read_text()})
    assert startup_url(restarted).startswith("postgresql")
    with engine.connect() as db:
        assert SecretCipher(old_key).decrypt(db.exec_driver_sql("SELECT ciphertext FROM provider_secrets").scalar_one()) == "provider-key-test-only"


def test_wrong_password_mixed_database_and_interrupted_restore_are_blocked(site, tmp_path):
    service, settings, engine = site
    payload = service.export(PASSWORD)
    with pytest.raises(AppError, match="密码错误"):
        service.stage_restore(payload, "wrong")
    from app.services.site_backup import ensure_restore_target
    with pytest.raises(AppError, match="禁止恢复 SQLite"):
        ensure_restore_target({"database.sqlite3": b"not executed"}, settings)
    atomic_json(Path(settings.database_migration_state_dir) / "site-restore.json", {"state": "RESTORING"})
    with pytest.raises(Exception, match="interrupted"):
        startup_url(settings)


@pytest.mark.parametrize("after_database_commit", [False, True])
def test_process_loss_recovers_by_database_commit_marker(site, tmp_path, monkeypatch, after_database_commit):
    from app.services import site_restore_postgresql as recovery
    service, settings, engine = site
    payload = service.export(PASSWORD)
    with engine.begin() as db:
        db.exec_driver_sql("UPDATE users SET password_hash='current-before-crash'")
    (settings.storage_root / "lesson.txt").write_bytes(b"current-before-crash")
    def process_loss(*_args, **_kwargs):
        raise SystemExit("simulated process loss")
    with monkeypatch.context() as patcher:
        patcher.setattr(recovery, "recover_offline", process_loss)
        if after_database_commit:
            original = recovery.atomic_json
            def fail_completion(path, value):
                if value.get("state") == "COMPLETED":
                    process_loss()
                return original(path, value)
            patcher.setattr(recovery, "atomic_json", fail_completion)
        else:
            patcher.setattr(recovery, "restore", process_loss)
        with pytest.raises(SystemExit):
            restore_offline(payload, PASSWORD, settings, tmp_path / ".env")
    with pytest.raises(Exception, match="interrupted"):
        startup_url(settings)
    result = recovery.recover_offline(settings)
    assert result["status"] == ("restored" if after_database_commit else "rolled_back")
    engine.dispose()
    with engine.connect() as db:
        assert db.exec_driver_sql("SELECT password_hash FROM users WHERE id='admin'").scalar_one() == ("hash-a" if after_database_commit else "current-before-crash")
    assert (settings.storage_root / "lesson.txt").read_bytes() == (b"lesson contents" if after_database_commit else b"current-before-crash")
    assert startup_url(settings).startswith("postgresql")
