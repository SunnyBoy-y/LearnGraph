"""Bounded file I/O for site archives, including the existing Fernet format.

Plaintext stays in private temporary directories. A decrypted archive is never
opened until its complete Fernet HMAC and padding have been checked.
"""
from __future__ import annotations

import base64
from collections.abc import Mapping
import hashlib
import io
import os
from pathlib import Path
import shutil
import struct
from tempfile import TemporaryDirectory
import time

from cryptography.exceptions import InvalidSignature
from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives import hashes, hmac, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app.core.errors import AppError

BLOCK = 1024 * 1024
SMALL_MEMBER = 32 * BLOCK


def require_space(directory: Path, needed: int, reserve: int = 512 * BLOCK) -> None:
    directory = directory.resolve()
    while not directory.exists():
        directory = directory.parent
    if shutil.disk_usage(directory).free < needed + reserve:
        raise AppError(507, "site_backup_space_insufficient", "备份或恢复临时空间不足，请释放空间后重试")


def work_root(settings, *, create=True) -> Path:
    root = settings.storage_root.resolve().parent / ".backup-work"
    if any(root.is_relative_to(managed.resolve()) or managed.resolve().is_relative_to(root) for managed in (settings.storage_root, settings.memory_root)):
        raise AppError(409, "site_backup_root_overlap", "备份暂存目录与受管文件目录不能相互嵌套")
    if root.is_symlink():
        raise AppError(409, "site_backup_symlink", "备份暂存目录不能为符号链接")
    if create:
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        root.chmod(0o700)
    return root


def private_temp(settings=None, *, directory=None):
    return TemporaryDirectory(prefix="lg-site-", dir=directory or (work_root(settings) if settings else None))


def copy_stream(source, target, *, limit: int) -> tuple[int, str]:
    total, digest = 0, hashlib.sha256()
    while block := source.read(BLOCK):
        total += len(block)
        if total > limit:
            raise AppError(413, "site_backup_too_large", "备份或解压后的数据超过配置上限")
        target.write(block)
        digest.update(block)
    return total, digest.hexdigest()


def file_info(path: Path) -> dict:
    with path.open("rb") as source:
        size, digest = copy_stream(source, _Discard(), limit=2**63 - 1)
    return {"size": size, "sha256": digest}


def small_bytes(path: Path) -> bytes:
    with path.open('rb') as handle:
        value = handle.read(SMALL_MEMBER + 1)
    if len(value) > SMALL_MEMBER:
        raise AppError(413, "site_backup_metadata_too_large", "备份配置或密钥文件超过 32 MiB 安全上限")
    return value


class _Discard:
    def write(self, value):
        return len(value)


class FileMembers(Mapping):
    """Small metadata can be read as bytes; databases/files are copied by path."""
    def __init__(self, temporary):
        self.temporary = temporary
        self.root = Path(temporary.name)
        self.entries = {}

    def __iter__(self):
        return iter(self.entries)

    def __len__(self):
        return len(self.entries)

    def __contains__(self, name):
        return name in self.entries

    def __getitem__(self, name):
        path = self.path(name)
        if path.stat().st_size > SMALL_MEMBER:
            raise AppError(413, "site_backup_member_requires_stream", "大备份成员必须通过文件流读取")
        return small_bytes(path)

    def path(self, name):
        if name not in self.entries:
            raise KeyError(name)
        return self.root / "members" / name

    def close(self):
        self.temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def copy_member(members, name: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as output:
        target.chmod(0o600)
        if isinstance(members, FileMembers):
            with members.path(name).open("rb") as source:
                copy_stream(source, output, limit=members.entries[name]["size"])
        else:  # Compatibility for small callers and recovery fault-injection tests.
            output.write(members[name])
        output.flush()
        os.fsync(output.fileno())


def member_info(members, name: str):
    if isinstance(members, FileMembers):
        return members.entries.get(name)
    content = members.get(name)
    return None if content is None else {"size": len(content), "sha256": hashlib.sha256(content).hexdigest()}


class BackupArtifact:
    def __init__(self, temporary, path):
        self.temporary, self.path = temporary, path

    def close(self):
        self.temporary.cleanup()


def _key(password, salt):
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode("utf-8"))


def encrypt_fernet(source, target, password: str, salt: bytes) -> None:
    key, iv = _key(password, salt), os.urandom(16)
    signer = hmac.HMAC(key[:16], hashes.SHA256())
    cipher = Cipher(algorithms.AES(key[16:]), modes.CBC(iv)).encryptor()
    padder, pending = padding.PKCS7(128).padder(), b""

    def emit(value, signed=True):
        nonlocal pending
        if signed:
            signer.update(value)
        pending += value
        end = len(pending) // 3 * 3
        if end:
            target.write(base64.urlsafe_b64encode(pending[:end]))
            pending = pending[end:]

    emit(b"\x80" + struct.pack(">Q", int(time.time())) + iv)
    while block := source.read(BLOCK):
        emit(cipher.update(padder.update(block)))
    emit(cipher.update(padder.finalize()) + cipher.finalize())
    emit(signer.finalize(), signed=False)
    if pending:
        target.write(base64.urlsafe_b64encode(pending))


def decrypt_fernet(source, target, password: str, salt: bytes, limit: int) -> None:
    key = _key(password, salt)
    signer = hmac.HMAC(key[:16], hashes.SHA256())
    tail, prefix, encoded, cipher = b"", b"", b"", None
    unpadder, total, ended = padding.PKCS7(128).unpadder(), 0, False

    def write(value):
        nonlocal total
        total += len(value)
        if total > limit:
            raise AppError(413, "site_backup_too_large", "备份解密后超过恢复上限")
        target.write(value)

    try:
        while chunk := source.read(BLOCK):
            if ended:
                raise InvalidToken
            encoded += chunk
            end = len(encoded) // 4 * 4
            data, encoded = encoded[:end], encoded[end:]
            if not data:
                continue
            ended = b"=" in data
            raw = base64.b64decode(data, altchars=b"-_", validate=True)
            if len(prefix) < 25:
                take = min(25 - len(prefix), len(raw))
                prefix += raw[:take]
                raw = raw[take:]
                if len(prefix) == 25:
                    if prefix[0] != 128:
                        raise InvalidToken
                    signer.update(prefix)
                    cipher = Cipher(algorithms.AES(key[16:]), modes.CBC(prefix[9:])).decryptor()
            tail += raw
            if len(tail) > 32:
                encrypted, tail = tail[:-32], tail[-32:]
                signer.update(encrypted)
                write(unpadder.update(cipher.update(encrypted)))
        if encoded or cipher is None or len(tail) != 32:
            raise InvalidToken
        signer.verify(tail)
        write(unpadder.update(cipher.finalize()) + unpadder.finalize())
    except (ValueError, InvalidSignature, InvalidToken) as exc:
        raise AppError(422, "site_backup_password_invalid", "备份密码错误或加密内容已损坏") from exc


def payload_source(payload):
    if isinstance(payload, (str, Path)):
        return Path(payload).open("rb")
    if isinstance(payload, bytes):
        return io.BytesIO(payload)
    # The caller owns UploadFile's SpooledTemporaryFile. Do not close it here.
    from contextlib import nullcontext
    payload.seek(0)
    return nullcontext(payload)
