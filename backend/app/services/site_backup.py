"""Encrypted instance backups. Applying a backup is deliberately an offline operation.

No workspace JSON serializer is involved: the SQLite online backup API preserves
all tables, binary columns, credentials, account records and foreign keys.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from contextlib import closing
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
from app.domain.migration_models import InfrastructureBinding
from app.domain.models import utc_now

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


def _validate_database(payload: bytes) -> tuple[int, int]:
    with TemporaryDirectory(prefix="lg-site-check-") as temporary:
        path = Path(temporary) / "site.db"
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


def read_backup(payload: bytes, password: str, limit: int) -> dict[str, bytes]:
    """Authenticate, bound decompression, verify every member, then parse data."""
    if len(payload) > limit:
        raise AppError(413, "site_backup_too_large", "备份超过恢复上限")
    outer = _members(payload, limit)
    try:
        if set(outer) != {"encryption.json", "payload.fernet"}:
            raise ValueError("members")
        if len(outer["encryption.json"]) > 4096:
            raise ValueError("encryption metadata too large")
        envelope = json.loads(outer["encryption.json"])
        if envelope["format"] != FORMAT or envelope["version"] != VERSION or envelope["kdf"] != "scrypt-32768-8-1":
            raise ValueError("format")
        salt = base64.b64decode(envelope["salt"], validate=True)
        if len(salt) != 16:
            raise ValueError("salt")
        plaintext = _cipher(password, salt).decrypt(outer["payload.fernet"])
    except InvalidToken as exc:
        raise AppError(422, "site_backup_password_invalid", "备份密码错误或加密内容已损坏") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError(422, "site_backup_format_invalid", "备份加密格式不受支持（旧工作区 ZIP 不属于全站备份）") from exc
    members = _members(plaintext, limit)
    try:
        manifest = json.loads(members["manifest.json"])
        if manifest["format"] != FORMAT or manifest["version"] != VERSION or not isinstance(manifest.get("app_version"), str):
            raise ValueError("manifest")
        entries = manifest["members"]
        if not isinstance(entries, dict) or set(entries) != set(members) - {"manifest.json"}:
            raise ValueError("inventory")
        for name, entry in entries.items():
            if len(members[name]) != entry["size"] or _digest(members[name]) != entry["sha256"]:
                raise ValueError("checksum")
            if name not in {"database.sqlite3", "configuration.json", "secret-store.json"} and not name.startswith(("storage/", "memory/", "config-files/")):
                raise ValueError("unknown member")
        _validate_database(members["database.sqlite3"])
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
        source = database_path(self.settings)
        # A local archive must never silently omit an active external store.
        for binding in self.db.scalars(select(InfrastructureBinding).where(InfrastructureBinding.role == "active", InfrastructureBinding.status == "active")):
            if binding.capability == "object_storage" and (binding.provider_kind != "local" or Path(binding.locator.get("root", self.settings.storage_root)).resolve() != self.settings.storage_root.resolve()):
                raise AppError(409, "site_backup_storage_unsupported", "存在外部对象存储或自定义存储根目录，不能生成完整本地全站备份")
        limit = self.settings.max_backup_bytes
        members: dict[str, bytes] = {}
        size = 0

        def add(name: str, content: bytes) -> None:
            nonlocal size
            _check_path(name)
            size += len(content)
            if size > limit:
                raise AppError(413, "site_backup_too_large", "全站数据超过备份上限，请提高 LEARNGRAPH_MAX_BACKUP_BYTES")
            members[name] = content

        with TemporaryDirectory(prefix="lg-site-backup-") as temporary:
            snapshot = Path(temporary) / "site.db"
            with closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as live, closing(sqlite3.connect(snapshot)) as target:
                live.backup(target)
            add("database.sqlite3", snapshot.read_bytes())
        _validate_database(members["database.sqlite3"])
        for label, root in managed_roots(self.settings).items():
            if root.exists():
                for path in sorted(root.rglob("*")):
                    if path.is_symlink() or (path.exists() and root not in path.resolve().parents):
                        raise AppError(409, "site_backup_symlink", "受管目录含符号链接，无法保证备份完整性")
                    if path.is_file():
                        if path.stat().st_size > limit - size:
                            raise AppError(413, "site_backup_too_large", "全站文件超过备份上限")
                        add(f"{label}/{path.relative_to(root).as_posix()}", path.read_bytes())
        # Check database references against the snapshot, not the changing live DB.
        with TemporaryDirectory(prefix="lg-site-files-") as temporary:
            snapshot = Path(temporary) / "site.db"
            snapshot.write_bytes(members["database.sqlite3"])
            with closing(sqlite3.connect(snapshot)) as connection:
                for object_key, expected_size, expected_hash in connection.execute("SELECT object_key, size_bytes, sha256 FROM files"):
                    content = members.get("storage/" + object_key.replace("\\", "/"))
                    if content is None or len(content) != expected_size or _digest(content) != expected_hash:
                        raise AppError(409, "site_backup_file_invalid", "数据库引用的文件缺失或已变化，请停止写入后重试备份")
        configuration = {"settings": self.settings.model_dump(mode="json"), "environment": {key: value for key, value in os.environ.items() if key.startswith("LEARNGRAPH_")}}
        # Resolve local paths so the offline restorer can preserve data locations.
        configuration["settings"]["database_url"] = "sqlite:///" + source.as_posix()
        for label, root in managed_roots(self.settings).items():
            configuration["settings"][label + "_root"] = str(root)
        for name in ("host_bridge_token_file", "sandboxd_token_file", "sandboxd_admin_token_file", "update_agent_token_file"):
            value = getattr(self.settings, name)
            if value and Path(value).is_file():
                add("config-files/" + name, Path(value).read_bytes())
                configuration["settings"][name] = str(Path(value).resolve())
        if Path(".env").is_file():
            add("config-files/dotenv", Path(".env").read_bytes())
        for name in RUNTIME_FILES:
            path = self.settings.storage_root.resolve().parent / name
            if path.is_file():
                add("config-files/" + name, path.read_bytes())
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
        manifest = {"format": FORMAT, "version": VERSION, "created_at": utc_now().isoformat(), "app_version": self.settings.app_version, "scope": "site", "members": {name: {"size": len(content), "sha256": _digest(content)} for name, content in members.items()}, "excluded": ["external services and running sandbox disks", "application source and container images"]}
        inner = BytesIO()
        with ZipFile(inner, "w", compression=ZIP_DEFLATED) as archive:
            for name, content in members.items():
                archive.writestr(name, content)
            archive.writestr("manifest.json", _json(manifest))
        salt = os.urandom(16)
        outer = BytesIO()
        with ZipFile(outer, "w", compression=ZIP_STORED) as archive:
            archive.writestr("encryption.json", _json({"format": FORMAT, "version": VERSION, "kdf": "scrypt-32768-8-1", "salt": base64.b64encode(salt).decode("ascii")}))
            archive.writestr("payload.fernet", _cipher(password, salt).encrypt(inner.getvalue()))
        result = outer.getvalue()
        # Verify the exact downloadable artifact using the recovery parser.
        read_backup(result, password, limit)
        return result

    def stage_restore(self, payload: bytes, password: str) -> dict:
        members = read_backup(payload, password, self.settings.max_backup_bytes)
        if json.loads(members["manifest.json"])["app_version"] != self.settings.app_version:
            raise AppError(409, "site_restore_version_mismatch", "请使用与备份相同版本的 LearnGraph 执行全站恢复")
        tables, records = _validate_database(members["database.sqlite3"])
        job_id = str(uuid4())
        directory = database_path(self.settings).parent / "site-restore"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / f"{job_id}.zip"
        with path.open("xb") as handle:
            os.chmod(path, 0o600)
            handle.write(payload)
        return {"job_id": job_id, "source_workspace_id": None, "tables": tables, "records": records, "files": sum(name.startswith("storage/") for name in members), "memory_files": sum(name.startswith("memory/") for name in members), "mode": "staged", "restore_path": str(path), "restart_required": True}
