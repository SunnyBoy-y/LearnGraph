"""Format interoperability, bounded reads and failure cleanup for large backups."""
import base64
from io import BytesIO
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.services import backup_io
from app.services.backup_io import BLOCK, decrypt_fernet, encrypt_fernet
from app.services.site_backup import _cipher, read_backup
from test_site_backup import site, PASSWORD


class BoundedReader(BytesIO):
    def read(self, count=-1):
        assert 0 <= count <= BLOCK, "unbounded read on streaming encryption path"
        return super().read(count)


@pytest.mark.parametrize("length", [0, 1, 15, 16, BLOCK-1, BLOCK, BLOCK+37])
def test_streaming_fernet_reads_old_tokens_and_produces_standard_tokens(length):
    payload, salt = os.urandom(length), os.urandom(16)
    encrypted = BytesIO()
    encrypt_fernet(BoundedReader(payload), encrypted, PASSWORD, salt)
    assert _cipher(PASSWORD, salt).decrypt(encrypted.getvalue()) == payload
    for token in [encrypted.getvalue(), _cipher(PASSWORD, salt).encrypt(payload)]:
        output = BytesIO()
        decrypt_fernet(BoundedReader(token), output, PASSWORD, salt, length+1)
        assert output.getvalue() == payload


@pytest.mark.parametrize("mutation", ["wrong-password", "truncate", "signature", "append"])
def test_complete_authentication_required(mutation):
    salt, output = os.urandom(16), BytesIO()
    token = _cipher(PASSWORD, salt).encrypt(os.urandom(BLOCK+100))
    password = PASSWORD
    if mutation == "wrong-password": password = "wrong"
    if mutation == "truncate": token = token[:-4]
    if mutation == "signature":
        raw = bytearray(base64.urlsafe_b64decode(token)); raw[-1] ^= 1
        token = base64.urlsafe_b64encode(raw)
    if mutation == "append": token += b"AAAA"
    with pytest.raises(AppError):
        decrypt_fernet(BoundedReader(token), output, password, salt, 2*BLOCK)


def test_file_members_and_restore_never_read_database_or_large_files_as_bytes(site, monkeypatch, tmp_path):
    service, settings, database = site
    large = settings.memory_root / "large.bin"
    with large.open("wb") as output:
        for _ in range(34): output.write(os.urandom(BLOCK))
    service.settings = settings.model_copy(update={"max_backup_bytes":128*BLOCK})
    original = Path.read_bytes
    def guarded(path):
        assert path.stat().st_size <= backup_io.SMALL_MEMBER, "large file loaded into memory"
        assert path.name != "database.sqlite3", "database read_bytes is forbidden"
        return original(path)
    monkeypatch.setattr(Path,"read_bytes",guarded)
    artifact = service.export_file(PASSWORD)
    try:
        with read_backup(artifact.path,PASSWORD,128*BLOCK,directory=tmp_path) as members:
            assert "database.sqlite3" in members
            assert members.entries["memory/large.bin"]["size"] == 34*BLOCK
            with pytest.raises(AppError): members["memory/large.bin"]
        assert service.stage_restore(artifact.path,PASSWORD)["mode"] == "staged"
        from app.services.site_restore import restore_offline
        assert restore_offline(artifact.path,PASSWORD,service.settings,tmp_path/"restored.env")["status"] == "restored"
    finally:
        artifact.close()
    assert not artifact.path.exists()


def test_capacity_preflight_refuses_before_snapshot_and_cleans_plaintext(site, monkeypatch):
    service, _, _ = site
    monkeypatch.setattr(backup_io.shutil,"disk_usage",lambda _:SimpleNamespace(free=1))
    with pytest.raises(AppError) as failure:
        service.export_file(PASSWORD)
    assert failure.value.status_code == 507
    assert not list(backup_io.work_root(service.settings).iterdir())


def test_failed_authentication_removes_quarantined_plaintext(site, tmp_path):
    service, settings, _ = site
    artifact = service.export_file(PASSWORD)
    destination = tmp_path / "parse"
    destination.mkdir()
    try:
        with pytest.raises(AppError):
            read_backup(artifact.path,"wrong",settings.max_backup_bytes,directory=destination)
        assert not list(destination.iterdir())
    finally:
        artifact.close()
