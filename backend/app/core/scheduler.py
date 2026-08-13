from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.database import commit_with_locked_retry
from app.core.process_lock import acquire_advisory_lock, release_advisory_lock
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

_SANDBOX_COMMAND_SNAPSHOT_PREFIX = ".learngraph-command-snapshot-"

# Windows refuses to unlink files that carry the read-only attribute, and
# snapshot copies preserve it through shutil.copy2. Clear the bit and retry a
# few times so transient locks (e.g. antivirus scanners) do not fail the sweep.
_WIN_RM_RETRIES = 5
_WIN_RM_RETRY_DELAY_SECONDS = 0.2


def _force_rmtree(path: Path) -> None:
    """Remove a directory tree even when entries carry the read-only attribute.

    Plain ``shutil.rmtree`` raises PermissionError (WinError 5) on Windows as
    soon as a copied file kept its read-only bit. The per-entry error handler
    clears the attribute and briefly retries; on other platforms this behaves
    exactly like ``shutil.rmtree``.
    """

    if os.name != "nt":
        shutil.rmtree(path)
        return

    def _on_rm_error(func, target, exc) -> None:
        # Python 3.12+ onexc passes the OSError; older onerror passes a 3-tuple.
        error = exc[1] if isinstance(exc, tuple) else exc
        if isinstance(error, FileNotFoundError) and Path(target) != path:
            # Raced with a concurrent deletion of an inner entry: fine.
            return
        try:
            os.chmod(target, stat.S_IWRITE)
        except OSError:
            pass
        for attempt in range(_WIN_RM_RETRIES):
            try:
                func(target)
                return
            except OSError as retry_error:
                if attempt == _WIN_RM_RETRIES - 1:
                    raise retry_error from error
                time.sleep(_WIN_RM_RETRY_DELAY_SECONDS)

    try:
        # onexc (OSError instance) is available since Python 3.12.
        shutil.rmtree(path, onexc=_on_rm_error)
    except TypeError:
        shutil.rmtree(path, onerror=_on_rm_error)


def _prune_orphaned_sandbox_snapshots(
    workspace_root: Path,
    *,
    now: datetime,
    grace_seconds: int,
) -> int:
    """Remove stale command safety snapshots left behind by crashed workers.

    A live Agent command owns exactly one snapshot below its user directory and
    removes it in ``finally``. The grace period must stay above the maximum
    command runtime so the periodic sweep cannot race a healthy execution.
    Symlinks and unexpected filesystem entries are never followed or removed.
    """

    cutoff_timestamp = now.timestamp() - max(1, grace_seconds)
    removed = 0
    try:
        owner_directories = tuple(workspace_root.iterdir())
    except OSError:
        logger.exception("Sandbox snapshot root scan failed")
        return 0
    for owner_directory in owner_directories:
        try:
            if owner_directory.is_symlink() or not owner_directory.is_dir():
                continue
            candidates = tuple(owner_directory.iterdir())
        except OSError:
            logger.exception(
                "Sandbox snapshot owner scan failed",
                extra={"owner_directory": str(owner_directory)},
            )
            continue
        for candidate in candidates:
            if not candidate.name.startswith(_SANDBOX_COMMAND_SNAPSHOT_PREFIX):
                continue
            try:
                if candidate.is_symlink() or not candidate.is_dir():
                    continue
                if candidate.stat().st_mtime > cutoff_timestamp:
                    continue
                _force_rmtree(candidate)
                removed += 1
            except FileNotFoundError:
                continue
            except OSError:
                logger.exception(
                    "Orphaned sandbox command snapshot cleanup failed",
                    extra={"snapshot_path": str(candidate)},
                )
    return removed


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
    # B1-7: cross-process mutex so multiple workers do not all run the sweep.
    with SessionLocal() as lock_db:
        lock_token = acquire_advisory_lock(lock_db, "sweep.mastery", ttl_seconds=600)
    if lock_token is None:
        return []
    try:
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
    finally:
        with SessionLocal() as lock_db:
            release_advisory_lock(lock_db, "sweep.mastery", lock_token)


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
        from app.services.memory_zones import reconcile_memory_zones

        zones = reconcile_memory_zones(db, workspace_id, now=now)
        db.commit()
        return {
            **retention,
            **temporal,
            "zones_reviewed": zones.reviewed,
            "zones_changed": zones.changed,
        }


