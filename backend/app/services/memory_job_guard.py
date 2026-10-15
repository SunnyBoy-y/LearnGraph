"""Durable per-session leases; no write transaction spans a model call."""
from __future__ import annotations

from contextvars import ContextVar
from datetime import timedelta, timezone
from functools import wraps
import hashlib
import json
import threading
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.domain.models import MemoryJobState, utc_now

LEASE_SECONDS = 600
MAX_ATTEMPTS = 3
_current = ContextVar('memory_job_lease', default=None)

def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value

def job_id_for(workspace_id, kind, resource_id):
    return hashlib.sha256(f'{workspace_id}:{kind}:{resource_id}'.encode()).hexdigest()[:36]

def current_lease():
    return _current.get()

class MemoryJobLease:
    def __init__(self, bind, job_id, token, checkpoint=None, attempt=1):
        self.bind, self.job_id, self.token = bind, job_id, token
        self.checkpoint, self.attempt = dict(checkpoint or {}), attempt
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        def heartbeat():
            while not self._stop.wait(30):
                try:
                    with Session(self.bind) as db:
                        now = utc_now()
                        result = db.execute(update(MemoryJobState).where(
                            MemoryJobState.id == self.job_id,
                            MemoryJobState.lease_token == self.token,
                            MemoryJobState.status == 'running',
                            MemoryJobState.lease_until > now,
                        ).values(lease_until=now + timedelta(seconds=LEASE_SECONDS)))
                        db.commit()
                        if not result.rowcount:
                            return
                except Exception:
                    # A missed heartbeat cannot authorize a stale fenced write.
                    continue
        self._thread = threading.Thread(target=heartbeat, daemon=True, name='memory-job-lease')
        self._thread.start()
        return self

    def fence(self, db):
        now = utc_now()
        result = db.execute(update(MemoryJobState).where(
            MemoryJobState.id == self.job_id, MemoryJobState.lease_token == self.token,
            MemoryJobState.status == 'running', MemoryJobState.lease_until > now,
        ).values(lease_until=now + timedelta(seconds=LEASE_SECONDS)))
        if result.rowcount != 1:
            raise RuntimeError('Memory job lease was lost; refusing stale results')

    def save_checkpoint(self, db, checkpoint):
        self.fence(db)
        db.execute(update(MemoryJobState).where(
            MemoryJobState.id == self.job_id, MemoryJobState.lease_token == self.token
        ).values(checkpoint=dict(checkpoint)))

    def finish(self, *, success, status, error=''):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with Session(self.bind) as db:
            row = db.scalar(select(MemoryJobState).where(
                MemoryJobState.id == self.job_id, MemoryJobState.lease_token == self.token))
            if row is None:
                return
            if status in {"disabled", "policy_disabled", "too_short", "below_threshold", "fresh", "fresh_enough", "unconfigured"}:
                row.attempt_count = 0
            delay = min(600, 30 * (2 ** max(0, row.attempt_count - 1)))
            db.execute(update(MemoryJobState).where(
                MemoryJobState.id == self.job_id, MemoryJobState.lease_token == self.token,
            ).values(status='done' if success else ('exhausted' if row.attempt_count >= MAX_ATTEMPTS else 'retry'),
                     result_status=str(status)[:40], last_error=str(error)[:500],
                     lease_token=None, lease_until=None,
                     next_attempt_at=None if success else utc_now() + timedelta(seconds=delay)))
            db.commit()

def claim_memory_job(db, *, workspace_id, kind, resource_id, window_key, force=False):
    bind = db.get_bind()
    job_id = job_id_for(workspace_id, kind, resource_id)
    now, token = utc_now(), str(uuid4())
    with Session(bind) as lock_db:
        row = lock_db.get(MemoryJobState, job_id)
        if row is None:
            lock_db.add(MemoryJobState(id=job_id, workspace_id=workspace_id, kind=kind,
                resource_id=str(resource_id), window_key=window_key, attempt_count=0, status='ready'))
            try:
                lock_db.commit()
            except IntegrityError:
                lock_db.rollback()
            row = lock_db.get(MemoryJobState, job_id)
        if row is None or (row.lease_token and _aware(row.lease_until) and _aware(row.lease_until) > now):
            return None, 'busy'
        same_window = row.window_key == window_key
        if same_window:
            if row.status == 'done':
                return None, 'already_processed'
            if _aware(row.next_attempt_at) and _aware(row.next_attempt_at) > now:
                return None, 'retry_backoff'
            if row.attempt_count >= MAX_ATTEMPTS and not force:
                return None, 'retry_exhausted'
        attempts = row.attempt_count + 1 if same_window else 1
        if same_window and force and row.attempt_count >= MAX_ATTEMPTS:
            attempts = 1  # Explicit operator retry, only after the cooldown.
        checkpoint = dict(row.checkpoint or {})
        result = lock_db.execute(update(MemoryJobState).where(
            MemoryJobState.id == job_id, MemoryJobState.window_key == row.window_key,
            MemoryJobState.attempt_count == row.attempt_count,
            or_(MemoryJobState.lease_until.is_(None), MemoryJobState.lease_until <= now),
        ).values(window_key=window_key, attempt_count=attempts, lease_token=token,
                 lease_until=now + timedelta(seconds=LEASE_SECONDS), status='running',
                 next_attempt_at=None, last_error=''))
        lock_db.commit()
        if result.rowcount != 1:
            return None, 'busy'
    return MemoryJobLease(bind, job_id, token, checkpoint, attempts).start(), 'claimed'

