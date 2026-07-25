"""View-layer skill translation with content-hash cache (D-081)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.extension_models import SkillPackageFile, SkillRecord, SkillTranslationCache
from app.domain.models import new_id, utc_now
from app.domain.schemas.extensions import SkillTranslateRequest, SkillTranslateResponse
from app.providers.factory import model_provider_for_workspace
from app.repositories.audit import AuditRepository
from app.repositories.extensions import SkillRepository
from app.services.session_workspace import BlobStore
from app.services.skill_package import normalize_skill_relative_path


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class SkillTranslationService:
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
        self.skills = SkillRepository(db, workspace_id)
        self.blobs = BlobStore(db, workspace_id, settings)
        self.audit = AuditRepository(db, workspace_id)

    def require_skill(self, skill_id: str) -> SkillRecord:
        return self.skills.require(skill_id, "Skill")

    def translate(
        self, skill_id: str, payload: SkillTranslateRequest
    ) -> SkillTranslateResponse:
        skill = self.require_skill(skill_id)
        path = normalize_skill_relative_path(payload.source_path or "SKILL.md")
        if path.startswith("scripts/"):
            raise AppError(
                400,
                "skill_translate_scripts_blocked",
                "Code files under scripts/ are not translated",
            )
        target = payload.target_locale.strip().replace("_", "-")
        if len(target) < 2:
            raise AppError(400, "invalid_locale", "target_locale is required")

        content_hash = skill.content_hash or skill.manifest_hash
        source_text = ""
        if skill.kind == "agent_skill_package" or skill.package_format == "skill_md_v1":
            row = self.db.scalar(
                select(SkillPackageFile).where(
                    SkillPackageFile.workspace_id == self.workspace_id,
                    SkillPackageFile.skill_id == skill.id,
                    SkillPackageFile.relative_path == path,
                    SkillPackageFile.is_directory.is_(False),
                )
            )
            if row is None:
                raise AppError(404, "skill_file_not_found", "Skill file was not found")
            data = self.blobs.read_bytes(row.blob_sha256)
            try:
                source_text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AppError(415, "skill_file_not_text", "Only UTF-8 text can be translated") from exc
            # File-level hash for cache when package content_hash also tracks tree
            content_hash = hashlib.sha256(data).hexdigest()
        else:
            source_text = skill.instructions_markdown or ""
            if not source_text:
                raise AppError(400, "skill_empty", "Skill has no text to translate")
            content_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()

        provider = model_provider_for_workspace(self.db, self.workspace_id, self.settings)
        if not getattr(provider, "available", False) or not getattr(
            provider, "remote_capability", False
        ):
            raise AppError(
                503,
                "translator_unavailable",
                "No remote model provider is configured for skill translation",
            )
        model_id = getattr(provider, "model_id", "") or "unknown"
        provider_id = getattr(provider, "provider_id", "") or ""

        if not payload.force:
            cached = self.db.scalar(
                select(SkillTranslationCache).where(
                    SkillTranslationCache.workspace_id == self.workspace_id,
                    SkillTranslationCache.content_hash == content_hash,
                    SkillTranslationCache.target_locale == target,
                    SkillTranslationCache.translator_model_id == model_id,
                    SkillTranslationCache.source_path == path,
                )
            )
            if cached is not None:
                return SkillTranslateResponse(
                    skill_id=skill.id,
                    source_path=path,
                    content_hash=content_hash,
                    target_locale=target,
                    translator_model_id=model_id,
                    cached=True,
                    translated_text=cached.translated_text,
                    usage_event_id=cached.usage_event_id,
                )

        prompt = (
            "You are a careful technical translator for AI agent skill documents.\n"
            f"Translate the following skill document into locale '{target}'.\n"
            "Preserve Markdown structure, headings, lists, code fences, and YAML frontmatter keys.\n"
            "Translate frontmatter description/name values when they are natural language.\n"
            "Do not invent new capabilities. Return only the translated document.\n\n"
            "----- BEGIN DOCUMENT -----\n"
            f"{source_text[:18000]}\n"
            "----- END DOCUMENT -----\n"
        )
        # Prefer generate_json with a simple envelope for reliability, fall back to stream_answer join.
        translated = ""
        try:
            result = provider.generate_json(
                prompt
                + "\nRespond as JSON object {\"translated_text\": \"...\"} with the full document.",
                "skill_translation",
                {
                    "type": "object",
                    "properties": {"translated_text": {"type": "string"}},
                    "required": ["translated_text"],
                    "additionalProperties": False,
                },
            )
            translated = str(result.get("translated_text") or "").strip()
        except Exception:
            chunks: list[str] = []
            for piece in provider.stream_answer(prompt):
                if piece:
                    chunks.append(piece)
            translated = "".join(chunks).strip()
        if not translated:
            raise AppError(502, "translation_empty", "Model returned an empty translation")

        cache = SkillTranslationCache(
            id=new_id(),
            workspace_id=self.workspace_id,
            skill_id=skill.id,
            content_hash=content_hash,
            source_path=path,
            source_locale=skill.locale_source or "",
            target_locale=target,
            translator_model_id=model_id,
            translator_provider_id=provider_id,
            translated_text=translated,
            usage_event_id=None,
        )
        self.db.add(cache)
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.translate",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "target_locale": target,
                "source_path": path,
                "content_hash": content_hash,
                "model_id": model_id,
                "provider_id": provider_id,
                "cached": False,
                "feature": "skill_view_translation",
            },
        )
        self.db.commit()
        return SkillTranslateResponse(
            skill_id=skill.id,
            source_path=path,
            content_hash=content_hash,
            target_locale=target,
            translator_model_id=model_id,
            cached=False,
            translated_text=translated,
            usage_event_id=None,
        )
