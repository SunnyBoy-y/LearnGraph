from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.domain.models import SandboxSession, Workspace, utc_now
from app.domain.schemas.learning import MasterySchedulerTickView
from app.providers.local.memory import LocalWorkspaceMemoryProvider
from app.services.mastery import MasteryService
from app.services.memory import MemoryService
from app.providers.ports.sandbox import SandboxSessionHandle
from app.services.sandbox_bootstrap import backend_for_settings


logger = logging.getLogger(__name__)


def run_workspace_mastery_tick(
    workspace_id: str,
    *,
    now: datetime | None = None,
    execute: bool = True,
    actor_id: str = "system:scheduler",
) -> MasterySchedulerTickView:
    with SessionLocal() as db:
        return MasteryService(db, workspace_id, actor_id).scheduler_tick(
            now=now,
            execute=execute,
        )


def run_mastery_ticks(
    *,
    now: datetime | None = None,
    execute: bool = True,
) -> list[MasterySchedulerTickView]:
    with SessionLocal() as db:
        workspace_ids = list(db.scalars(select(Workspace.id)))
    results: list[MasterySchedulerTickView] = []
    for workspace_id in workspace_ids:
        try:
            results.append(
                run_workspace_mastery_tick(
                    workspace_id,
                    now=now,
                    execute=execute,
                )
            )
        except Exception:
            # One workspace cannot suppress durable work in the others. Its
            # queued/running rows remain in SQLite for the next tick.
            logger.exception(
                "Mastery scheduler tick failed for workspace %s",
                workspace_id,
            )
    return results


async def mastery_scheduler(
    stop: asyncio.Event,
    interval_seconds: int | None = None,
) -> None:
    interval = max(
        1,
        interval_seconds
        if interval_seconds is not None
        else get_settings().mastery_scheduler_interval_seconds,
    )
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_mastery_ticks)
        except Exception:
            logger.exception("Periodic mastery scheduler wake-up failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue


def run_workspace_memory_retention(
    workspace_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Destroy expired recovery keys without contacting the active semantic Provider."""

    settings = get_settings()
    with SessionLocal() as db:
        workspace = db.get(Workspace, workspace_id)
        if workspace is None:
            return {"content_keys_destroyed": 0, "journal_entries_removed": 0}
        provider = LocalWorkspaceMemoryProvider(settings.memory_root, workspace.id)
        return MemoryService(
            db,
            workspace,
            "system:memory-retention",
            provider,
            settings.memory_root,
        ).purge_expired(now=now)


def run_memory_retention_sweeps(*, now: datetime | None = None) -> dict[str, int]:
    with SessionLocal() as db:
        workspace_ids = list(db.scalars(select(Workspace.id)))
    totals = {"content_keys_destroyed": 0, "journal_entries_removed": 0}
    for workspace_id in workspace_ids:
        try:
            result = run_workspace_memory_retention(workspace_id, now=now)
        except Exception:
            logger.exception("Memory retention sweep failed for workspace %s", workspace_id)
            continue
        for key in totals:
            totals[key] += result[key]
    return totals


async def memory_retention_scheduler(
    stop: asyncio.Event,
    interval_seconds: int | None = None,
) -> None:
    interval = max(
        1,
        interval_seconds
        if interval_seconds is not None
        else get_settings().memory_retention_interval_seconds,
    )
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_memory_retention_sweeps)
        except Exception:
            logger.exception("Periodic memory retention wake-up failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue


def run_sandbox_cleanup_sweep(*, now: datetime | None = None) -> dict[str, int]:
    settings = get_settings()
    current = now or utc_now()
    totals = {"cooled": 0, "cleaned": 0, "cleanup_blocked": 0}
    workspace_root = settings.resolved_sandbox_workspace_root
    workspace_root.mkdir(parents=True, exist_ok=True)

    def aware(value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    with SessionLocal() as db:
        sessions = list(
            db.scalars(
                select(SandboxSession).where(
                    SandboxSession.cleanup_status != "cleaned",
                )
            ).all()
        )
        for session in sessions:
            runtime_last = aware(session.runtime_last_used_at)
            runtime_started = aware(session.runtime_started_at)
            workspace_expires = aware(session.workspace_expires_at) or aware(
                session.expires_at
            )
            absolute_expires = aware(session.absolute_expires_at) or workspace_expires
            workspace_expired = bool(
                (workspace_expires and workspace_expires <= current)
                or (absolute_expires and absolute_expires <= current)
            )
            if workspace_expired and session.lifecycle_state in {"STARTING", "RUNNING"}:
                # The wall-time watchdog owns an active command. Never remove
                # its bind mount underneath it; the next sweep can expire it
                # after the command has left an active state.
                continue
            runtime_expired = bool(
                session.backend_session_ref
                and session.lifecycle_state != "RUNNING"
                and (
                    (
                        runtime_last
                        and runtime_last
                        + timedelta(
                            seconds=settings.sandbox_container_idle_ttl_seconds
                        )
                        <= current
                    )
                    or (
                        runtime_started
                        and runtime_started
                        + timedelta(
                            seconds=settings.sandbox_container_absolute_ttl_seconds
                        )
                        <= current
                    )
                )
            )
            if not workspace_expired and not runtime_expired:
                continue
            session.cleanup_status = "running"
            db.commit()
            try:
                if session.backend_session_ref:
                    backend_for_settings(settings, session.runtime_kind).delete(
                        SandboxSessionHandle(session.id, session.backend_session_ref)
                    )
                session.backend_session_ref = None
                session.cleanup_error_class = None
                if workspace_expired:
                    if session.workspace_relative_path:
                        candidate = (
                            workspace_root / session.workspace_relative_path
                        ).resolve()
                        if (
                            candidate == workspace_root
                            or workspace_root not in candidate.parents
                        ):
                            raise RuntimeError(
                                "Sandbox workspace cleanup escaped managed root"
                            )
                        if candidate.exists():
                            shutil.rmtree(candidate)
                    session.status = "deleted"
                    session.lifecycle_state = "EXPIRED"
                    session.cleanup_status = "cleaned"
                    totals["cleaned"] += 1
                else:
                    session.status = "ready"
                    session.lifecycle_state = "COLD"
                    session.cleanup_status = "not_started"
                    totals["cooled"] += 1
            except Exception as exc:
                session.cleanup_status = "cleanup_blocked"
                session.cleanup_error_class = type(exc).__name__
                totals["cleanup_blocked"] += 1
            db.commit()
    return totals


async def sandbox_cleanup_scheduler(
    stop: asyncio.Event,
    interval_seconds: int | None = None,
) -> None:
    interval = max(
        1,
        interval_seconds
        if interval_seconds is not None
        else get_settings().sandbox_cleanup_interval_seconds,
    )
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_sandbox_cleanup_sweep)
        except Exception:
            logger.exception("Periodic sandbox cleanup wake-up failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
