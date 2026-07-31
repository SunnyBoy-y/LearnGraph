from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.domain.models import (
    MemoryProfileSnapshot,
    SandboxAgentCommand,
    SandboxSession,
    Workspace,
    utc_now,
)
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
    """Destroy expired keys and reconcile due temporal memory atoms."""

    settings = get_settings()
    with SessionLocal() as db:
        workspace = db.get(Workspace, workspace_id)
        if workspace is None:
            return {"content_keys_destroyed": 0, "journal_entries_removed": 0}
        provider = LocalWorkspaceMemoryProvider(settings.memory_root, workspace.id)
        retention = MemoryService(
            db,
            workspace,
            "system:memory-retention",
            provider,
            settings.memory_root,
        ).purge_expired(now=now)
        from app.services.memory_profile import reconcile_workspace_temporal_atoms

        temporal = reconcile_workspace_temporal_atoms(
            db,
            workspace,
            settings,
            now=now,
        )
        return {**retention, **temporal}


def run_memory_retention_sweeps(*, now: datetime | None = None) -> dict[str, int]:
    with SessionLocal() as db:
        workspace_ids = list(db.scalars(select(Workspace.id)))
    totals = {
        "content_keys_destroyed": 0,
        "journal_entries_removed": 0,
        "reviewed": 0,
        "lapsed": 0,
    }
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


def run_memory_extraction_sweeps() -> dict[str, int]:
    """ChatGPT-dreaming-style pass over quiet sessions.

    Two independent per-workspace jobs share this cadence: memory extraction
    (MemoryDrafts) and rolling LLM context summaries (ContextSummary
    kind='model'). Each is gated by its own workspace enhancement settings.
    """

    from app.services.memory_enhancement import (
        run_workspace_extraction_sweep,
        run_workspace_summarization_sweep,
    )

    settings = get_settings()
    totals = {
        "sessions_processed": 0,
        "drafts_created": 0,
        "auto_committed": 0,
        "sessions_summarized": 0,
        "profiles_rewritten": 0,
    }
    with SessionLocal() as db:
        workspace_ids = list(db.scalars(select(Workspace.id)))
    for workspace_id in workspace_ids:
        try:
            with SessionLocal() as db:
                workspace = db.get(Workspace, workspace_id)
                if workspace is None:
                    continue
                result = run_workspace_extraction_sweep(
                    db,
                    workspace,
                    settings,
                    idle_seconds=settings.memory_extraction_idle_seconds,
                    sessions_per_sweep=settings.memory_extraction_sessions_per_sweep,
                )
                summarized = run_workspace_summarization_sweep(
                    db,
                    workspace,
                    settings,
                    idle_seconds=settings.memory_extraction_idle_seconds,
                    sessions_per_sweep=settings.memory_extraction_sessions_per_sweep,
                )
                profiles_rewritten = 0
                from app.services.memory_enhancement import load_enhancement_config
                from app.services.memory_profile import (
                    MemoryProfileService,
                    _eligible_profile_records,
                )

                config = load_enhancement_config(db, workspace.id)
                if config["summarization"]["enabled"]:
                    owner_ids = list(
                        dict.fromkeys(
                            db.scalars(
                                select(MemoryProfileSnapshot.owner_subject_id)
                                .where(
                                    MemoryProfileSnapshot.workspace_id
                                    == workspace.id,
                                    MemoryProfileSnapshot.status == "stale",
                                )
                                .order_by(
                                    MemoryProfileSnapshot.updated_at.desc()
                                )
                                .limit(3)
                            ).all()
                        )
                    )
                    owner_has_snapshot = db.scalar(
                        select(MemoryProfileSnapshot.id)
                        .where(
                            MemoryProfileSnapshot.workspace_id == workspace.id,
                            MemoryProfileSnapshot.owner_subject_id
                            == workspace.owner_user_id,
                        )
                        .limit(1)
                    )
                    owner_has_profile_atoms = bool(
                        _eligible_profile_records(db, workspace.id)
                    )
                    if (
                        workspace.owner_user_id
                        and owner_has_snapshot is None
                        and owner_has_profile_atoms
                    ):
                        owner_ids.insert(0, workspace.owner_user_id)
                    for owner_id in owner_ids:
                        try:
                            view = MemoryProfileService(
                                db,
                                workspace,
                                owner_id,
                                settings,
                            ).refresh_profile()
                            profiles_rewritten += int(view.status == "ready")
                        except Exception:
                            logger.exception(
                                "Memory profile rewrite failed for workspace %s owner %s",
                                workspace.id,
                                owner_id,
                            )
                result = {
                    **result,
                    **summarized,
                    "profiles_rewritten": profiles_rewritten,
                }
        except Exception:
            logger.exception(
                "Memory extraction sweep failed for workspace %s", workspace_id
            )
            continue
        for key in totals:
            totals[key] += result.get(key, 0)
    return totals


async def memory_extraction_scheduler(
    stop: asyncio.Event,
    interval_seconds: int | None = None,
) -> None:
    interval = max(
        1,
        interval_seconds
        if interval_seconds is not None
        else get_settings().memory_extraction_interval_seconds,
    )
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_memory_extraction_sweeps)
        except Exception:
            logger.exception("Periodic memory extraction wake-up failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue


def run_sandbox_cleanup_sweep(*, now: datetime | None = None) -> dict[str, int]:
    settings = get_settings()
    current = now or utc_now()
    totals = {"cooled": 0, "cleaned": 0, "recovered": 0, "cleanup_blocked": 0}
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
            if session.lifecycle_state in {"STARTING", "RUNNING"}:
                lease_expires = aware(session.lease_expires_at)
                heartbeat = aware(session.heartbeat_at)
                legacy_stale_at = heartbeat or runtime_last or runtime_started or aware(
                    session.updated_at
                )
                legacy_stale = bool(
                    legacy_stale_at
                    and legacy_stale_at
                    + timedelta(seconds=settings.sandbox_wall_time_seconds + 60)
                    <= current
                )
                lease_stale = bool(lease_expires and lease_expires <= current)
                if not lease_stale and not (lease_expires is None and legacy_stale):
                    continue
                if session.active_command_id:
                    command = db.get(SandboxAgentCommand, session.active_command_id)
                    if command is not None and command.status in {"created", "running"}:
                        command.status = "failed"
                        command.error_class = "sandbox_command_interrupted"
                        command.error_message = "Sandbox command lease expired during backend interruption"
                session.lifecycle_state = "RECOVERING"
                session.active_command_id = None
                session.lease_token_hash = None
                session.lease_expires_at = None
                session.heartbeat_at = current
                db.commit()
                totals["recovered"] += 1
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
