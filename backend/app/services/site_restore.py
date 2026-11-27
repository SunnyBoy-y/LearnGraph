"""Offline recovery CLI: python -m app.services.site_restore --backup PATH --server-stopped.

Run from backend/ with the API, workers and schedulers stopped. Passwords are
read from the terminal, never from argv or environment variables.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.core.config import Settings
from app.core.errors import AppError
from app.core.secret_store import KeyringSecretStore
from app.services.site_backup import RUNTIME_FILES, database_path, managed_roots, read_backup
from app.services.backup_io import copy_member, work_root, require_space, small_bytes


def _unlink_sqlite_sidecars(path: Path) -> None:
    """Remove SQLite WAL/SHM sidecar files so stale frames never shadow a restored DB."""
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)


def _copy_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _unlink_sqlite_sidecars(target)
    with (
        closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=5)) as src,
        closing(sqlite3.connect(target, timeout=5)) as dst,
    ):
        src.backup(dst, pages=256, sleep=0.02)
    _unlink_sqlite_sidecars(target)


def recover_sqlite_offline(settings: Settings) -> dict:
    """Roll back an interrupted offline SQLite site restore using its durable journal."""
    from app.core.database_routing import atomic_json, read_json, state_root

    journal_path = state_root(settings) / "site-restore.json"
    journal = read_json(journal_path)
    if not journal or journal.get("state") in {"COMPLETED", "ROLLED_BACK"}:
        return {"status": "no_pending_restore"}
    if journal.get("engine") != "sqlite":
        from app.services.site_restore_postgresql import recover_offline as recover_pg_offline

        return recover_pg_offline(settings)

    recovery_id = journal["id"]
    database = Path(journal["database"])
    database_before = Path(journal["database_before"])
    if journal.get("had_database"):
        if database_before.exists():
            _copy_database(database_before, database)
    else:
        database.unlink(missing_ok=True)
        _unlink_sqlite_sidecars(database)

    env_file = Path(journal["env_file"])
    env_before = Path(journal["env_before"])
    if journal.get("had_env"):
        if env_before.exists():
            env_file.parent.mkdir(parents=True, exist_ok=True)
            env_file.write_bytes(small_bytes(env_before))
    else:
        env_file.unlink(missing_ok=True)

    for item in reversed(journal.get("external", [])):
        target = Path(item["path"])
        backup_path = Path(item["backup"]) if item.get("backup") else None
        if item.get("existed"):
            if backup_path is not None and backup_path.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(small_bytes(backup_path))
                os.chmod(target, 0o600)
        else:
            target.unlink(missing_ok=True)

    for item in reversed(journal.get("roots", [])):
        root = Path(item["path"])
        previous = Path(item["previous"])
        if previous.exists():
            if root.exists():
                root.rename(root.with_name(root.name + ".failed-site-restore-" + recovery_id))
            previous.rename(root)
        elif not item.get("existed") and root.exists():
            root.rename(root.with_name(root.name + ".failed-site-restore-" + recovery_id))

    atomic_json(journal_path, {**journal, "state": "ROLLED_BACK"})
    return {"status": "rolled_back", "restart_required": True}


def restore_offline(payload: bytes, password: str, settings: Settings, env_file: Path) -> dict:
    from app.core.database_routing import OperationLease, state_root
    try:
        lease = OperationLease(state_root(settings))
    except BlockingIOError as exc:
        raise AppError(409, "site_restore_busy", "迁移或另一项恢复正在执行") from exc
    try:
        from app.services.database_maintenance import ensure_available
        ensure_available(settings)
        return _restore_offline(payload, password, settings, env_file)
    finally:
        lease.close()


def _restore_offline(payload: bytes, password: str, settings: Settings, env_file: Path) -> dict:
    from app.services.site_backup import backup_database_kind, ensure_restore_target
    with read_backup(payload, password, settings.max_backup_bytes, directory=work_root(settings)) as members:
        return _restore_validated(members, settings, env_file)


def _restore_validated(members, settings: Settings, env_file: Path) -> dict:
    from app.services.site_backup import backup_database_kind, ensure_restore_target
    if json.loads(members["manifest.json"])["app_version"] != settings.app_version:
        raise AppError(409, "site_restore_version_mismatch", "请使用与备份相同版本的 LearnGraph 执行全站恢复")
    url = ensure_restore_target(members, settings)
    if backup_database_kind(members) == "postgresql":
        from app.services.site_restore_postgresql import restore_site
        return restore_site(members, settings, env_file, url)
    from app.core.database_routing import atomic_json, read_json, source_is_sealed, state_root
    from sqlalchemy.engine import make_url
    source_url = make_url(settings.database_url)
    migration_job = read_json(state_root(settings) / "job.json")
    if (read_json(state_root(settings) / "active.json") or source_url.get_backend_name() != "sqlite"
        or migration_job and migration_job.get("state") not in {"CANCELLED", "COMPLETED"}
        or source_url.database and source_url.database != ":memory:" and source_is_sealed(Path(source_url.database).resolve())):
        raise AppError(409, "sqlite_restore_after_migration_forbidden", "主业务库已迁移或正在切换，禁止恢复旧 SQLite；请使用 PostgreSQL 原生灾备")
    """Restore the entire DB and replace managed roots, retaining rollback copies."""
    if json.loads(members["manifest.json"])["app_version"] != settings.app_version:
        raise AppError(409, "site_restore_version_mismatch", "请使用与备份相同版本的 LearnGraph 执行全站恢复")
    configuration = json.loads(members["configuration.json"])
    restored = configuration["settings"]
    roots = managed_roots(settings)
    database = database_path(settings)
    env_file = env_file.resolve()
    for label, root in roots.items():
        if root == Path(root.anchor) or root.is_symlink() or root in {Path.cwd().resolve(), Path.home().resolve()}:
            raise AppError(409, "site_restore_root_invalid", "恢复目录不能为系统根目录、用户目录或当前工作目录")
        if database.is_relative_to(root) or env_file.is_relative_to(root) or any(root.is_relative_to(other) for key, other in roots.items() if key != label):
            raise AppError(409, "site_restore_root_overlap", "数据库、配置与受管文件目录不能相互嵌套")
        restored[label + "_root"] = str(root)
    restored["database_url"] = "sqlite:///" + database.as_posix()
    key_data = json.loads(members["secret-store.json"])
    # Import keys into an isolated keyring namespace, preserving the old store.
    if key_data:
        restored["keyring_service_name"] += ".restore-" + uuid4().hex
    desired_environment = dict(configuration["environment"])
    for key, value in restored.items():
        if value is None:
            continue
        desired_environment["LEARNGRAPH_" + key.upper()] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else str(value)
    # Process/container environment overrides .env. Fail before touching data if
    # a restart would silently keep a different key or runtime configuration.
    conflicts = [key for key, value in os.environ.items() if key.startswith("LEARNGRAPH_") and key in desired_environment and value != desired_environment[key]]
    if conflicts:
        raise AppError(409, "site_restore_environment_conflict", "请先移除或同步这些进程/容器环境变量，再从不带覆盖值的终端恢复：" + ", ".join(sorted(conflicts)))
    Settings(_env_file=None, **restored)
    recovery_id = uuid4().hex
    database_before = database.with_name(database.name + ".before-site-restore-" + recovery_id)
    env_before = env_file.with_name(env_file.name + ".before-site-restore-" + recovery_id)
    swapped: list[tuple[Path, Path, bool]] = []
    external_files: list[tuple[Path, bytes | None]] = []
    had_database = database.exists()
    had_env = env_file.exists()
    journal_path = state_root(settings) / "site-restore.json"
    journal_roots = [
        {
            "path": str(root),
            "previous": str(root.with_name(root.name + ".before-site-restore-" + recovery_id)),
            "existed": root.exists(),
        }
        for root in roots.values()
    ]
    journal_external: list[dict] = []
    journal = {
        "id": recovery_id,
        "engine": "sqlite",
        "state": "IN_PROGRESS",
        "database": str(database),
        "database_before": str(database_before),
        "had_database": had_database,
        "env_file": str(env_file),
        "env_before": str(env_before),
        "had_env": had_env,
        "roots": journal_roots,
        "external": journal_external,
    }
    require_space(database.parent, (database.stat().st_size if had_database else 0) * 2 + sum(info["size"] for info in members.entries.values()))
    with TemporaryDirectory(prefix="lg-site-restore-", dir=work_root(settings)) as temporary:
        staging = Path(temporary)
        snapshot = staging / "site.db"
        copy_member(members, "database.sqlite3", snapshot)
        if had_database:
            _copy_database(database, database_before)
            os.chmod(database_before, 0o600)
        if had_env:
            env_before.write_bytes(small_bytes(env_file))
            os.chmod(env_before, 0o600)
        # Pre-stage all managed root directories BEFORE mutating live paths or
        # recording the IN_PROGRESS journal so extraction failures leave zero
        # side effects on live data.
        staged_roots: list[tuple[Path, Path, Path]] = []
        try:
            for label, root in roots.items():
                root.parent.mkdir(parents=True, exist_ok=True)
                pending = root.with_name(root.name + ".site-restore-" + recovery_id)
                pending.mkdir(mode=0o700)
                for name in members:
                    if name.startswith(label + "/"):
                        target = pending / name[len(label) + 1:]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        copy_member(members, name, target)
                previous = root.with_name(root.name + ".before-site-restore-" + recovery_id)
                staged_roots.append((root, pending, previous))
        except BaseException:
            import shutil
            for _root, pending, _previous in staged_roots:
                if pending.is_dir() and not pending.is_symlink():
                    shutil.rmtree(pending, ignore_errors=True)
            raise

        # Persist the crash-recovery journal before touching any live path so an
        # abrupt kill/power-loss blocks startup until --recover-pending runs.
        atomic_json(journal_path, journal)
        try:
            for root, pending, previous in staged_roots:
                existed = root.exists()
                if existed:
                    root.rename(previous)
                swapped.append((root, previous, existed))
                pending.rename(root)
            if key_data:
                store = KeyringSecretStore(Settings(_env_file=None, **restored))
                for version, secret in key_data["keys"].items():
                    store._set_password(store._key_account(int(version)), secret)
                if key_data["identity"]:
                    store._set_password(store._identity_account, key_data["identity"])
                store._set_password(store._active_account, str(key_data["active_version"]))
            for name in ("host_bridge_token_file", "sandboxd_token_file", "sandboxd_admin_token_file", "update_agent_token_file"):
                content = members.get("config-files/" + name)
                if content is not None and restored.get(name):
                    target = Path(restored[name]).resolve()
                    if not any(target.is_relative_to(root) for root in roots.values()):
                        original = small_bytes(target) if target.exists() else None
                        external_files.append((target, original))
                        backup_copy = None
                        if original is not None:
                            backup_copy = target.with_name(target.name + ".before-site-restore-" + recovery_id)
                            backup_copy.write_bytes(original)
                            os.chmod(backup_copy, 0o600)
                        journal_external.append(
                            {
                                "path": str(target),
                                "backup": str(backup_copy) if backup_copy else None,
                                "existed": original is not None,
                            }
                        )
                        atomic_json(journal_path, journal)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    os.chmod(target, 0o600)
            for name in RUNTIME_FILES:
                target = settings.storage_root.resolve().parent / name
                original = small_bytes(target) if target.exists() else None
                external_files.append((target, original))
                backup_copy = None
                if original is not None:
                    backup_copy = target.with_name(target.name + ".before-site-restore-" + recovery_id)
                    backup_copy.write_bytes(original)
                    os.chmod(backup_copy, 0o600)
                journal_external.append(
                    {
                        "path": str(target),
                        "backup": str(backup_copy) if backup_copy else None,
                        "existed": original is not None,
                    }
                )
                atomic_json(journal_path, journal)
                content = members.get("config-files/" + name)
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(content)
                    os.chmod(target, 0o600)
            _copy_database(snapshot, database)
            # Restore accounts/permissions but revoke historical sessions.
            with closing(sqlite3.connect(database, timeout=5)) as connection:
                connection.execute("UPDATE auth_sessions SET revoked_at = CURRENT_TIMESTAMP WHERE revoked_at IS NULL")
                connection.commit()
            env_file.parent.mkdir(parents=True, exist_ok=True)
            # python-dotenv single quotes preserve JSON, URLs and dollar signs.
            text = "# Restored LearnGraph effective configuration; contains secrets.\n"
            for key, value in sorted(desired_environment.items()):
                if not key.startswith("LEARNGRAPH_") or not key.replace("_", "").isalnum():
                    raise ValueError("invalid environment key")
                text += key + "='" + value.replace("\\", "\\\\").replace("'", "\\'") + "'\n"
            temporary_env = env_file.with_name(env_file.name + ".site-restore-" + recovery_id)
            temporary_env.write_text(text, encoding="utf-8")
            os.chmod(temporary_env, 0o600)
            temporary_env.replace(env_file)
            atomic_json(journal_path, {**journal, "state": "COMPLETED"})
        except Exception:
            if had_database:
                _copy_database(database_before, database)
            else:
                database.unlink(missing_ok=True)
                _unlink_sqlite_sidecars(database)
            if had_env:
                env_file.write_bytes(small_bytes(env_before))
            else:
                env_file.unlink(missing_ok=True)
            for target, content in reversed(external_files):
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(content)
            for root, previous, existed in reversed(swapped):
                if root.exists():
                    root.rename(root.with_name(root.name + ".failed-site-restore-" + recovery_id))
                if existed:
                    previous.rename(root)
            atomic_json(journal_path, {**journal, "state": "ROLLED_BACK"})
            raise
    return {"status": "restored", "restart_required": True, "rollback_database": str(database_before) if had_database else None, "rollback_configuration": str(env_before) if had_env else None}


def main() -> None:
    parser = argparse.ArgumentParser(description="停服后恢复 LearnGraph 全站加密备份；覆盖全站数据库和受管文件")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--recover-pending", action="store_true", help="按 PostgreSQL 事务提交标记完成或撤销被中断的恢复")
    parser.add_argument("--env-file", type=Path, default=Path(os.environ["LEARNGRAPH_DATA_ROOT"]) / "restored.env" if os.environ.get("LEARNGRAPH_DATA_ROOT") else Path(".env"))
    parser.add_argument("--server-stopped", action="store_true", help="确认所有 API、worker 和调度器已停止")
    arguments = parser.parse_args()
    if not arguments.server_stopped:
        parser.error("必须先停止 API、worker 和调度器，再加 --server-stopped")
    settings = Settings()
    if arguments.recover_pending:
        from app.core.database_routing import OperationLease, state_root
        lease = OperationLease(state_root(settings))
        try:
            print(json.dumps(recover_sqlite_offline(settings), ensure_ascii=False))
        finally:
            lease.close()
        return
    if not arguments.backup:
        parser.error("需要 --backup 或 --recover-pending")
    password = getpass.getpass("备份密码: ")
    try:
        if arguments.backup.stat().st_size > settings.max_backup_bytes:
            raise AppError(413, "site_backup_too_large", "备份超过恢复上限")
        result = restore_offline(arguments.backup, password, settings, arguments.env_file)
    except AppError as exc:
        parser.exit(1, str(exc.message) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
