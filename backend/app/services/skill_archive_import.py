"""Zip archive skill import: decode, validate, and reuse the manual pipeline.

The archive is unpacked entirely in memory. Only UTF-8 text entries become
package files; binary, oversized, or path-escaping entries are skipped and
reported in the validation report. Nothing from the archive is ever executed
on the host.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import zipfile

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.extension_models import SkillRecord
from app.domain.schemas.extensions import (
    SkillArchiveImportRequest,
    SkillManualImportFile,
    SkillManualImportRequest,
    SkillView,
)
from app.services.skill_market import SkillMarketService
from app.services.skill_package import (
    MAX_SKILL_FILE_BYTES,
    MAX_SKILL_FILES,
    MAX_SKILL_PACKAGE_BYTES,
    normalize_skill_relative_path,
    parse_skill_md_frontmatter,
)

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
# Zip metadata that must never become package content.
IGNORED_SEGMENTS = {"__MACOSX", ".git", ".DS_Store", "Thumbs.db"}


def _decode_archive(archive_base64: str) -> bytes:
    try:
        raw = base64.b64decode(archive_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError(400, "invalid_archive_encoding", "archive_base64 is not valid base64") from exc
    if len(raw) > MAX_SKILL_PACKAGE_BYTES:
        raise AppError(400, "skill_package_too_large", "Archive exceeds the 20 MB limit")
    return raw


class SkillArchiveImportService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.market = SkillMarketService(db, workspace_id, actor_id, settings)

    def import_archive(self, payload: SkillArchiveImportRequest) -> SkillRecord:
        raw = _decode_archive(payload.archive_base64)
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise AppError(400, "invalid_archive", "The upload is not a valid zip archive") from exc

        entries: list[tuple[str, bytes]] = []
        skipped: list[dict[str, str]] = []
        total = 0
        with archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if len(infos) > MAX_SKILL_FILES * 2:
                raise AppError(400, "skill_too_many_files", "Archive contains too many entries")
            for info in infos:
                name = info.filename.replace("\\", "/").strip("/")
                if not name or any(seg in IGNORED_SEGMENTS for seg in name.split("/")):
                    continue
                try:
                    path = normalize_skill_relative_path(name)
                except AppError:
                    skipped.append({"path": name[:200], "reason": "invalid_path"})
                    continue
                if info.file_size > MAX_SKILL_FILE_BYTES:
                    skipped.append({"path": path[:200], "reason": "file_too_large"})
                    continue
                # Bounded read guards against zip-bomb size lies in the header.
                with archive.open(info) as handle:
                    data = handle.read(MAX_SKILL_FILE_BYTES + 1)
                if len(data) > MAX_SKILL_FILE_BYTES:
                    skipped.append({"path": path[:200], "reason": "file_too_large"})
                    continue
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    skipped.append({"path": path[:200], "reason": "not_utf8_text"})
                    continue
                total += len(data)
                if total > MAX_SKILL_PACKAGE_BYTES:
                    raise AppError(400, "skill_package_too_large", "Archive exceeds the 20 MB limit")
                entries.append((path, text.encode("utf-8")))

        entries = self._strip_common_prefix(entries)
        if len(entries) > MAX_SKILL_FILES:
            raise AppError(400, "skill_too_many_files", "Archive exceeds the file count limit")
        skill_md = next((data for path, data in entries if path == "SKILL.md"), None)
        if skill_md is None:
            raise AppError(
                400,
                "skill_md_required",
                "Archive must contain SKILL.md (at its root or inside a single top-level folder)",
            )

        meta, _ = parse_skill_md_frontmatter(skill_md.decode("utf-8"))
        skill_key = payload.skill_key or self._derive_key(meta, payload.filename, raw)
        request = SkillManualImportRequest(
            skill_key=skill_key,
            name=payload.name or (str(meta.get("name")) if meta.get("name") else None),
            source="archive_import",
            version="1.0.0",
            files=[
                SkillManualImportFile(path=path, contents=data.decode("utf-8"))
                for path, data in entries
            ],
        )
        return self.market.import_manual(
            request,
            origin_type="archive_import",
            origin_ref=f"zip:{(payload.filename or 'upload')[:200]}",
            report_extra={
                "archive_sha256": hashlib.sha256(raw).hexdigest(),
                "archive_size_bytes": len(raw),
                "skipped_entries": skipped[:50],
                "skipped_entry_count": len(skipped),
            },
        )

    @staticmethod
    def _strip_common_prefix(entries: list[tuple[str, bytes]]) -> list[tuple[str, bytes]]:
        """Re-root `pkg/SKILL.md` style archives so SKILL.md sits at the top."""

        current = entries
        while current and not any(path == "SKILL.md" for path, _ in current):
            prefixes = {path.split("/", 1)[0] for path, _ in current}
            if len(prefixes) != 1 or not all("/" in path for path, _ in current):
                break
            current = [(path.split("/", 1)[1], data) for path, data in current]
        return current

    @staticmethod
    def _derive_key(meta: dict, filename: str, raw: bytes) -> str:
        candidates = [str(meta.get("name") or ""), (filename or "").rsplit(".", 1)[0]]
        for candidate in candidates:
            key = re.sub(r"[^a-z0-9._-]+", "-", candidate.strip().lower()).strip("-")[:80]
            if KEY_RE.match(key):
                return key
        return f"zip-{hashlib.sha256(raw).hexdigest()[:12]}"

    def skill_view(self, skill: SkillRecord) -> SkillView:
        return SkillView.model_validate(skill)
