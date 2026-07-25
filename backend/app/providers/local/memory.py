from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from uuid import uuid4

from app.core.errors import AppError
from app.providers.ports.memory import (
    CanonicalMemory,
    ProviderBindingResult,
    ProviderHealth,
)


_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_-]+")
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def _atomic_replace(temporary: Path, target: Path) -> None:
    """Retry transient Windows sharing violations without weakening atomicity."""

    for attempt in range(6):
        try:
            temporary.replace(target)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.01 * (attempt + 1))


class LocalWorkspaceMemoryProvider:
    provider_id = "local_workspace_markdown"
    available = True
    remote_capability = False

    def __init__(self, root: Path, workspace_id: str) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.workspace_id = workspace_id

    def _workspace_root(self) -> Path:
        safe = _SAFE_SEGMENT.sub("_", self.workspace_id)
        path = (self.root / safe).resolve()
        if self.root not in path.parents:
            raise AppError(400, "invalid_workspace", "Invalid workspace memory path")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _resolve(self, relative_path: str) -> Path:
        workspace_root = self._workspace_root()
        path = (workspace_root / relative_path).resolve()
        if workspace_root not in path.parents:
            raise AppError(400, "invalid_memory_path", "Memory path escapes workspace")
        return path

    @staticmethod
    def _marker(memory_id: str) -> tuple[str, str]:
        return f"<!-- lg_memory:{memory_id}:start -->", f"<!-- lg_memory:{memory_id}:end -->"

    @staticmethod
    def _record_payload(memory: CanonicalMemory, *, heading: str = "#") -> str:
        source_ids = ",".join(memory.source_ids)
        return (
            f"<!-- lg_memory_id={memory.memory_id} revision={memory.revision} "
            f"content_sha256={memory.content_hash} zone={memory.zone} "
            f"namespace={memory.namespace} source_ids={source_ids} -->\n"
            f"{heading} {memory.title}\n\n{memory.content.rstrip()}\n"
        )

    def _relative_path(self, memory: CanonicalMemory) -> str:
        if memory.zone == "hot":
            return f"OVERVIEW.md#{memory.memory_id}"
        if memory.zone == "recent":
            day = memory.origin_updated_at.date().isoformat()
            return f"recent/{day}-{memory.memory_id}.md"
        if memory.zone == "archive":
            when = memory.origin_updated_at
            return f"archive/events/{when.year:04d}/{when.month:02d}/{memory.memory_id}/EVENT.md"
        return f"topics/{memory.memory_id}.md"

    def health(self) -> ProviderHealth:
        root = self._workspace_root()
        return ProviderHealth(
            provider_id=self.provider_id,
            available=True,
            status="healthy_local",
            remote_capability=False,
            details={"workspace_root_ready": root.is_dir()},
        )

    def upsert(
        self,
        memory: CanonicalMemory,
        *,
        provider_record_id: str | None = None,
    ) -> ProviderBindingResult:
        relative_path = self._relative_path(memory)
        if provider_record_id and provider_record_id != relative_path:
            self.delete(provider_record_id)
        if relative_path.startswith("OVERVIEW.md#"):
            path = self._resolve("OVERVIEW.md")
        else:
            path = self._resolve(relative_path)
        with _path_lock(path):
            if relative_path.startswith("OVERVIEW.md#"):
                existing = path.read_text(encoding="utf-8") if path.exists() else "# Overview\n\n"
                start, end = self._marker(memory.memory_id)
                section = f"{start}\n{self._record_payload(memory, heading='##')}{end}\n"
                pattern = re.compile(
                    rf"{re.escape(start)}.*?{re.escape(end)}\n?",
                    flags=re.DOTALL,
                )
                payload = pattern.sub(section, existing) if pattern.search(existing) else existing.rstrip() + "\n\n" + section
            else:
                payload = self._record_payload(memory)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{memory.memory_id}.{uuid4()}.tmp")
            temporary.write_text(payload, encoding="utf-8")
            _atomic_replace(temporary, path)
        return ProviderBindingResult(
            provider_record_id=relative_path,
            provider_entity_kind="workspace",
            provider_entity_value=self.workspace_id,
            target_readback_hash=memory.content_hash,
            relative_path=relative_path,
        )

    def delete(self, provider_record_id: str) -> None:
        relative_path, separator, memory_id = provider_record_id.partition("#")
        path = self._resolve(relative_path)
        with _path_lock(path):
            if not separator:
                path.unlink(missing_ok=True)
                return
            if not path.exists():
                return
            start, end = self._marker(memory_id)
            existing = path.read_text(encoding="utf-8")
            payload = re.sub(
                rf"\n?{re.escape(start)}.*?{re.escape(end)}\n?",
                "\n",
                existing,
                flags=re.DOTALL,
            ).rstrip() + "\n"
            temporary = path.with_name(f".{path.name}.{memory_id}.{uuid4()}.tmp")
            temporary.write_text(payload, encoding="utf-8")
            _atomic_replace(temporary, path)

    def read_legacy(self, relative_path: str) -> str:
        """Read pre-Journal Markdown once while lazily creating revision 1."""

        path = self._resolve(relative_path.partition("#")[0])
        if not path.exists():
            raise AppError(404, "memory_content_missing", "Memory file is missing")
        return path.read_text(encoding="utf-8")
