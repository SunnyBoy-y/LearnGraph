"""Private host-updater CLI. HTTP entry points remain system-admin-only."""
from __future__ import annotations

import argparse
import hashlib
import json
from contextlib import closing
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
from zipfile import ZIP_STORED, ZipFile

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.database_routing import OperationLease, atomic_json, postgres_engine, read_json, state_root
from app.core.errors import AppError
from app.core.secret_store import secret_store_from_settings
from app.services.postgresql_backup import effective_url, snapshot

CAPABILITY = "sqlite-postgresql-v1"


def ensure_available(settings, *, owner=None) -> None:
    root = state_root(settings)
    job, active = read_json(root / "job.json"), read_json(root / "active.json")
    update = read_json(root / "update.json")
    pending = read_json(root / "site-restore.json")
    if (job and job.get("state") not in {"COMPLETED", "CANCELLED"}
        or active and active.get("phase") != "postgresql_active"
        or pending and pending.get("state") not in {"COMPLETED", "ROLLED_BACK"}
        or update and update.get("id") != owner):
        raise AppError(409, "database_maintenance_busy", "主业务库迁移、恢复或更新尚未结束")


def status(settings) -> dict:
    url = effective_url(settings)
    parsed = make_url(url)
    engine = parsed.get_backend_name()
    active = read_json(state_root(settings) / "active.json")
    identity = hashlib.sha256(parsed.set(password=None).render_as_string(hide_password=False).encode()).hexdigest()
    return {"engine": engine, "activation_id": active.get("job_id") if active else None, "identity": identity, "capability": CAPABILITY}


def reserve(settings, owner: str, *, release=False) -> None:
    path = state_root(settings) / "update.json"
    current = read_json(path)
    if release:
        if current and current.get("id") == owner:
            path.unlink()
        return
    ensure_available(settings, owner=owner)
    atomic_json(path, {"id": owner})


def backup(settings, destination: Path, *, owner=None) -> dict:
    ensure_available(settings, owner=owner)
    url = effective_url(settings)
    info = status(settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise AppError(409, "database_backup_exists", "备份目标已存在，禁止覆盖")
    pending = destination.with_name(destination.name + ".partial")
    try:
        from app.services.backup_io import require_space
        from app.services.postgresql_backup import database_bytes
        estimate = database_bytes(url) if info["engine"] == "postgresql" else Path(make_url(url).database).stat().st_size
        require_space(destination.parent, estimate * 3)
        workspace=state_root(settings)/'maintenance-work'
        workspace.mkdir(parents=True,mode=0o700,exist_ok=True)
        workspace.chmod(0o700)
        require_space(workspace,estimate)
        with TemporaryDirectory(prefix="lg-update-snapshot-", dir=workspace) as temporary:
            database = Path(temporary) / "database"
            if info["engine"] == "postgresql":
                metadata = snapshot(url, database, include_files=False)
                database_name = "database.postgresql.dump"
            elif info["engine"] == "sqlite":
                source = Path(make_url(url).database).resolve()
                with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as live, closing(sqlite3.connect(database)) as copy:
                    live.backup(copy)
                    if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or copy.execute("PRAGMA foreign_key_check").fetchone():
                        raise AppError(422, "database_backup_invalid", "SQLite 备份校验失败")
                metadata, database_name = {"engine": "sqlite"}, "database.sqlite3"
            else:
                raise AppError(409, "database_backup_unsupported", "不支持的主数据库")
            store = secret_store_from_settings(settings)
            key = store.active_key(create=False)
            keys = {str(version): store.key(version).secret for version in range(1, key.version + 1)} if store.provider_name == "keyring" else {str(key.version): key.secret}
            recovery = {"settings": settings.model_dump(mode="json"), "active": read_json(state_root(settings) / "active.json"), "keys": keys}
            # This is a host-private pre-update DATABASE artifact. Files remain
            # on unchanged persistent volumes; user downloads use the encrypted
            # full-site format instead. Never expose this artifact via HTTP.
            with pending.open("xb") as output:
                pending.chmod(0o600)
                with ZipFile(output, "w", compression=ZIP_STORED) as archive:
                    archive.write(database, database_name)
                    archive.writestr("database-metadata.json", json.dumps(metadata))
                    archive.writestr("recovery.json", json.dumps(recovery))
                output.flush()
                import os
                os.fsync(output.fileno())
            with ZipFile(pending) as archive:
                if archive.testzip() is not None:
                    raise AppError(422, "database_backup_invalid", "备份传输校验失败")
            pending.replace(destination)
        digest = hashlib.sha256()
        with destination.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        return {**info, "sha256": digest.hexdigest(), "bytes": destination.stat().st_size, "snapshot_workspace": str(workspace)}
    finally:
        pending.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="主业务数据库更新备份与互锁（宿主更新器使用）")
    parser.add_argument("operation", choices=["status", "backup", "reserve", "release"])
    parser.add_argument("--owner")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.owner is not None and not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", args.owner):
        parser.error("Invalid operation owner")
    settings = Settings()
    try:
        if args.operation == "status":
            result = status(settings)
        else:
            lease = OperationLease(state_root(settings))
            try:
                if args.operation == "backup":
                    if args.output is None:
                        parser.error("--output required")
                    result = backup(settings, args.output.resolve(), owner=args.owner)
                else:
                    if args.owner is None:
                        parser.error("--owner required")
                    reserve(settings, args.owner, release=args.operation == "release")
                    result = {"status": "released" if args.operation == "release" else "reserved"}
            finally:
                lease.close()
    except (AppError, BlockingIOError) as exc:
        parser.exit(1, (exc.message if isinstance(exc, AppError) else "Database operation is busy") + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
