"""Encrypted instance backups for SQLite and PostgreSQL; offline application."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile
from zlib import error as ZlibError

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.secret_store import MasterKeyNotFound, secret_store_from_settings
from app.services.backup_io import (BackupArtifact, FileMembers, SMALL_MEMBER, copy_member, copy_stream,
    decrypt_fernet, encrypt_fernet, file_info, member_info, payload_source, private_temp, require_space, work_root)

FORMAT = "learngraph.site-backup"
VERSION = 1
RUNTIME_FILES = (
    "sandbox-runtime.json", "sandbox-bootstrap-policy.json",
    "sandbox-bootstrap-source.json", "sandbox-preview-config.json",
)


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _cipher(password: str, salt: bytes) -> Fernet:
    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def database_path(settings: Settings) -> Path:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise AppError(409, "site_backup_database_unsupported", "全站备份目前支持本地 SQLite；外部数据库须使用数据库原生灾备工具")
    return Path(url.database).resolve()


def backup_database_kind(members: dict[str, bytes]) -> str:
    return "postgresql" if "database.postgresql.dump" in members else "sqlite"


def validate_backup_database(members: dict[str, bytes]) -> tuple[int, int]:
    if backup_database_kind(members) == "sqlite":
        return _validate_database(members.path("database.sqlite3") if isinstance(members, FileMembers) else members["database.sqlite3"])
    from app.services.postgresql_backup import validate_archive, validate_metadata
    metadata = json.loads(members["database-metadata.json"])
    validate_metadata(metadata)
    if metadata['tables']['files'][0] != len(metadata['files']):
        raise AppError(422,'site_backup_file_invalid','全站备份缺少完整的数据库文件引用清单')
    if isinstance(members, FileMembers):
        validate_archive(members.path("database.postgresql.dump"), metadata)
        return validate_metadata(metadata)
    with TemporaryDirectory(prefix="lg-pg-check-") as temporary:
        path = Path(temporary) / "database.dump"
        path.write_bytes(members["database.postgresql.dump"])
        validate_archive(path, metadata)
    return validate_metadata(metadata)


def ensure_restore_target(members: dict[str, bytes], settings: Settings) -> str:
    from app.core.database_routing import read_json, source_is_sealed, state_root
    from app.services.postgresql_backup import effective_url
    root = state_root(settings)
    job = read_json(root / "job.json")
    active = read_json(root / "active.json")
    if active and active.get("phase") != "postgresql_active" or job and job.get("state") not in {"CANCELLED", "COMPLETED"}:
        raise AppError(409, "site_restore_migration_busy", "主业务库迁移尚未结束，不能覆盖恢复")
    url = effective_url(settings)
    target = make_url(url)
    kind = backup_database_kind(members)
    if target.get_backend_name() != kind:
        raise AppError(409, "site_restore_database_mismatch", "全站恢复必须使用同类数据库；已启用 PostgreSQL 后禁止恢复 SQLite")
    if kind == "sqlite" and target.database and target.database != ":memory:" and source_is_sealed(Path(target.database).resolve()):
        raise AppError(409, "sqlite_restore_after_migration_forbidden", "SQLite 已永久封存，禁止恢复为主库")
    return url


def managed_roots(settings: Settings) -> dict[str, Path]:
    return {"storage": settings.storage_root.resolve(), "memory": settings.memory_root.resolve()}


def _check_path(name: str) -> None:
    path = PurePosixPath(name)
    if not name or any(ord(character) < 32 or character in '\\:<>|?*"' for character in name) or path.is_absolute() or any(part in {"..", ".", ""} for part in name.split("/")):
        raise AppError(422, "site_backup_path_invalid", "备份包含不安全的路径")
    for part in path.parts:
        if part.endswith((".", " ")) or part.split(".")[0].casefold() in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}:
            raise AppError(422, "site_backup_path_invalid", "备份包含不可移植的文件名")


def _members(payload: bytes, limit: int) -> dict[str, bytes]:
    try:
        with ZipFile(BytesIO(payload)) as archive:
            infos = archive.infolist()
            if len(infos) > 100_000 or len({item.filename.casefold() for item in infos}) != len(infos):
                raise ValueError("duplicate members")
            if sum(item.file_size for item in infos) > limit:
                raise AppError(413, "site_backup_too_large", "备份解压后超过恢复上限")
            for item in infos:
                _check_path(item.orig_filename)
                _check_path(item.filename)
                if item.is_dir() or item.flag_bits & 1 or (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("unsupported archive member")
            return {item.filename: archive.read(item) for item in infos}
    except (BadZipFile, KeyError, ValueError, RuntimeError, NotImplementedError, OSError, EOFError, ZlibError) as exc:
        raise AppError(422, "site_backup_invalid", "备份 ZIP 损坏或结构不受支持") from exc


def _validate_database(payload: bytes | Path) -> tuple[int, int]:
    with TemporaryDirectory(prefix="lg-site-check-") as temporary:
        path = Path(temporary) / "site.db"
        if isinstance(payload, Path):
            path = payload
        else:
            path.write_bytes(payload)
        try:
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("integrity")
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("foreign keys")
                tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                if not {"users", "workspaces", "provider_configs", "provider_secrets", "auth_sessions", "files"} <= set(tables):
                    raise ValueError("not a LearnGraph database")
                records = sum(connection.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in tables)
                return len(tables), records
        except (sqlite3.Error, ValueError) as exc:
            raise AppError(422, "site_backup_database_invalid", "备份数据库完整性或外键校验失败") from exc


def _checked_infos(archive, limit):
    infos = archive.infolist()
    if len(infos) > 100_000 or len({item.filename.casefold() for item in infos}) != len(infos):
        raise ValueError("duplicate members")
    if sum(item.file_size for item in infos) > limit:
        raise AppError(413, "site_backup_too_large", "备份解压后超过恢复上限")
    for item in infos:
        _check_path(item.orig_filename)
        _check_path(item.filename)
        if item.is_dir() or item.flag_bits & 1 or (item.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError("unsupported archive member")
    return infos


def read_backup(payload, password: str, limit: int, *, directory=None) -> FileMembers:
    members = FileMembers(private_temp(directory=directory))
    try:
        return _read_backup(payload, password, limit, members)
    except BaseException:
        members.close()
        raise


def _read_backup(payload, password: str, limit: int, members: FileMembers) -> FileMembers:
    """Authenticate, bound decompression, verify every member, then parse data."""
    try:
        with payload_source(payload) as handle:
            handle.seek(0, 2)
            if handle.tell() > limit:
                raise AppError(413, "site_backup_too_large", "备份超过恢复上限")
            handle.seek(0)
            with ZipFile(handle) as outer:
                _checked_infos(outer, limit)
                if set(outer.namelist()) != {"encryption.json", "payload.fernet"} or outer.getinfo("encryption.json").file_size > 4096:
                    raise ValueError("members")
                envelope = json.loads(outer.read("encryption.json"))
                if envelope["format"] != FORMAT or envelope["version"] != VERSION or envelope["kdf"] != "scrypt-32768-8-1":
                    raise ValueError("format")
                salt = base64.b64decode(envelope["salt"], validate=True)
                if len(salt) != 16:
                    raise ValueError("salt")
                require_space(members.root, outer.getinfo("payload.fernet").file_size * 2)
                inner = members.root / "inner.zip"
                with outer.open("payload.fernet") as encrypted, inner.open("xb") as output:
                    inner.chmod(0o600)
                    decrypt_fernet(encrypted, output, password, salt, limit)
    except InvalidToken as exc:
        raise AppError(422, "site_backup_password_invalid", "备份密码错误或加密内容已损坏") from exc
    except (BadZipFile, KeyError, TypeError, ValueError, EOFError, ZlibError) as exc:
        raise AppError(422, "site_backup_format_invalid", "备份加密格式不受支持（旧工作区 ZIP 不属于全站备份）") from exc
    try:
        with ZipFile(inner) as archive:
            infos = _checked_infos(archive, limit)
            require_space(members.root, sum(item.file_size for item in infos))
            for item in infos:
                path = members.root / "members" / item.filename
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.open(item) as source, path.open("xb") as target:
                    path.chmod(0o600)
                    size, digest = copy_stream(source, target, limit=item.file_size)
                members.entries[item.filename] = {"size": size, "sha256": digest}
        inner.unlink()
    except (BadZipFile, ValueError, RuntimeError, NotImplementedError, EOFError, ZlibError) as exc:
        raise AppError(422, "site_backup_invalid", "备份 ZIP 损坏或结构不受支持") from exc
    try:
        manifest = json.loads(members["manifest.json"])
        if manifest["format"] != FORMAT or manifest["version"] not in {1, 2} or not isinstance(manifest.get("app_version"), str):
            raise ValueError("manifest")
        entries = manifest["members"]
        if not isinstance(entries, dict) or set(entries) != set(members) - {"manifest.json"}:
            raise ValueError("inventory")
        for name, entry in entries.items():
            if member_info(members, name) != entry:
                raise ValueError("checksum")
            if name not in {"database.sqlite3", "database.postgresql.dump", "database-metadata.json", "configuration.json", "secret-store.json"} and not name.startswith(("storage/", "memory/", "config-files/")):
                raise ValueError("unknown member")
        if ("database.sqlite3" in members) == ("database.postgresql.dump" in members):
            raise ValueError("exactly one database required")
        if "database.postgresql.dump" in members and (manifest["version"] != 2 or manifest.get("database_kind") != "postgresql" or "database-metadata.json" not in members):
            raise ValueError("PostgreSQL metadata required")
        if "database.sqlite3" in members and "database-metadata.json" in members:
            raise ValueError("mixed database metadata")
        members.database_validation = validate_backup_database(members)
        if "database.postgresql.dump" in members:
            for object_key, expected_size, expected_hash in json.loads(members["database-metadata.json"])["files"]:
                content = member_info(members, "storage/" + object_key.replace("\\", "/"))
                if content != {"size": expected_size, "sha256": expected_hash}:
                    raise ValueError("PostgreSQL file reference mismatch")
        configuration = json.loads(members["configuration.json"])
        Settings(_env_file=None, **configuration["settings"])
        keys = json.loads(members["secret-store.json"])
        if not isinstance(keys, dict) or not isinstance(configuration.get("environment"), dict):
            raise ValueError("configuration")
        if any(not isinstance(key, str) or not key.startswith("LEARNGRAPH_") or not key.replace("_", "").isalnum() or not isinstance(value, str) for key, value in configuration["environment"].items()):
            raise ValueError("environment")
        if keys and (keys.get("provider") != "keyring" or not isinstance(keys.get("active_version"), int) or not isinstance(keys.get("keys"), dict) or str(keys["active_version"]) not in keys["keys"] or any(not version.isdigit() or not isinstance(secret, str) or not secret for version, secret in keys["keys"].items()) or keys.get("identity") is not None and not isinstance(keys["identity"], str)):
            raise ValueError("secret store")
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError(422, "site_backup_manifest_invalid", "备份清单、配置或 SHA-256 校验失败") from exc
    return members


class SiteBackupService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def export(self, password: str) -> bytes:
        """Small-call compatibility. HTTP and large jobs always use export_file."""
        artifact = self.export_file(password)
        try:
            if artifact.path.stat().st_size > SMALL_MEMBER:
                raise AppError(413, "site_backup_requires_stream", "大备份必须通过流式下载入口读取")
            return artifact.path.read_bytes()
        finally:
            artifact.close()

    def export_file(self, password: str) -> BackupArtifact:
        from app.core.database_routing import OperationLease, state_root
        from app.services.database_maintenance import ensure_available
        try:
            lease = OperationLease(state_root(self.settings))
        except BlockingIOError as exc:
            raise AppError(409, "site_backup_busy", "数据库迁移、更新备份或恢复正在执行") from exc
        temporary = None
        try:
            ensure_available(self.settings)
            temporary = private_temp(self.settings)
            return BackupArtifact(temporary, self._export_path(password, Path(temporary.name)))
        except BaseException:
            if temporary is not None:
                temporary.cleanup()
            raise
        finally:
            lease.close()

    def _export_path(self, password: str, temporary: Path) -> Path:
        from app.domain.migration_models import InfrastructureBinding
        from app.services.postgresql_backup import effective_url, snapshot as pg_snapshot
        source_url = effective_url(self.settings)
        kind = make_url(source_url).get_backend_name()
        if kind not in {"sqlite", "postgresql"}:
            raise AppError(409, "site_backup_database_unsupported", "全站备份仅支持 SQLite 和 PostgreSQL")
        source = database_path(self.settings) if kind == "sqlite" else None
        # A local archive must never silently omit an active external store.
        for binding in self.db.scalars(select(InfrastructureBinding).where(InfrastructureBinding.role == "active", InfrastructureBinding.status == "active")):
            if binding.capability == "object_storage" and (binding.provider_kind != "local" or Path(binding.locator.get("root", self.settings.storage_root)).resolve() != self.settings.storage_root.resolve()):
                raise AppError(409, "site_backup_storage_unsupported", "存在外部对象存储或自定义存储根目录，不能生成完整本地全站备份")
        limit = self.settings.max_backup_bytes
        members: dict[str, Path] = {}
        entries = {}
        size = 0
        # Budget for snapshot, compressed ZIP, base64 ciphertext and independent
        # verification. PostgreSQL's size estimate includes indexes and TOAST.
        from app.services.postgresql_backup import database_bytes
        raw_size = source.stat().st_size if source else database_bytes(source_url)
        file_size = sum(path.stat().st_size for root in managed_roots(self.settings).values() if root.exists() for path in root.rglob("*") if path.is_file())
        require_space(temporary, 5 * (raw_size + file_size))

        def add(name: str, content: bytes | Path) -> None:
            nonlocal size
            _check_path(name)
            if name.startswith("config-files/") and isinstance(content, Path) and content.stat().st_size > SMALL_MEMBER:
                raise AppError(413, "site_backup_metadata_too_large", "备份配置或密钥文件超过 32 MiB 安全上限")
            path = temporary / "source" / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open("xb") as output:
                path.chmod(0o600)
                if isinstance(content, Path):
                    with content.open("rb") as handle:
                        count, digest = copy_stream(handle, output, limit=limit - size)
                else:
                    if len(content) > limit - size:
                        raise AppError(413, "site_backup_too_large", "全站数据超过备份上限，请提高 LEARNGRAPH_MAX_BACKUP_BYTES")
                    output.write(content)
                    count, digest = len(content), _digest(content)
            size += count
            members[name], entries[name] = path, {"size": count, "sha256": digest}

        snapshot = temporary / "database.snapshot"
        if kind == "sqlite":
            from app.services.database_migration import _online_snapshot
            _online_snapshot(source, snapshot)
            _validate_database(snapshot)
            add("database.sqlite3", snapshot)
            with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)) as connection:
                if connection.execute("SELECT count(*) FROM files").fetchone()[0] > 100_000:
                    raise AppError(413, "site_backup_too_many_files", "备份文件清单超过 100,000 项安全上限")
                references = list(connection.execute("SELECT object_key,size_bytes,sha256 FROM files"))
        else:
            metadata = pg_snapshot(source_url, snapshot)
            add("database.postgresql.dump", snapshot)
            add("database-metadata.json", _json(metadata))
            references = metadata["files"]
        snapshot.unlink()
        for label, root in managed_roots(self.settings).items():
            if root.exists():
                for path in sorted(root.rglob("*")):
                    if path.is_symlink() or (path.exists() and root not in path.resolve().parents):
                        raise AppError(409, "site_backup_symlink", "受管目录含符号链接，无法保证备份完整性")
                    if path.is_file():
                        add(f"{label}/{path.relative_to(root).as_posix()}", path)
        for object_key, expected_size, expected_hash in references:
            if entries.get("storage/" + object_key.replace("\\", "/")) != {"size": expected_size, "sha256": expected_hash}:
                raise AppError(409, "site_backup_file_invalid", "数据库引用的文件缺失或已变化，请停止写入后重试备份")
        configuration = {"settings": self.settings.model_dump(mode="json"), "environment": {key: value for key, value in os.environ.items() if key.startswith("LEARNGRAPH_")}}
        # Resolve local paths so the offline restorer can preserve data locations.
        configuration["settings"]["database_url"] = "sqlite:///" + source.as_posix() if source else source_url
        for label, root in managed_roots(self.settings).items():
            configuration["settings"][label + "_root"] = str(root)
        for name in ("host_bridge_token_file", "sandboxd_token_file", "sandboxd_admin_token_file", "update_agent_token_file"):
            value = getattr(self.settings, name)
            if value and Path(value).is_file():
                add("config-files/" + name, Path(value))
                configuration["settings"][name] = str(Path(value).resolve())
        if Path(".env").is_file():
            add("config-files/dotenv", Path(".env"))
        for name in RUNTIME_FILES:
            path = self.settings.storage_root.resolve().parent / name
            if path.is_file():
                add("config-files/" + name, path)
        add("configuration.json", _json(configuration))
        keys: dict[str, object] = {}
        store = secret_store_from_settings(self.settings)
        if self.settings.secret_provider == "keyring":
            status = store.status()
            if status.active_key_version is not None:
                try:
                    identity = store.identity_key()
                except MasterKeyNotFound:
                    identity = None
                keys = {"provider": "keyring", "active_version": status.active_key_version, "keys": {str(version): store.key(version).secret for version in range(1, status.active_key_version + 1)}, "identity": identity}
        add("secret-store.json", _json(keys))
        manifest = {"format": FORMAT, "version": 2 if kind == "postgresql" else VERSION, "database_kind": kind, "created_at": datetime.now(timezone.utc).isoformat(), "app_version": self.settings.app_version, "scope": "site", "members": entries, "excluded": ["external services and running sandbox disks", "application source and container images"]}
        inner = temporary / "inner.zip"
        with ZipFile(inner, "w", compression=ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
            for name, path in members.items():
                # Native pg_dump already compresses its segments.
                archive.write(path, name, compress_type=ZIP_STORED if name.endswith(".postgresql.dump") else ZIP_DEFLATED)
            archive.writestr("manifest.json", _json(manifest))
        inner.chmod(0o600)
        salt = os.urandom(16)
        result = temporary / "learngraph-site-backup.zip"
        with result.open("xb") as output:
            result.chmod(0o600)
            with ZipFile(output, "w", compression=ZIP_STORED, allowZip64=True) as archive:
                archive.writestr("encryption.json", _json({"format": FORMAT, "version": VERSION, "kdf": "scrypt-32768-8-1", "salt": base64.b64encode(salt).decode("ascii")}))
                with inner.open("rb") as source, archive.open("payload.fernet", "w", force_zip64=True) as encrypted:
                    encrypt_fernet(source, encrypted, password, salt)
            output.flush()
            os.fsync(output.fileno())
        inner.unlink()
        # Release snapshot/file copies before independent verification.
        for path in members.values():
            path.unlink()
        with read_backup(result, password, limit, directory=temporary):
            pass
        return result

    def stage_restore(self, payload: bytes, password: str) -> dict:
        from app.core.database_routing import OperationLease, state_root
        from app.services.database_maintenance import ensure_available
        try:
            lease = OperationLease(state_root(self.settings))
        except BlockingIOError as exc:
            raise AppError(409, "site_restore_busy", "数据库维护正在执行") from exc
        try:
            ensure_available(self.settings)
            return self._stage_restore(payload, password)
        finally:
            lease.close()

    def _stage_restore(self, payload: bytes, password: str) -> dict:
        members = read_backup(payload, password, self.settings.max_backup_bytes, directory=work_root(self.settings))
        try:
            return self._stage_validated(payload, members)
        finally:
            members.close()

    def _stage_validated(self, payload, members) -> dict:
        if json.loads(members["manifest.json"])["app_version"] != self.settings.app_version:
            raise AppError(409, "site_restore_version_mismatch", "请使用与备份相同版本的 LearnGraph 执行全站恢复")
        ensure_restore_target(members, self.settings)
        tables, records = members.database_validation
        job_id = str(uuid4())
        directory = (database_path(self.settings).parent if backup_database_kind(members) == "sqlite" else self.settings.storage_root.resolve().parent) / "site-restore"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / f"{job_id}.zip"
        try:
            with path.open("xb") as handle:
                os.chmod(path, 0o600)
                with payload_source(payload) as source:
                    source.seek(0, 2)
                    require_space(directory, source.tell())
                    source.seek(0)
                    copy_stream(source, handle, limit=self.settings.max_backup_bytes)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {"job_id": job_id, "source_workspace_id": None, "tables": tables, "records": records, "files": sum(name.startswith("storage/") for name in members), "memory_files": sum(name.startswith("memory/") for name in members), "mode": "staged", "restore_path": str(path), "restart_required": True}
