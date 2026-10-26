from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime,timedelta,timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import current_principal, get_db, get_settings
from app.api.routers import migrations
from app.core.config import Settings
from app.core.errors import install_error_handlers
from app.core.security import Principal, hash_password


@pytest.fixture
def api(monkeypatch, tmp_path):
    user = SimpleNamespace(is_system_admin=True, status="active", password_hash=hash_password("Current-admin-password!"),must_change_password=False,tenant_id='tenant')
    principal = Principal(str(uuid4()), "admin", "tenant", "session", is_system_admin=True)
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'site.db').as_posix()}",storage_root=tmp_path/'storage',memory_root=tmp_path/'memory')
    session=SimpleNamespace(user_id=principal.user_id,tenant_id='tenant',revoked_at=None,expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
    state = SimpleNamespace(principal=principal, exported=False, staged=False, settings=settings,user=user,session=session)
    class Service:
        def __init__(self, *_): pass
        def export_file(self, password):
            assert password == "Separate-backup-password!"
            state.exported = True
            from app.services.backup_io import BackupArtifact, private_temp
            temporary = private_temp(directory=tmp_path)
            from pathlib import Path
            path = Path(temporary.name) / "backup.zip"
            path.write_bytes(b"encrypted-fixture-zip")
            return BackupArtifact(temporary, path)
        def stage_restore(self, payload, password):
            assert password == "Separate-backup-password!"
            state.staged = True
            return dict(job_id="job", source_workspace_id=None, tables=2, records=4, files=1, memory_files=1, mode="staged", restore_path="test.zip", restart_required=True)
    monkeypatch.setattr(migrations, "SiteBackupService", Service)
    app = FastAPI()
    app.include_router(migrations.router, prefix="/api/v1")
    install_error_handlers(app)
    app.dependency_overrides[current_principal] = lambda: state.principal
    from app.domain.models import User
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(get=lambda model,*_: user if model is User else session)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, state


def test_admin_export_uses_password_post_and_no_store(api):
    client, state = api
    assert client.get("/api/v1/migrations/backup").status_code == 405
    assert client.post("/api/v1/migrations/backup", json={}).status_code == 422
    wrong = client.post("/api/v1/migrations/backup", json={"current_password": "wrong", "backup_password": "Separate-backup-password!"})
    assert wrong.status_code == 403 and not state.exported
    result = client.post("/api/v1/migrations/backup", json={"current_password": "Current-admin-password!", "backup_password": "Separate-backup-password!"})
    assert result.status_code == 200 and state.exported
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["content-type"] == "application/zip"


def test_workspace_owner_cannot_export_or_restore_site(api):
    client, state = api
    state.principal = Principal(str(uuid4()), "owner", "tenant", "session", is_system_admin=False)
    assert client.get("/api/v1/migrations/backup").status_code == 403
    assert client.post("/api/v1/migrations/backup", json={"current_password": "Current-admin-password!", "backup_password": "Separate-backup-password!"}).status_code == 403
    response = client.post("/api/v1/migrations/restore", files={"backup": ("site.zip", b"zip", "application/zip")}, data={"confirm": "true", "current_password": "Current-admin-password!", "backup_password": "Separate-backup-password!"})
    assert response.status_code == 403
    assert not state.exported and not state.staged


def test_restore_requires_password_and_confirmation_and_returns_staged(api):
    client, state = api
    files = {"backup": ("site.zip", b"zip", "application/zip")}
    data = {"current_password": "Current-admin-password!", "backup_password": "Separate-backup-password!"}
    assert client.post("/api/v1/migrations/restore", files=files, data=data).status_code == 400
    result = client.post("/api/v1/migrations/restore", files=files, data={**data, "confirm": "true"})
    assert result.status_code == 200 and state.staged
    assert result.json()["mode"] == "staged"


def test_restore_upload_rejects_declared_oversize_before_spooling(api):
    client, state = api
    response = client.post('/api/v1/migrations/restore', content=b'not a real upload', headers={'Content-Length':str(32*1024**3), 'Content-Type':'multipart/form-data; boundary=fixture'})
    assert response.status_code == 413 and not state.staged


def test_restore_upload_rejects_insufficient_disk_before_spooling(api, monkeypatch):
    client, state = api
    import app.services.backup_io as io
    monkeypatch.setattr(io.shutil,'disk_usage',lambda _:SimpleNamespace(free=1))
    response = client.post('/api/v1/migrations/restore',content=b'data')
    assert response.status_code == 507 and not state.staged


def test_chunked_oversize_upload_closes_partial_spools(api, monkeypatch):
    client, state = api
    state.settings.max_backup_bytes = 1024 * 1024
    import starlette.formparsers as parser
    original, spools = parser.SpooledTemporaryFile, []
    def spool(*args, **kwargs):
        result = original(*args, **kwargs)
        spools.append(result)
        return result
    monkeypatch.setattr(parser, 'SpooledTemporaryFile', spool)
    def chunks():
        yield b'--test\r\nContent-Disposition: form-data; name="backup"; filename="test.zip"\r\nContent-Type: application/zip\r\n\r\n'
        for _ in range(20): yield b'x' * 128 * 1024
        yield b'\r\n--test--\r\n'
    response = client.post('/api/v1/migrations/restore', content=chunks(), headers={'Content-Type':'multipart/form-data; boundary=test'})
    assert response.status_code == 413 and not state.staged
    assert all(item.closed for item in spools)


def test_non_admin_restore_is_rejected_before_multipart_is_parsed(api, monkeypatch):
    client, state = api
    state.principal = Principal(str(uuid4()), 'owner', 'tenant', 'session', is_system_admin=False)
    def forbidden(*args, **kwargs):
        raise AssertionError('non-admin must never start multipart parsing')
    monkeypatch.setattr(migrations, 'backup_form', forbidden)
    response=client.post('/api/v1/migrations/restore',content=b'body must not be parsed',headers={'Content-Type':'multipart/form-data; boundary=test'})
    assert response.status_code==403 and not state.staged


def test_prepared_browser_download_is_private_single_use_and_cleaned(api):
    client,state=api
    response=client.post('/api/v1/migrations/backup/browser-grants',json={'current_password':'Current-admin-password!','backup_password':'Separate-backup-password!'})
    assert response.status_code==200
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    ticket=response.json()['ticket_id']
    from app.services.backup_download import directory
    path=directory(state.settings,ticket)
    assert b'Separate-backup-password!' not in (path/'ticket.json').read_bytes()
    result=client.get('/api/v1/migrations/backup/browser/'+ticket)
    assert result.status_code==200 and result.content==b'encrypted-fixture-zip'
    assert result.headers['cache-control']=='no-store'
    assert not path.exists()
    assert client.get('/api/v1/migrations/backup/browser/'+ticket).status_code==404


@pytest.mark.parametrize('failure',['no-cookie','revoked-session','demoted','expired','tenant-changed'])
def test_prepared_download_rechecks_authorization_and_expiry(api,failure):
    client,state=api
    prepared=client.post('/api/v1/migrations/backup/browser-grants',json={'current_password':'Current-admin-password!','backup_password':'Separate-backup-password!'})
    assert prepared.status_code==200
    ticket=prepared.json()['ticket_id']
    if failure=='no-cookie': client.cookies.clear()
    if failure=='revoked-session': state.session.revoked_at=datetime.now(timezone.utc)
    if failure=='demoted': state.user.is_system_admin=False
    if failure=='tenant-changed': state.user.tenant_id='another-tenant'
    if failure=='expired':
        import json
        from app.core.database_routing import atomic_json
        from app.services.backup_download import directory
        path=directory(state.settings,ticket)/'ticket.json'
        atomic_json(path,{**json.loads(path.read_text()),'expires_at':0})
    response=client.get('/api/v1/migrations/backup/browser/'+ticket)
    assert response.status_code==(410 if failure=='expired' else 403)
    if failure=='no-cookie':
        from app.services.backup_download import directory
        assert not (directory(state.settings,ticket)/'executor.lock').exists()


def test_unauthorized_download_does_not_create_private_directories(api):
    client,state=api
    root=state.settings.storage_root.parent/'.backup-work'
    assert not root.exists()
    response=client.get('/api/v1/migrations/backup/browser/'+uuid4().hex)
    assert response.status_code==404 and not root.exists()


def test_restore_multipart_spools_only_to_private_data_workspace(api,monkeypatch):
    client,state=api
    import app.api.backup_upload as upload
    from pathlib import Path
    original,locations=upload.TemporaryFile,[]
    def tracked(*args,**kwargs):
        locations.append(Path(kwargs['dir']))
        return original(*args,**kwargs)
    monkeypatch.setattr(upload,'TemporaryFile',tracked)
    result=client.post('/api/v1/migrations/restore',files={'backup':('large.zip',b'x'*(2*1024*1024),'application/zip')},data={'confirm':'true','current_password':'Current-admin-password!','backup_password':'Separate-backup-password!'})
    assert result.status_code==200 and locations
    root=state.settings.storage_root.parent/'.backup-work'
    assert all(path.parent==root and not path.exists() for path in locations)


def test_expired_prepared_file_cleanup_respects_active_download_lease(api,monkeypatch):
    client,state=api
    prepared=client.post('/api/v1/migrations/backup/browser-grants',json={'current_password':'Current-admin-password!','backup_password':'Separate-backup-password!'})
    from app.services import backup_download as downloads
    from app.core.database_routing import OperationLease,atomic_json
    import json
    path=downloads.directory(state.settings,prepared.json()['ticket_id'])
    atomic_json(path/'ticket.json',{**json.loads((path/'ticket.json').read_text()),'expires_at':0})
    lease=OperationLease(path)
    downloads.prune_downloads(state.settings)
    assert (path/'site.zip').exists()
    lease.close()
    downloads.prune_downloads(state.settings)
    assert not path.exists()
