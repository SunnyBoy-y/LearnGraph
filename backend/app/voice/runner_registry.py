"""Process-local registry of running voice bots, with idempotent close.

A voice session is one WebRTC peer plus one audio pipeline: an ASR websocket, a
TTS websocket and a set of transports.  ``DELETE /voice/sessions/{id}`` must
actually stop all of that instead of only flipping a status column, otherwise
the pipeline keeps synthesising audio for a call nobody is listening to.

Two layers make that work across more than one worker:

* this registry — a fast path that closes the runner when the request lands on
  the worker that owns it;
* the durable session row — a worker whose session was closed elsewhere notices
  ``status != "active"`` in its control watchdog and closes itself.

``close`` is idempotent at both layers, so a delete racing a transport drop is
safe.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass
class VoiceRunnerHandle:
    """A registered voice bot for one session."""

    voice_session_id: str
    chat_session_id: str
    owner_user_id: str
    tenant_id: str = ""
    peer_connection_id: str | None = None
    peer_generation: int = 0
    # ``stop`` closes the pipeline and its processors; ``task`` is the asyncio
    # task that owns the runner so cancellation propagates to the websockets.
    stop: Callable[[str], Awaitable[None]] | None = None
    task: asyncio.Task[Any] | None = None
    processors: list[Any] = field(default_factory=list)
    transports: list[Any] = field(default_factory=list)
    closed: bool = False

    def register_processor(self, processor: Any) -> None:
        self.processors.append(processor)

    def register_transport(self, transport: Any) -> None:
        self.transports.append(transport)


_REGISTRY: dict[str, VoiceRunnerHandle] = {}


def register_runner(handle: VoiceRunnerHandle) -> VoiceRunnerHandle:
    existing = _REGISTRY.get(handle.voice_session_id)
    _REGISTRY[handle.voice_session_id] = handle
    if existing is not None and not existing.closed:
        # A reconnect that reaches the same worker must not leave the previous
        # pipeline running: two live pipelines would answer the same turn twice.
        logger.info(
            "Replacing live voice runner for session %s", handle.voice_session_id
        )
        existing.closed = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return existing
        loop.create_task(_close_handle(existing, reason="replaced_by_reconnect"))
    return existing


def unregister_runner(voice_session_id: str) -> None:
    handle = _REGISTRY.get(voice_session_id)
    if handle is not None:
        handle.closed = True
    _REGISTRY.pop(voice_session_id, None)


def get_runner(voice_session_id: str) -> VoiceRunnerHandle | None:
    return _REGISTRY.get(voice_session_id)


def active_voice_session_ids() -> tuple[str, ...]:
    return tuple(_REGISTRY)


async def _close_handle(handle: VoiceRunnerHandle, *, reason: str) -> bool:
    """Tear down one handle: pipeline task, processors, transports.

    Every step is guarded: a already-dead websocket or a cancelled task must not
    turn a hang-up into a 500 for the user.
    """
    if handle.closed and handle.task is not None and handle.task.done():
        return False
    handle.closed = True
    if handle.stop is not None:
        try:
            await handle.stop(reason)
        except Exception:
            logger.warning(
                "voice runner stop failed for session %s",
                handle.voice_session_id,
                exc_info=True,
            )
    for processor in list(handle.processors):
        cleanup = getattr(processor, "cleanup", None)
        if cleanup is None:
            continue
        try:
            result = cleanup()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.debug("processor cleanup failed", exc_info=True)
    for transport in list(handle.transports):
        for method_name in ("disconnect", "close"):
            method = getattr(transport, method_name, None)
            if method is None:
                continue
            try:
                result = method()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.debug("transport %s failed", method_name, exc_info=True)
    task = handle.task
    if task is not None and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except asyncio.TimeoutError:
            logger.warning(
                "voice runner task for session %s did not stop in time",
                handle.voice_session_id,
            )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("voice runner task ended with an error", exc_info=True)
    logger.info(
        "voice runner for session %s closed (%s)", handle.voice_session_id, reason
    )
    return True


async def close_runner(voice_session_id: str, *, reason: str = "client_request") -> bool:
    """Close the local runner for a session.  Idempotent.

    Returns True when this call performed the close.
    """
    handle = _REGISTRY.pop(voice_session_id, None)
    if handle is None:
        return False
    return await _close_handle(handle, reason=reason)


async def close_all_runners(*, reason: str = "shutdown") -> int:
    """Close every local runner; used on application shutdown."""
    closed = 0
    for session_id in list(_REGISTRY):
        if await close_runner(session_id, reason=reason):
            closed += 1
    return closed


def close_local_runner(voice_session_id: str, *, reason: str = "client_request") -> None:
    """Request a local close from synchronous code (an HTTP route).

    The caller is a request handler, which may or may not be running inside an
    event loop.  When there is no loop the bot is not owned by this process
    either, so there is nothing local to close and the durable status is the
    only channel that matters.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _REGISTRY.get(voice_session_id) is None:
        return
    loop.create_task(close_runner(voice_session_id, reason=reason))


def clear_registry() -> None:
    """Test helper: drop all handles without touching their pipelines."""
    _REGISTRY.clear()
