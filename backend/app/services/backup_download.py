"""Private, expiring, single-use full-site download artifacts shared by workers."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import threading
import time
from uuid import uuid4

from app.core.database_routing import OperationLease, atomic_json
from app.core.errors import AppError
from app.domain.models import AuthSession, User
from app.services.backup_io import small_bytes, work_root

TTL = 600
COOKIE = 'learngraph_backup_download'


def download_root(settings, *, create=True) -> Path:
    parent = work_root(settings,create=create)
    root = parent / 'browser-downloads'
    if root.is_symlink() or root.resolve().parent != parent:
        raise AppError(409, 'site_backup_symlink', '下载暂存目录不能为符号链接')
    if create:
        root.mkdir(mode=0o700, exist_ok=True)
        root.chmod(0o700)
    return root


def directory(settings, ticket: str) -> Path:
    if not re.fullmatch(r'[0-9a-f]{32}', ticket):
        raise AppError(404, 'site_backup_download_missing', '下载授权不存在或已失效')
    root = download_root(settings,create=False)
    path = root / ticket
    if path.is_symlink() or path.resolve().parent != root:
        raise AppError(404, 'site_backup_download_missing', '下载授权不存在或已失效')
    return path


def _remove(path: Path, lease) -> None:
    # These are fresh UUID directories under the dedicated private download
    # root. Never follow a symlink or remove an arbitrary manifest-provided path.
    if path.is_dir() and not path.is_symlink() and path.resolve().parent == path.parent.resolve():
        for name in ('site.zip', 'ticket.json'):
            (path / name).unlink(missing_ok=True)
    lease.close()
    if path.is_dir() and not path.is_symlink():
        (path / 'executor.lock').unlink(missing_ok=True)
        try: path.rmdir()
        except OSError: pass


def _prune(path: Path) -> None:
    if not path.is_dir() or path.is_symlink():
        return
    created = path.stat().st_mtime
    try: lease = OperationLease(path)
    except (BlockingIOError, FileNotFoundError): return
    try:
        metadata = json.loads(small_bytes(path/'ticket.json')) if (path/'ticket.json').exists() else {}
        if metadata.get('expires_at', created+TTL) <= time.time():
            _remove(path, lease)
    finally:
        lease.close()


def prune_downloads(settings, *, schedule=False) -> None:
    root = download_root(settings)
    for path in root.iterdir():
        if not re.fullmatch(r'[0-9a-f]{32}', path.name) or not path.is_dir() or path.is_symlink():
            continue
        _prune(path)
        if schedule and path.exists():
            try: expiry=json.loads(small_bytes(path/'ticket.json')).get('expires_at',time.time()+TTL)
            except (OSError, ValueError): expiry=time.time()+TTL
            timer=threading.Timer(max(1,expiry-time.time()+1),_prune,args=(path,))
            timer.daemon=True;timer.start()


def prepare(artifact, settings, principal) -> tuple[str, str]:
    prune_downloads(settings)
    ticket, token = uuid4().hex, secrets.token_urlsafe(32)
    path=directory(settings,ticket)
    path.mkdir(mode=0o700)
    try:
        artifact.path.replace(path/'site.zip')  # Same data filesystem, no extra multi-GiB copy.
        atomic_json(path/'ticket.json', {'user_id':principal.user_id,'session_id':principal.session_id,
            'token_sha256':hashlib.sha256(token.encode()).hexdigest(),'expires_at':time.time()+TTL,'claimed':False})
    except BaseException:
        (path/'site.zip').unlink(missing_ok=True)
        (path/'ticket.json').unlink(missing_ok=True)
        path.rmdir()
        raise
    finally:
        artifact.close()
    timer=threading.Timer(TTL+1,_prune,args=(path,));timer.daemon=True;timer.start()
    return ticket,token


class Download:
    def __init__(self,path,lease):
        self.path,self.lease=path/'site.zip',lease

    def close(self):
        _remove(self.path.parent,self.lease)


def claim(settings,db,ticket: str,*,token=None,principal=None) -> Download:
    path=directory(settings,ticket)
    if not (path/'ticket.json').is_file():
        raise AppError(404,'site_backup_download_missing','下载授权不存在或已失效')
    metadata=json.loads(small_bytes(path/'ticket.json'))
    cookie_ok=bool(token and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),metadata['token_sha256']))
    bearer_ok=bool(principal and principal.user_id==metadata['user_id'] and principal.session_id==metadata['session_id'])
    if not cookie_ok and not bearer_ok:
        raise AppError(403,'site_backup_download_forbidden','需要原管理员会话的下载授权')
    user,session=db.get(User,metadata['user_id']),db.get(AuthSession,metadata['session_id'])
    expiry=session.expires_at if session else datetime.min.replace(tzinfo=timezone.utc)
    if expiry.tzinfo is None: expiry=expiry.replace(tzinfo=timezone.utc)
    if (user is None or not user.is_system_admin or user.status!='active' or user.must_change_password
        or session is None or session.user_id!=metadata['user_id'] or session.tenant_id!=user.tenant_id or session.revoked_at is not None or expiry<=datetime.now(timezone.utc)):
        raise AppError(403,'site_backup_download_forbidden','管理员权限或登录会话已失效')
    try: lease=OperationLease(path)
    except BlockingIOError as exc:
        raise AppError(409,'site_backup_download_used','此下载授权正在使用') from exc
    try:
        metadata=json.loads(small_bytes(path/'ticket.json'))
        if metadata['expires_at']<=time.time() or metadata['claimed']:
            raise AppError(410,'site_backup_download_used','下载授权已过期或已使用')
        atomic_json(path/'ticket.json',{**metadata,'claimed':True})
        return Download(path,lease)
    except BaseException:
        lease.close()
        raise