def run_memory_retention_sweeps(*, now: datetime | None = None) -> dict[str, int]:
    with SessionLocal() as db:
        workspace_ids = list(db.scalars(select(Workspace.id)))
    totals = {
        "content_keys_destroyed": 0,
        "journal_entries_removed": 0,
        "reviewed": 0,
        "lapsed": 0,
        "zones_reviewed": 0,
        "zones_changed": 0,
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
        # B1-7: only one process runs each sweep round.
        with SessionLocal() as lock_db:
            lock_token = acquire_advisory_lock(lock_db, "sweep.retention", ttl_seconds=600)
        if lock_token is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                continue
            continue
        try:
            await asyncio.to_thread(run_memory_retention_sweeps)
            with SessionLocal() as lock_db:
                release_advisory_lock(lock_db, "sweep.retention", lock_token)
        except Exception:
            logger.exception("Periodic memory retention wake-up failed")
            with SessionLocal() as lock_db:
                release_advisory_lock(lock_db, "sweep.retention", lock_token)
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
        # B1-7: only one process runs each sweep round.
        with SessionLocal() as lock_db:
            lock_token = acquire_advisory_lock(lock_db, "sweep.extraction", ttl_seconds=600)
        if lock_token is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                continue
            continue
        try:
            await asyncio.to_thread(run_memory_extraction_sweeps)
            with SessionLocal() as lock_db:
                release_advisory_lock(lock_db, "sweep.extraction", lock_token)
        except Exception:
            logger.exception("Periodic memory extraction wake-up failed")
            with SessionLocal() as lock_db:
                release_advisory_lock(lock_db, "sweep.extraction", lock_token)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue


def run_sandbox_cleanup_sweep(*, now: datetime | None = None) -> dict[str, int]:
    settings = get_settings()
    current = now or utc_now()
    totals = {
        "cooled": 0,
        "cleaned": 0,
        "recovered": 0,
        "cleanup_blocked": 0,
        "snapshots_cleaned": 0,
    }
    workspace_root = settings.resolved_sandbox_workspace_root
    workspace_root.mkdir(parents=True, exist_ok=True)
    # Warm web_fetch pool containers are not DB-tracked; prune idle ones here
    # so a long-idle process can never leak warm containers. Best-effort.
    try:
        from app.providers.sandbox_fetch_pool import prune_fetch_pools

        prune_fetch_pools()
    except Exception:
        logger.exception("Web fetch container pool prune failed")

    snapshot_grace_seconds = max(
        settings.sandbox_snapshot_cleanup_grace_seconds,
        settings.sandbox_wall_time_seconds + 120,
    )
    totals["snapshots_cleaned"] = _prune_orphaned_sandbox_snapshots(
        workspace_root,
        now=current,
        grace_seconds=snapshot_grace_seconds,
    )

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
                observed_command_id = session.active_command_id
                observed_lease_token = session.lease_token_hash
                observed_generation = session.command_generation
                claimed = db.execute(
                    update(SandboxSession)
                    .where(
                        SandboxSession.id == session.id,
                        SandboxSession.active_command_id == observed_command_id,
                        SandboxSession.lease_token_hash == observed_lease_token,
                        SandboxSession.command_generation == observed_generation,
                    )
                    .values(
                        lifecycle_state="RECOVERING",
                        lease_token_hash=None,
                        lease_expires_at=None,
                    )
                )
                if claimed.rowcount != 1:
                    db.rollback()
                    continue
                db.commit()
                if session.backend_session_ref:
                    try:
                        backend_for_settings(settings, session.runtime_kind).delete(
                            SandboxSessionHandle(session.id, session.backend_session_ref)
                        )
                    except Exception as exc:
                        session.cleanup_status = "cleanup_blocked"
                        session.cleanup_error_class = type(exc).__name__
                        totals["cleanup_blocked"] += 1
                        commit_with_locked_retry(
                            db,
                            redo=lambda: (
                                setattr(session, "cleanup_status", "cleanup_blocked"),
                                setattr(session, "cleanup_error_class", type(exc).__name__),
                            ),
                        )
                        continue
                if observed_command_id:
                    command = db.get(SandboxAgentCommand, observed_command_id)
                    if command is not None and command.status in {"created", "running"}:
                        command.status = "failed"
                        command.error_class = "sandbox_command_interrupted"
                        command.error_message = "Sandbox command lease expired during backend interruption"
                session.backend_session_ref = None
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
            commit_with_locked_retry(
                db,
                redo=lambda: setattr(session, "cleanup_status", "running"),
            )
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
                            _force_rmtree(candidate)
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
        # B1-7: only one process runs each sweep round.
        with SessionLocal() as lock_db:
            lock_token = acquire_advisory_lock(lock_db, "sweep.sandbox_cleanup", ttl_seconds=600)
        if lock_token is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                continue
            continue
        try:
            await asyncio.to_thread(run_sandbox_cleanup_sweep)
            with SessionLocal() as lock_db:
                release_advisory_lock(lock_db, "sweep.sandbox_cleanup", lock_token)
        except Exception:
            logger.exception("Periodic sandbox cleanup wake-up failed")
            with SessionLocal() as lock_db:
                release_advisory_lock(lock_db, "sweep.sandbox_cleanup", lock_token)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue


def run_mcp_runner_cleanup_sweep(*, now: datetime | None = None) -> dict[str, int]:
    """Reap orphaned isolated MCP stdio runner containers.

    A process crash between ``provision`` and ``terminate`` leaves a durable
    ``MCPRunnerSession`` record in ``running`` state. This sweep deletes every
    ``running`` container whose ``expires_at`` has passed and marks the record
    ``terminated``. The offline deny-by-default posture is unchanged — it only
    removes leaked containers.
    """

    from app.domain.extension_models import MCPRunnerSession
    from app.providers.remote.sandbox import DockerSandboxBackend
    from app.services.sandbox_runtime import resolve_sandbox_image

    settings = get_settings()
    current = now or utc_now()
    totals = {"terminated": 0, "deleted": 0, "skipped": 0}
    image_ref = resolve_sandbox_image(settings)
    backend = DockerSandboxBackend(
        enabled=settings.sandbox_enabled,
        image_ref=image_ref or "",
        runtime_kind="python-node",
        archive_bytes=settings.sandbox_agent_archive_bytes,
    )
    with SessionLocal() as db:
        sessions = list(
            db.scalars(
                select(MCPRunnerSession).where(
                    MCPRunnerSession.status == "running",
                    MCPRunnerSession.expires_at <= current,
                )
            ).all()
        )
        for session in sessions:
            totals["terminated"] += 1
            try:
                backend.delete(
                    backend.resume(session.session_id, session.backend_ref)
                )
                totals["deleted"] += 1
            except Exception:  # noqa: BLE001 - the container may already be gone
                totals["skipped"] += 1
            session.status = "terminated"
        db.commit()
    return totals


async def mcp_runner_cleanup_scheduler(
    stop: asyncio.Event,
    interval_seconds: int | None = None,
) -> None:
    interval = max(
        1,
        interval_seconds
        if interval_seconds is not None
        else get_settings().mcp_stdio_cleanup_interval_seconds,
    )
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_mcp_runner_cleanup_sweep)
        except Exception:
            logger.exception("Periodic MCP runner cleanup wake-up failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue


async def wal_checkpoint_scheduler(
    stop: asyncio.Event,
    interval_seconds: int | None = None,
) -> None:
    """Periodically compact the SQLite WAL so autocheckpoint stays cheap.

    See ``app.core.database.run_wal_checkpoint`` for why: a multi-MB WAL turns
    the first write commit past the autocheckpoint threshold into a long-lived
    holder of the single SQLite write lock, which can starve concurrent
    message streams with ``database is locked``.  The checkpoint itself is
    best-effort and never waits on a busy database; this loop only keeps the
    WAL small so normal commits stay fast.  Disabled when
    ``wal_checkpoint_interval_seconds`` is 0.
    """

    interval = max(
        5,
        interval_seconds
        if interval_seconds is not None
        else get_settings().wal_checkpoint_interval_seconds,
    )
    if interval <= 0:
        return
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_wal_checkpoint)
        except Exception:
            logger.debug("WAL checkpoint round skipped", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
