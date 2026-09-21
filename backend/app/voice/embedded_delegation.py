"""Non-blocking Tutor delegation port for the embedded voice runtime.

The foreground Tutor owns the only audio output.  Background research,
reasoning, and tool work is exposed to it through a small deterministic port:
handlers return a receipt immediately and never wait for the background result.

The concrete coordinator lives outside this module.  The embedded runtime can
install an implementation without importing the service layer into the audio
pipeline; when no implementation is installed, the tools are not advertised.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Protocol


class DelegationKind(str, Enum):
    RESEARCH = "research"
    REASONING = "reasoning"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class DelegationRequest:
    """Controlled context handed to the background task coordinator."""

    kind: DelegationKind
    query: str
    purpose: str = ""
    context: str = ""
    voice_session_id: str = ""
    trigger_turn_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskControlRequest:
    task_id: str

    instruction: str = ""


class VoiceDelegationPort(Protocol):
    """Async application boundary used by Tutor tools.

    Implementations must treat the methods as enqueue/control operations and
    return quickly.  Long model or tool execution belongs to the durable task
    runtime, never to the audio event loop.
    """

    async def delegate(self, request: DelegationRequest) -> Mapping[str, Any]: ...

    async def task_status(self, request: TaskControlRequest) -> Mapping[str, Any]: ...

    async def cancel_task(self, request: TaskControlRequest) -> Mapping[str, Any]: ...

    async def revise_task(self, request: TaskControlRequest) -> Mapping[str, Any]: ...

    async def set_task_delivery(self, request: TaskControlRequest) -> Mapping[str, Any]: ...

    async def ready_results(self, *, limit: int = 10) -> list[dict[str, Any]]: ...

    async def acknowledge_result(
        self, task_id: str, *, delivery_state: str = "delivered"
    ) -> Mapping[str, Any]: ...

    async def search_memory(self, query: str, *, limit: int = 5) -> Mapping[str, Any]:
        """Look up long-term memory the way a chat turn would read it."""
        ...

    async def list_artifacts(self, query: str = "", *, limit: int = 5) -> Mapping[str, Any]:
        """List the workspace's artifacts (cards and published collections)."""
        ...


DelegationPortFactory = Callable[[str], VoiceDelegationPort | None]

_PORT_FACTORY: DelegationPortFactory | None = None


def register_delegation_port_factory(factory: DelegationPortFactory | None) -> None:
    """Install the coordinator adapter at application startup.

    This is deliberately a narrow registration hook rather than a global task
    store.  Durable task/session state remains in the coordinator/database.
    Tests and deployments can replace the factory without modifying the voice
    pipeline.
    """

    global _PORT_FACTORY
    _PORT_FACTORY = factory


def resolve_delegation_port(voice_session_id: str) -> VoiceDelegationPort | None:
    if _PORT_FACTORY is None:
        return None
    try:
        return _PORT_FACTORY(voice_session_id)
    except Exception:
        return None


def _receipt(value: Mapping[str, Any] | None, *, operation: str) -> dict[str, Any]:
    result = dict(value or {})
    if "status" not in result:
        result["status"] = "accepted" if operation == "delegate" else "unknown"
    if "task_id" not in result and result.get("subagent_id"):
        result["task_id"] = result["subagent_id"]
    return result


async def dispatch_delegation(
    port: VoiceDelegationPort | None,
    request: DelegationRequest,
    *,
    timeout_secs: float = 2.0,
) -> dict[str, Any]:
    """Invoke the coordinator with a hard latency bound.

    A timeout is not proof that no task was accepted; it is reported as
    ``unknown`` so Tutor never claims completion and the caller can reconcile
    through the durable task list.
    """

    if port is None:
        return {
            "status": "unavailable",
            "reason": "voice_delegation_not_configured",
        }
    try:
        result = await asyncio.wait_for(
            port.delegate(request),
            timeout=max(0.01, float(timeout_secs)),
        )
    except asyncio.TimeoutError:
        return {
            "status": "unknown",
            "reason": "delegation_acceptance_timeout",
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "reason": "delegation_acceptance_failed",
            "message": str(exc)[:200],
        }
    return _receipt(result, operation="delegate")


async def dispatch_task_control(
    port: VoiceDelegationPort | None,
    *,
    operation: str,
    request: TaskControlRequest,
    timeout_secs: float = 2.0,
) -> dict[str, Any]:
    if port is None:
        return {
            "status": "unavailable",
            "task_id": request.task_id,
            "reason": "voice_delegation_not_configured",
        }
    method = {
        "status": port.task_status,
        "cancel": port.cancel_task,
        "revise": port.revise_task,
        "delivery": port.set_task_delivery,
    }.get(operation)
    if method is None:
        return {
            "status": "failed",
            "task_id": request.task_id,
            "reason": "unsupported_task_operation",
        }
    try:
        result = await asyncio.wait_for(
            method(request),
            timeout=max(0.01, float(timeout_secs)),
        )
    except asyncio.TimeoutError:
        return {
            "status": "unknown",
            "task_id": request.task_id,
            "reason": "task_control_timeout",
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "task_id": request.task_id,
            "reason": "task_control_failed",
            "message": str(exc)[:200],
        }
    result = dict(result or {})
    result.setdefault("task_id", request.task_id)
    return result


async def dispatch_memory_search(
    port: VoiceDelegationPort | None,
    query: str,
    *,
    limit: int = 5,
    timeout_secs: float = 3.0,
) -> dict[str, Any]:
    """Run the tutor's memory lookup with a hard bound.

    A lookup the model asked for is still a lookup the user is waiting on, so it
    gets a ceiling and reports a reason instead of raising: the tutor can say it
    did not find anything, which is a worse answer than a match but a far better
    one than a broken turn.
    """

    text = str(query or "").strip()
    if not text:
        return {"items": [], "count": 0, "reason": "empty_query"}
    if port is None:
        return {"items": [], "count": 0, "reason": "voice_tools_unavailable"}
    try:
        result = await asyncio.wait_for(
            port.search_memory(text, limit=max(1, min(int(limit), 20))),
            timeout=max(0.01, float(timeout_secs)),
        )
    except asyncio.TimeoutError:
        return {"items": [], "count": 0, "reason": "memory_search_timeout"}
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {
            "items": [],
            "count": 0,
            "reason": "memory_search_failed",
            "message": str(exc)[:200],
        }
    return dict(result or {})


async def dispatch_artifact_list(
    port: VoiceDelegationPort | None,
    query: str = "",
    *,
    limit: int = 5,
    timeout_secs: float = 3.0,
) -> dict[str, Any]:
    """List the workspace's artifacts for the tutor, bounded like the lookup."""

    if port is None:
        return {"items": [], "count": 0, "reason": "voice_tools_unavailable"}
    try:
        result = await asyncio.wait_for(
            port.list_artifacts(
                str(query or "").strip(),
                limit=max(1, min(int(limit), 20)),
            ),
            timeout=max(0.01, float(timeout_secs)),
        )
    except asyncio.TimeoutError:
        return {"items": [], "count": 0, "reason": "artifact_list_timeout"}
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        return {
            "items": [],
            "count": 0,
            "reason": "artifact_list_failed",
            "message": str(exc)[:200],
        }
    return dict(result or {})