def guarded_session_job(kind):
    def decorate(fn):
        @wraps(fn)
        def wrapped(db, workspace, session_id, settings, *, actor_id=None, force=False):
            from app.domain.models import MemoryExtractionState, ContextSummary, Message
            from app.services.memory_enhancement import _new_messages_since, load_enhancement_config
            state = db.scalar(select(MemoryExtractionState).where(
                MemoryExtractionState.workspace_id == workspace.id, MemoryExtractionState.session_id == session_id))
            job = db.get(MemoryJobState, job_id_for(workspace.id, kind, session_id))
            if kind == 'extraction':
                pending = _new_messages_since(db, workspace.id, session_id, state)
                if not pending:
                    return {'status': 'no_new_messages', 'drafts_created': 0}
                first = pending[0]
                source = [getattr(state, 'last_message_id', None), first.id, first.content, dict(job.checkpoint or {}) if job else {}]
            else:
                latest = db.scalar(select(ContextSummary).where(
                    ContextSummary.workspace_id == workspace.id, ContextSummary.session_id == session_id,
                    ContextSummary.kind == 'model').order_by(ContextSummary.version.desc()).limit(1))
                # Success changes version; failures cannot reset on each appended turn.
                first = db.scalar(select(Message).where(Message.workspace_id == workspace.id,
                    Message.session_id == session_id, Message.status == 'completed')
                    .order_by(Message.created_at, Message.id).limit(1))
                source = [getattr(latest, 'version', 0), getattr(first, 'id', None), getattr(first, 'content', '')]
            config = load_enhancement_config(db, workspace.id)
            window = hashlib.sha256(json.dumps([source, config], sort_keys=True, default=str).encode()).hexdigest()
            db.commit()
            lease, status = claim_memory_job(db, workspace_id=workspace.id, kind=kind,
                resource_id=session_id, window_key=window, force=force)
            if lease is None:
                return {'status': status, 'drafts_created': 0}
            token = _current.set(lease)
            try:
                db.expire_all()
                kwargs = {'force': force}
                if actor_id is not None:
                    kwargs['actor_id'] = actor_id
                result = fn(db, workspace, session_id, settings, **kwargs)
                status = result.get('status', 'error')
                success = status in {'ok', 'no_new_messages', 'session_deleted'}
                # Cheap skips are not permanent: configuration/thresholds can change.
                db.commit()
                lease.finish(success=success, status=status)
                return result
            except BaseException as exc:
                db.rollback()
                lease.finish(success=False, status='error', error=str(exc))
                raise
            finally:
                _current.reset(token)
        return wrapped
    return decorate

def extraction_batch(messages, checkpoint, char_budget=12000):
    """Return exact text slices and a checkpoint; never advance past unseen text."""
    selected, texts, complete, next_checkpoint = [], {}, [], {}
    remaining = char_budget
    for message in messages:
        content = message.content or ''
        digest = hashlib.sha256(content.encode()).hexdigest()
        offset = int(checkpoint.get('offset', 0)) if checkpoint.get('message_id') == message.id and checkpoint.get('content_hash') == digest else 0
        end = min(len(content), offset + remaining)
        if end <= offset:
            break
        selected.append(message)
        texts[message.id] = content[offset:end]
        remaining -= end - offset
        if end < len(content):
            next_checkpoint = {'message_id': message.id, 'content_hash': digest, 'offset': end}
            break
        complete.append(message)
        if remaining <= 0:
            break
    return selected, texts, complete, next_checkpoint
