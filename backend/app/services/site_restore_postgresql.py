"""Offline PostgreSQL full-site recovery, with a durable filesystem journal."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

from app.core.config import Settings
from app.core.database_routing import atomic_json, encrypt_url, postgres_engine, read_json, state_root
from app.core.errors import AppError
from app.core.secret_store import KeyringSecretStore
from app.services.postgresql_backup import restore, snapshot
from app.services.site_backup import RUNTIME_FILES, managed_roots
from app.services.backup_io import copy_member, require_space, small_bytes, file_info


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write(path: Path, content: bytes) -> None:
    import hashlib
    if path.is_file() and path.stat().st_size == len(content) and file_info(path)['sha256'] == hashlib.sha256(content).hexdigest():
        # Docker commonly mounts service tokens read-only. Identical tokens
        # need no write or chmod; retain the mount and its host permissions.
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        path.chmod(0o600)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(path.parent)


def recover_offline(settings: Settings) -> dict:
    journal_path = state_root(settings) / "site-restore.json"
    journal = read_json(journal_path)
    if not journal or journal.get("state") in {"COMPLETED", "ROLLED_BACK"}:
        return {"status": "no_pending_restore"}
    private = Path(journal["recovery_directory"])
    prior = json.loads((private / "before.json").read_text())
    engine = postgres_engine(prior["database_url"])
    try:
        with engine.connect() as connection:
            exists = connection.execute(text("SELECT to_regclass('_lg_site_restore')")).scalar()
            marker = connection.execute(text("SELECT restore_id FROM _lg_site_restore WHERE singleton=1")).scalar() if exists else None
        if marker == journal["id"]:
            atomic_json(journal_path, {**journal, "state": "COMPLETED"})
            return {"status": "restored", "restart_required": True}
        # PostgreSQL recovery is one transaction. No marker means no commit;
        # only filesystem/configuration changes need to be undone.
        for item in reversed(journal["roots"]):
            root, previous = Path(item["path"]), Path(item["previous"])
            if previous.exists():
                if root.exists():
                    root.rename(root.with_name(root.name + ".failed-site-restore-" + journal["id"]))
                previous.rename(root)
            elif not item["existed"] and root.exists():
                root.rename(root.with_name(root.name + ".failed-site-restore-" + journal["id"]))
            _sync_directory(root.parent)
        for item in journal["external"]:
            path = Path(item["path"])
            if item["existed"]:
                _write(path, small_bytes(private / item["backup"]))
            else:
                path.unlink(missing_ok=True)
                if path.parent.exists():
                    _sync_directory(path.parent)
        atomic_json(journal_path, {**journal, "state": "ROLLED_BACK"})
        return {"status": "rolled_back", "restart_required": True}
    finally:
        engine.dispose()


def restore_site(members: dict[str, bytes], settings: Settings, env_file: Path, url: str) -> dict:
    recovery_id = uuid4().hex
    try:
        return _restore_site(members, settings, env_file, url, recovery_id)
    except BaseException:
        root = state_root(settings)
        journal = read_json(root / "site-restore.json")
        if not journal or journal.get("id") != recovery_id:
            # Before the journal is durable no live paths have been replaced.
            # Remove only directories created for THIS attempt; never discard
            # a journaled rollback copy or another attempt's recovery material.
            paths = [root / "site-restore-recovery" / recovery_id]
            paths += [directory.with_name(directory.name + ".site-restore-" + recovery_id) for directory in managed_roots(settings).values()]
            for path in paths:
                if path.is_dir() and not path.is_symlink() and path.resolve().parent == path.parent.resolve():
                    shutil.rmtree(path)
        raise


def _restore_site(members, settings: Settings, env_file: Path, url: str, recovery_id: str) -> dict:
    root = state_root(settings)
    prior_journal = read_json(root / "site-restore.json")
    if prior_journal and prior_journal.get("state") not in {"COMPLETED", "ROLLED_BACK"}:
        raise AppError(409, "site_restore_recovery_required", "存在未完成恢复，请先停服执行 --recover-pending")
    configuration = json.loads(members["configuration.json"])
    restored = configuration["settings"]
    roots = managed_roots(settings)
    env_file = env_file.resolve()
    for label, directory in roots.items():
        if directory == Path(directory.anchor) or directory.is_symlink() or directory in {Path.cwd().resolve(), Path.home().resolve()}:
            raise AppError(409, "site_restore_root_invalid", "恢复目录不能为根目录、用户目录或当前工作目录")
        if env_file.is_relative_to(directory) or root.is_relative_to(directory) or any(directory.is_relative_to(other) for key, other in roots.items() if key != label):
            raise AppError(409, "site_restore_root_overlap", "配置、迁移状态与受管目录不能相互嵌套")
        restored[label + "_root"] = str(directory)
    restored["database_url"] = url  # Preserve THIS PostgreSQL target/role, never the source address.
    restored["database_migration_state_dir"] = str(root)
    key_data = json.loads(members["secret-store.json"])
    if key_data:
        restored["keyring_service_name"] += ".restore-" + uuid4().hex
    desired = dict(configuration["environment"])
    for key, value in restored.items():
        if value is not None:
            desired["LEARNGRAPH_" + key.upper()] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else str(value)
    # Compose intentionally keeps SQLite as the bootstrap URL. active.json is
    # authoritative after migration; preserve that bootstrap env without ever
    # routing writes back to it.
    for key in ("LEARNGRAPH_DATABASE_URL", "LEARNGRAPH_DATABASE_MIGRATION_STATE_DIR", "LEARNGRAPH_DATA_ROOT"):
        if key in os.environ:
            desired[key] = os.environ[key]
    master_file = (Path(os.environ["LEARNGRAPH_DATA_ROOT"]) if os.environ.get("LEARNGRAPH_DATA_ROOT") else settings.storage_root.resolve().parent).resolve() / ".master-key"
    file_managed_key = (os.environ.get("_LEARNGRAPH_MASTER_KEY_FROM_FILE") == "1" and master_file.is_file()
        and master_file.read_text().strip() == (settings.master_key or "").strip() and restored.get("secret_provider") == "environment")
    conflicts = [key for key, value in os.environ.items() if key.startswith("LEARNGRAPH_") and key in desired and value != desired[key]
        and not (key == "LEARNGRAPH_MASTER_KEY" and file_managed_key)]
    if conflicts:
        raise AppError(409, "site_restore_environment_conflict", "请先同步这些容器环境变量，再执行恢复：" + ", ".join(sorted(conflicts)))
    restored_settings = Settings(_env_file=None, **restored)
    private = root / "site-restore-recovery" / recovery_id
    private.mkdir(parents=True, mode=0o700)
    private.chmod(0o700)
    _write(private / "before.json", json.dumps({"database_url": url}, ensure_ascii=False).encode())
    before_dump = private / "before.postgresql.dump"
    from app.services.postgresql_backup import database_bytes
    require_space(private, database_bytes(url) * 6 + sum(info["size"] for info in members.entries.values()) if hasattr(members, "entries") else database_bytes(url) * 6)
    metadata = snapshot(url, before_dump, include_files=False)
    before_dump.chmod(0o600)
    _write(private / "before-metadata.json", json.dumps(metadata).encode())
    active = read_json(root / "active.json")
    root_entries = [{"path": str(directory), "previous": str(directory.with_name(directory.name + ".before-site-restore-" + recovery_id)), "existed": directory.exists()} for directory in roots.values()]
    paths = {env_file, root / "active.json", master_file}
    token_names = ("host_bridge_token_file", "sandboxd_token_file", "sandboxd_admin_token_file", "update_agent_token_file")
    for name in token_names:
        if "config-files/" + name in members and restored.get(name):
            target = Path(restored[name]).resolve()
            if not any(target.is_relative_to(directory) for directory in roots.values()):
                paths.add(target)
    paths.update(settings.storage_root.resolve().parent / name for name in RUNTIME_FILES)
    external = []
    for index, path in enumerate(sorted(paths)):
        item = {"path": str(path), "existed": path.exists(), "backup": f"external-{index}"}
        if path.exists():
            _write(private / item["backup"], small_bytes(path))
        external.append(item)
    _sync_directory(private.parent)
    journal = {"id": recovery_id, "state": "RESTORING", "recovery_directory": str(private), "roots": root_entries, "external": external}
    # Stage all new directories before the first irreversible filesystem edit.
    staged = []
    for label, directory in roots.items():
        directory.parent.mkdir(parents=True, exist_ok=True)
        pending = directory.with_name(directory.name + ".site-restore-" + recovery_id)
        pending.mkdir(mode=0o700)
        for name in members:
            if name.startswith(label + "/"):
                copy_member(members, name, pending / name[len(label) + 1:])
        staged.append(pending)
        for child in pending.rglob("*"):
            if child.is_dir():
                _sync_directory(child)
        _sync_directory(pending)
    atomic_json(root / "site-restore.json", journal)
    try:
        for item, pending in zip(root_entries, staged):
            directory, previous = Path(item["path"]), Path(item["previous"])
            if item["existed"]:
                directory.rename(previous)
            pending.rename(directory)
            _sync_directory(directory.parent)
        if key_data:
            store = KeyringSecretStore(restored_settings)
            for version, secret in key_data["keys"].items():
                store._set_password(store._key_account(int(version)), secret)
            if key_data.get("identity"):
                store._set_password(store._identity_account, key_data["identity"])
            store._set_password(store._active_account, str(key_data["active_version"]))
        for name in token_names:
            if "config-files/" + name in members and restored.get(name):
                _write(Path(restored[name]).resolve(), members["config-files/" + name])
        for name in RUNTIME_FILES:
            target = settings.storage_root.resolve().parent / name
            if "config-files/" + name in members:
                _write(target, members["config-files/" + name])
            else:
                target.unlink(missing_ok=True)
        if active:
            atomic_json(root / "active.json", {**active, **encrypt_url(restored_settings, url), "phase": "postgresql_active"})
        if restored_settings.secret_provider == "environment" and restored_settings.master_key:
            _write(master_file, restored_settings.master_key.encode())
        env_text = "# Restored LearnGraph configuration; contains secrets.\n"
        for key, value in sorted(desired.items()):
            env_text += key + "='" + value.replace("\\", "\\\\").replace("'", "\\'") + "'\n"
        pending_env = env_file.with_name(env_file.name + ".site-restore-" + recovery_id)
        _write(pending_env, env_text.encode())
        pending_env.replace(env_file)
        _sync_directory(env_file.parent)
        archive = private / "requested.postgresql.dump"
        copy_member(members, "database.postgresql.dump", archive)
        # Database commit is LAST: filesystem, configuration and pointer are
        # already recoverable. Data checks, revocation and PG identity commit
        # together. No database drop/create or role escalation is required.
        restore(url, archive, json.loads(members["database-metadata.json"]), activation_id=active["job_id"] if active else None, activation_commit=active.get("final_commit") if active else None, restore_id=recovery_id, max_sql_bytes=settings.max_backup_bytes * 4)
        atomic_json(root / "site-restore.json", {**journal, "state": "COMPLETED"})
    except BaseException:
        result = recover_offline(settings)
        if result["status"] == "restored":
            return {**result, "rollback_database": str(before_dump), "rollback_configuration": str(private)}
        raise
    return {"status": "restored", "restart_required": True, "database_kind": "postgresql", "rollback_database": str(before_dump), "rollback_configuration": str(private)}
