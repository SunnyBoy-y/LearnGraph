"""Atomic memory provenance, temporal reconciliation, and profile summaries.

The profile is a projection over current MemoryRecord atoms. It is never a
fact source: every generated paragraph must cite one or more input atom IDs.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func as sql_func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import (
    MemoryEvidence,
    MemoryProfileSnapshot,
    MemoryRecord,
    MemoryRevision,
    Workspace,
    utc_now,
)
from app.domain.schemas.management import (
    MemoryCreateRequest,
    MemoryDraftCreateRequest,
    MemoryDraftDecisionRequest,
    MemoryProfileIntentRequest,
    MemoryProfileIntentResult,
    MemoryProfileView,
    MemoryUpdateRequest,
)
from app.providers.factory import memory_provider_for_workspace, model_provider_for_workspace
from app.services.billing import BillingService
from app.services.memory import MemoryService
from app.services.memory_enhancement import load_enhancement_config
from app.services.token_estimate import estimate_tokens


PROFILE_PROMPT_VERSION = "memory-profile-v1"
_PROFILE_MAX_ATOMS = 160
_PROFILE_MAX_MARKDOWN_CHARS = 12_000
_EXCERPT_CHARS = 1_200
_ELIGIBLE_SUMMARY = {"durable", "current"}
_INELIGIBLE_TEMPORAL = {
    "cancelled",
    "rescheduled",
    "lapsed_unverified",
    "expired",
}
_ALLOWED_OPERATIONS = {
    "CREATE",
    "UPDATE",
    "CORRECT",
    "CONFIRM",
    "COMPLETE",
    "CANCEL",
    "RESCHEDULE",
    "SUPERSEDE",
    "RETRACT",
    "NOOP",
}
_SENSITIVE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    re.compile(r"\b\d{17}[\dXx]\b"),
    re.compile(r"(?i)\b(?:password|passwd|api[_ -]?key|secret)\s*[:=]\s*\S+"),
)
_INSTRUCTION_PATTERNS = (
    re.compile(r"(?i)ignore\s+(?:all\s+)?previous"),
    re.compile(r"忽略(?:之前|以上|所有).{0,12}(?:指令|规则|要求)"),
    re.compile(r"(?i)system\s*prompt"),
    re.compile(r"(?:系统|开发者)消息.{0,10}(?:改成|设为|规则)"),
)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unsafe_memory_reason(text: str) -> str | None:
    if any(pattern.search(text) for pattern in _SENSITIVE_PATTERNS):
        return "sensitive_content"
    if any(pattern.search(text) for pattern in _INSTRUCTION_PATTERNS):
        return "instruction_shaped_memory"
    return None


def _profile_atom_query(workspace_id: str):
    return (
        select(MemoryRecord)
        .where(
            MemoryRecord.workspace_id == workspace_id,
            MemoryRecord.state == "active",
            MemoryRecord.atom_schema_version >= 1,
            MemoryRecord.ledger_status == "active",
            MemoryRecord.summary_eligibility.in_(tuple(_ELIGIBLE_SUMMARY)),
            ~MemoryRecord.temporal_status.in_(tuple(_INELIGIBLE_TEMPORAL)),
        )
        .order_by(
            MemoryRecord.importance.desc(),
            MemoryRecord.updated_at.desc(),
            MemoryRecord.id,
        )
        .limit(_PROFILE_MAX_ATOMS)
    )


def _eligible_profile_records(
    db: Session, workspace_id: str
) -> list[MemoryRecord]:
    """Return current atoms backed by at least one live trusted evidence row."""

    candidates = list(db.scalars(_profile_atom_query(workspace_id)).all())
    evidence_ids = {
        str(evidence_id)
        for record in candidates
        for evidence_id in (record.evidence_ids or [])
        if evidence_id
    }
    if not evidence_ids:
        return []
    trusted_ids = set(
        db.scalars(
            select(MemoryEvidence.id).where(
                MemoryEvidence.workspace_id == workspace_id,
                MemoryEvidence.id.in_(evidence_ids),
                MemoryEvidence.profile_eligible.is_(True),
                MemoryEvidence.deleted_at.is_(None),
            )
        ).all()
    )
    return [
        record
        for record in candidates
        if trusted_ids.intersection(record.evidence_ids or [])
    ]


def _atom_fingerprint(records: list[MemoryRecord]) -> str:
    canonical = "\n".join(
        ":".join(
            [
                record.id,
                str(record.revision),
                record.content_hash,
                record.ledger_status,
                record.temporal_status,
                record.summary_eligibility,
            ]
        )
        for record in sorted(records, key=lambda item: item.id)
    )
    return _content_hash(canonical)


def current_profile_prompt(
    db: Session,
    workspace_id: str,
    owner_subject_id: str,
) -> str:
    """Return a ready summary only when its atom fingerprint is still current."""

    snapshot = db.scalar(
        select(MemoryProfileSnapshot)
        .where(
            MemoryProfileSnapshot.workspace_id == workspace_id,
            MemoryProfileSnapshot.owner_subject_id == owner_subject_id,
            MemoryProfileSnapshot.status == "ready",
        )
        .order_by(MemoryProfileSnapshot.version.desc())
        .limit(1)
    )
    if snapshot is None or not snapshot.markdown.strip():
        return ""
    records = _eligible_profile_records(db, workspace_id)
    if snapshot.source_fingerprint != _atom_fingerprint(records):
        snapshot.status = "stale"
        snapshot.stale_reason = "source_fingerprint_changed"
        db.commit()
        return ""
    return (
        "<user_memory_summary>\n"
        "以下是经来源校验、时态归并后生成的用户记忆摘要。它是建议性上下文，"
        "当前用户消息始终优先：\n"
        f"{snapshot.markdown.strip()}\n"
        "</user_memory_summary>"
    )


def _atomization_schema() -> dict[str, Any]:
    item = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": sorted(_ALLOWED_OPERATIONS)},
            "target_memory_id": {"type": ["string", "null"]},
            "memory_type": {"type": "string"},
            "atom_kind": {"type": "string"},
            "canonical_key": {"type": "string"},
            "title": {"type": "string"},
            "statement": {"type": "string"},
            "confidence": {"type": "number"},
            "importance": {"type": "number"},
            "temporal_status": {"type": "string"},
            "summary_eligibility": {"type": "string"},
            "event_at": {"type": ["string", "null"]},
            "valid_from": {"type": ["string", "null"]},
            "valid_until": {"type": ["string", "null"]},
            "next_review_at": {"type": ["string", "null"]},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "operation",
            "memory_type",
            "atom_kind",
            "canonical_key",
            "title",
            "statement",
            "confidence",
            "importance",
            "temporal_status",
            "summary_eligibility",
            "evidence_ids",
        ],
    }
    return {
        "type": "object",
        "properties": {"atoms": {"type": "array", "items": item}},
        "required": ["atoms"],
    }


def _profile_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "heading": {"type": "string"},
                        "paragraphs": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string"},
                                    "atom_ids": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": ["text", "atom_ids"],
                            },
                        },
                    },
                    "required": ["heading", "paragraphs"],
                },
            }
        },
        "required": ["sections"],
    }


def _legacy_migration_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "atoms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_memory_id": {"type": "string"},
                        "memory_type": {"type": "string"},
                        "atom_kind": {"type": "string"},
                        "canonical_key": {"type": "string"},
                        "title": {"type": "string"},
                        "statement": {"type": "string"},
                        "temporal_status": {"type": "string"},
                        "summary_eligibility": {"type": "string"},
                        "event_at": {"type": ["string", "null"]},
                        "valid_until": {"type": ["string", "null"]},
                        "next_review_at": {"type": ["string", "null"]},
                        "confidence": {"type": "number"},
                        "importance": {"type": "number"},
                    },
                    "required": [
                        "source_memory_id",
                        "memory_type",
                        "atom_kind",
                        "canonical_key",
                        "title",
                        "statement",
                        "temporal_status",
                        "summary_eligibility",
                        "confidence",
                        "importance",
                    ],
                },
            }
        },
        "required": ["atoms"],
    }


def _profile_verification_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "valid_claim_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
            "violations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claim_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["claim_id", "reason"],
                },
            },
        },
        "required": ["valid_claim_ids", "violations"],
    }


class MemoryProfileService:
    def __init__(
        self,
        db: Session,
        workspace: Workspace,
        actor_id: str,
        settings: Settings,
    ) -> None:
        self.db = db
        self.workspace = workspace
        self.workspace_id = workspace.id
        self.actor_id = actor_id
        self.settings = settings
        self.memory = MemoryService(
            db,
            workspace,
            actor_id,
            memory_provider_for_workspace(
                db, workspace, actor_id, settings
            ),
            settings.memory_root,
        )

    def _model_selection(self) -> tuple[str, str]:
        config = load_enhancement_config(self.db, self.workspace_id)
        summarization = config["summarization"]
        extraction = config["extraction"]
        return (
            summarization["provider_id"] or extraction["provider_id"],
            summarization["model_id"] or extraction["model_id"],
        )

    def _model_json(
        self,
        *,
        prompt: str,
        schema_name: str,
        schema: dict[str, Any],
        feature: str,
        estimated_output_tokens: int,
    ) -> tuple[dict[str, Any], str]:
        provider_id, model_id = self._model_selection()
        if not provider_id or not model_id:
            raise AppError(
                409,
                "memory_profile_model_unconfigured",
                "Configure a memory extraction or summarization model first",
            )
        model = model_provider_for_workspace(
            self.db,
            self.workspace_id,
            self.settings,
            model_id=model_id,
            provider_id=provider_id,
        )
        if not getattr(model, "available", False):
            raise AppError(
                503,
                "memory_profile_model_unavailable",
                "The configured memory profile model is unavailable",
            )
        billing = BillingService(self.db, self.workspace_id, self.actor_id)
        quote = billing.preflight_model_call(
            provider_id=model.provider_id,
            model_id=getattr(model, "model_id", model_id),
            feature=feature,
            estimated_input_tokens=estimate_tokens(prompt),
            estimated_output_tokens=estimated_output_tokens,
            remote_capability=bool(getattr(model, "remote_capability", True)),
        )
        started_at = time.monotonic()
        error: Exception | None = None
        payload: dict[str, Any] = {}
        try:
            payload = model.generate_json(prompt, schema_name, schema)
        except Exception as exc:  # noqa: BLE001 - normalized after usage capture
            error = exc
        usage = dict(getattr(model, "last_usage", {}) or {})
        billing.record_usage(
            quote,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cached_input_tokens=int(usage.get("cached_input_tokens") or 0),
            cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
            attempt=1,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            usage_reported=bool(usage),
        )
        self.db.commit()
        if error is not None:
            raise AppError(
                502,
                f"{feature}_failed",
                f"Memory model call failed: {error}",
            ) from error
        return payload, str(getattr(model, "model_id", model_id))

    def _profile_view(
        self, snapshot: MemoryProfileSnapshot | None
    ) -> MemoryProfileView:
        if snapshot is None:
            return MemoryProfileView(
                workspace_id=self.workspace_id,
                owner_subject_id=self.actor_id,
            )
        return MemoryProfileView(
            id=snapshot.id,
            workspace_id=snapshot.workspace_id,
            owner_subject_id=snapshot.owner_subject_id,
            version=snapshot.version,
            status=snapshot.status,  # type: ignore[arg-type]
            markdown=snapshot.markdown,
            structured_sections=list(snapshot.structured_sections or []),
            source_atom_ids=list(snapshot.source_atom_ids or []),
            source_fingerprint=snapshot.source_fingerprint,
            generated_at=snapshot.generated_at,
            updated_at=snapshot.updated_at,
            stale_reason=snapshot.stale_reason,
        )

    def get_profile(self) -> MemoryProfileView:
        snapshot = self.db.scalar(
            select(MemoryProfileSnapshot)
            .where(
                MemoryProfileSnapshot.workspace_id == self.workspace_id,
                MemoryProfileSnapshot.owner_subject_id == self.actor_id,
                MemoryProfileSnapshot.status.in_(
                    ("ready", "stale", "building", "failed")
                ),
            )
            .order_by(MemoryProfileSnapshot.version.desc())
            .limit(1)
        )
        if snapshot is not None and snapshot.status == "ready":
            records = _eligible_profile_records(self.db, self.workspace_id)
            if snapshot.source_fingerprint != _atom_fingerprint(records):
                snapshot.status = "stale"
                snapshot.stale_reason = "source_fingerprint_changed"
                self.db.commit()
        return self._profile_view(snapshot)

    def profile_sources(self) -> dict[str, Any]:
        snapshot = self.db.scalar(
            select(MemoryProfileSnapshot)
            .where(
                MemoryProfileSnapshot.workspace_id == self.workspace_id,
                MemoryProfileSnapshot.owner_subject_id == self.actor_id,
            )
            .order_by(MemoryProfileSnapshot.version.desc())
            .limit(1)
        )
        if snapshot is None:
            return {"profile_version": 0, "atoms": []}
        claims_by_atom: dict[str, list[str]] = {}
        for claim_id, atom_ids in (snapshot.claim_atom_map or {}).items():
            for atom_id in atom_ids:
                claims_by_atom.setdefault(atom_id, []).append(claim_id)
        records = list(
            self.db.scalars(
                select(MemoryRecord).where(
                    MemoryRecord.workspace_id == self.workspace_id,
                    MemoryRecord.id.in_(
                        list(snapshot.source_atom_ids or []) or [""]
                    ),
                )
            ).all()
        )
        atoms: list[dict[str, Any]] = []
        for record in records:
            evidence_rows = list(
                self.db.scalars(
                    select(MemoryEvidence).where(
                        MemoryEvidence.workspace_id == self.workspace_id,
                        MemoryEvidence.id.in_(
                            list(record.evidence_ids or []) or [""]
                        ),
                    )
                ).all()
            )
            atoms.append(
                {
                    "memory_id": record.id,
                    "title": record.title,
                    "atom_kind": record.atom_kind,
                    "temporal_status": record.temporal_status,
                    "claim_ids": claims_by_atom.get(record.id, []),
                    "evidence": [
                        {
                            "id": item.id,
                            "source_kind": item.source_kind,
                            "authorship": item.authorship,
                            "message_id": item.message_id,
                            "file_id": item.file_id,
                            "excerpt": item.excerpt[:500],
                            "profile_eligible": item.profile_eligible,
                            "eligibility_reason": item.eligibility_reason,
                        }
                        for item in evidence_rows
                    ],
                }
            )
        return {
            "profile_version": snapshot.version,
            "status": snapshot.status,
            "atoms": atoms,
        }

    def _atom_payloads(self) -> tuple[list[MemoryRecord], list[dict[str, Any]]]:
        records = _eligible_profile_records(self.db, self.workspace_id)
        payloads: list[dict[str, Any]] = []
        for record in records:
            revision = self.db.scalar(
                select(MemoryRevision)
                .where(
                    MemoryRevision.workspace_id == self.workspace_id,
                    MemoryRevision.memory_id == record.id,
                    MemoryRevision.revision == record.revision,
                )
                .limit(1)
            )
            if revision is None or not revision.content:
                continue
            payloads.append(
                {
                    "id": record.id,
                    "revision": record.revision,
                    "type": record.record_kind,
                    "atom_kind": record.atom_kind,
                    "title": record.title,
                    "statement": revision.content,
                    "scope_type": record.scope_type,
                    "temporal_status": record.temporal_status,
                    "event_at": record.event_at.isoformat()
                    if record.event_at
                    else None,
                    "valid_until": record.valid_until.isoformat()
                    if record.valid_until
                    else None,
                    "confidence": record.confidence,
                }
            )
        allowed = {item["id"] for item in payloads}
        return [record for record in records if record.id in allowed], payloads

    def refresh_profile(self, *, force: bool = False) -> MemoryProfileView:
        records, atoms = self._atom_payloads()
        if not atoms:
            return MemoryProfileView(
                workspace_id=self.workspace_id,
                owner_subject_id=self.actor_id,
                status="empty",
            )
        fingerprint = _atom_fingerprint(records)
        current = self.db.scalar(
            select(MemoryProfileSnapshot)
            .where(
                MemoryProfileSnapshot.workspace_id == self.workspace_id,
                MemoryProfileSnapshot.owner_subject_id == self.actor_id,
                MemoryProfileSnapshot.status == "ready",
            )
            .order_by(MemoryProfileSnapshot.version.desc())
            .limit(1)
        )
        if (
            current is not None
            and current.source_fingerprint == fingerprint
            and not force
        ):
            return self._profile_view(current)
        prompt = (
            "你是 LearnGraph 的长期记忆摘要整理器。仅依据 ATOMS JSON 写一篇完整的"
            "中文用户记忆摘要。使用第二人称“你”。摘要是高层综合，不必包含所有原子。\n"
            "必须遵守：\n"
            "1. 每个段落只能陈述所引用 atom_ids 能直接支持的事实，绝不推断或发明。\n"
            "2. 合并重复事实；当前计划和稳定偏好分开；不要把一次事件写成习惯。\n"
            "3. 不输出已取消、过期、未验证失效的内容（输入已经过滤，仍须遵守）。\n"
            "4. 输出 2-8 个动态标题，每段 1-4 句；总长度尽量在 1200-2200 中文字符内。\n"
            "5. atom_ids 必须逐字来自输入。\n\n"
            f"ATOMS JSON:\n{json.dumps(atoms, ensure_ascii=False)}"
        )
        payload, model_id = self._model_json(
            prompt=prompt,
            schema_name="memory_profile",
            schema=_profile_schema(),
            feature="memory_profile_summary",
            estimated_output_tokens=2_400,
        )
        allowed_ids = {item["id"] for item in atoms}
        sections: list[dict[str, Any]] = []
        claim_map: dict[str, list[str]] = {}
        markdown_parts: list[str] = []
        raw_sections = payload.get("sections") if isinstance(payload, dict) else None
        for section_index, raw_section in enumerate(raw_sections or []):
            if not isinstance(raw_section, dict):
                continue
            heading = str(raw_section.get("heading") or "").strip()[:80]
            paragraphs: list[dict[str, Any]] = []
            for paragraph_index, raw_paragraph in enumerate(
                raw_section.get("paragraphs") or []
            ):
                if not isinstance(raw_paragraph, dict):
                    continue
                text = str(raw_paragraph.get("text") or "").strip()
                atom_ids = list(
                    dict.fromkeys(
                        str(value)
                        for value in raw_paragraph.get("atom_ids") or []
                        if str(value) in allowed_ids
                    )
                )
                if not text or not atom_ids:
                    continue
                claim_id = f"s{section_index}p{paragraph_index}"
                claim_map[claim_id] = atom_ids
                paragraphs.append(
                    {"id": claim_id, "text": text, "atom_ids": atom_ids}
                )
            if heading and paragraphs:
                sections.append({"heading": heading, "paragraphs": paragraphs})
                markdown_parts.append(f"## {heading}")
                markdown_parts.extend(item["text"] for item in paragraphs)
        markdown = "\n\n".join(markdown_parts).strip()[:_PROFILE_MAX_MARKDOWN_CHARS]
        if not markdown or not claim_map:
            raise AppError(
                502,
                "memory_profile_invalid",
                "The model returned a profile without atom-backed claims",
            )
        claims = [
            {
                "claim_id": paragraph["id"],
                "text": paragraph["text"],
                "atom_ids": paragraph["atom_ids"],
            }
            for section in sections
            for paragraph in section["paragraphs"]
        ]
        verification_prompt = (
            "你是记忆摘要事实校验器。逐条判断 CLAIMS 是否完全由其 atom_ids "
            "对应的 ATOMS 直接支持。任何扩写、身份猜测、因果推断、把一次事件"
            "概括为习惯、或时态不一致，都必须判为无效。只返回可保留的 "
            "valid_claim_ids 和违规原因。\n\n"
            f"ATOMS:\n{json.dumps(atoms, ensure_ascii=False)}\n\n"
            f"CLAIMS:\n{json.dumps(claims, ensure_ascii=False)}"
        )
        verification, _ = self._model_json(
            prompt=verification_prompt,
            schema_name="memory_profile_verification",
            schema=_profile_verification_schema(),
            feature="memory_profile_verification",
            estimated_output_tokens=800,
        )
        valid_claim_ids = {
            str(value)
            for value in verification.get("valid_claim_ids") or []
            if str(value) in claim_map
        }
        verified_sections: list[dict[str, Any]] = []
        verified_claim_map: dict[str, list[str]] = {}
        markdown_parts = []
        for section in sections:
            paragraphs = [
                paragraph
                for paragraph in section["paragraphs"]
                if paragraph["id"] in valid_claim_ids
            ]
            if not paragraphs:
                continue
            verified_sections.append(
                {"heading": section["heading"], "paragraphs": paragraphs}
            )
            markdown_parts.append(f"## {section['heading']}")
            for paragraph in paragraphs:
                verified_claim_map[paragraph["id"]] = paragraph["atom_ids"]
                markdown_parts.append(paragraph["text"])
        sections = verified_sections
        claim_map = verified_claim_map
        markdown = "\n\n".join(markdown_parts).strip()[:_PROFILE_MAX_MARKDOWN_CHARS]
        if not markdown or not claim_map:
            raise AppError(
                502,
                "memory_profile_unsupported_claims",
                "No generated profile claims passed atom entailment verification",
            )
        version = (
            self.db.scalar(
                select(sql_func.max(MemoryProfileSnapshot.version)).where(
                    MemoryProfileSnapshot.workspace_id == self.workspace_id,
                    MemoryProfileSnapshot.owner_subject_id == self.actor_id,
                )
            )
            or 0
        ) + 1
        now = utc_now()
        for snapshot in self.db.scalars(
            select(MemoryProfileSnapshot).where(
                MemoryProfileSnapshot.workspace_id == self.workspace_id,
                MemoryProfileSnapshot.owner_subject_id == self.actor_id,
                MemoryProfileSnapshot.status.in_(("ready", "stale", "failed")),
            )
        ).all():
            snapshot.status = "stale"
            snapshot.stale_reason = "superseded_by_new_profile"
        snapshot = MemoryProfileSnapshot(
            workspace_id=self.workspace_id,
            owner_subject_id=self.actor_id,
            version=version,
            status="ready",
            markdown=markdown,
            structured_sections=sections,
            source_fingerprint=fingerprint,
            source_atom_ids=sorted(allowed_ids),
            claim_atom_map=claim_map,
            prompt_version=PROFILE_PROMPT_VERSION,
            model_id=model_id,
            generated_at=now,
            activated_at=now,
        )
        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)
        return self._profile_view(snapshot)

    def _create_user_evidence(self, text: str) -> MemoryEvidence:
        now = utc_now()
        evidence = MemoryEvidence(
            workspace_id=self.workspace_id,
            source_kind="user_statement",
            source_id=f"profile-intent:{uuid4()}",
            authorship="user",
            observed_at=now,
            content_hash=_content_hash(text),
            excerpt=text[:_EXCERPT_CHARS],
            profile_eligible=True,
            eligibility_reason="explicit_profile_edit",
        )
        self.db.add(evidence)
        self.db.flush()
        return evidence

    def _existing_atom_context(self) -> list[dict[str, Any]]:
        records = list(
            self.db.scalars(
                select(MemoryRecord)
                .where(
                    MemoryRecord.workspace_id == self.workspace_id,
                    MemoryRecord.state == "active",
                    MemoryRecord.atom_schema_version >= 1,
                )
                .order_by(MemoryRecord.updated_at.desc())
                .limit(80)
            ).all()
        )
        return [
            {
                "id": record.id,
                "canonical_key": record.canonical_key,
                "atom_kind": record.atom_kind,
                "title": record.title,
                "ledger_status": record.ledger_status,
                "temporal_status": record.temporal_status,
                "summary_eligibility": record.summary_eligibility,
                "updated_at": record.updated_at.isoformat(),
            }
            for record in records
        ]

    def apply_intent(
        self, request: MemoryProfileIntentRequest
    ) -> MemoryProfileIntentResult:
        text = request.text.strip()
        unsafe_reason = _unsafe_memory_reason(text)
        if unsafe_reason:
            raise AppError(
                422,
                "memory_profile_write_blocked",
                "This text cannot be stored as long-term memory",
                {"reason": unsafe_reason},
            )
        evidence = self._create_user_evidence(text)
        now = utc_now()
        prompt = (
            "你是 LearnGraph 的原子记忆整理器。把本次明确由用户输入的修改意图转换为"
            "最小、规范化、可验证的原子操作。不要保存输入原文，不要发明事实。\n"
            f"可信当前时间：{now.isoformat()}；用户时区：{request.timezone_name}。\n"
            "时间规则：相对日期必须转成绝对 ISO 时间；计划过去不等于完成；"
            "取消用 CANCEL；改期用 RESCHEDULE；没有足够信息则 NOOP。\n"
            "一次事件不能推断成习惯。CREATE 每条只能有一个事实。"
            "所有非 NOOP 操作必须引用本次 evidence_id。"
            "修改已有记忆时必须使用 EXISTING_ATOMS 中的 target_memory_id。\n\n"
            f"evidence_id={evidence.id}\n"
            f"用户输入：{text}\n"
            f"选中的摘要文字：{request.selected_text or '（无）'}\n"
            f"选中的 atom_ids：{json.dumps(request.selected_atom_ids, ensure_ascii=False)}\n"
            "EXISTING_ATOMS:\n"
            f"{json.dumps(self._existing_atom_context(), ensure_ascii=False)}"
        )
        payload, _ = self._model_json(
            prompt=prompt,
            schema_name="memory_atom_intent",
            schema=_atomization_schema(),
            feature="memory_profile_intent",
            estimated_output_tokens=1_400,
        )
        existing_ids = {
            item["id"] for item in self._existing_atom_context()
        }
        affected: list[str] = []
        drafts_created = 0
        auto_committed = 0
        for raw in (payload.get("atoms") or [])[:8]:
            if not isinstance(raw, dict):
                continue
            operation = str(raw.get("operation") or "NOOP").upper()
            if operation not in _ALLOWED_OPERATIONS or operation == "NOOP":
                continue
            target = str(raw.get("target_memory_id") or "").strip() or None
            if operation != "CREATE" and target not in existing_ids:
                continue
            statement = str(raw.get("statement") or "").strip()[:4_000]
            title = str(raw.get("title") or "").strip()[:240]
            if not statement or not title:
                continue
            evidence_ids = [
                str(value) for value in raw.get("evidence_ids") or []
            ]
            if evidence.id not in evidence_ids:
                continue
            confidence = min(1.0, max(0.0, float(raw.get("confidence") or 0.0)))
            importance = min(1.0, max(0.0, float(raw.get("importance") or 0.5)))
            temporal_status = str(raw.get("temporal_status") or "timeless")
            eligibility = str(raw.get("summary_eligibility") or "durable")
            event_at = _parse_iso(raw.get("event_at"))
            valid_until = _parse_iso(raw.get("valid_until"))
            next_review = _parse_iso(raw.get("next_review_at"))
            temporal_anchor = valid_until or event_at
            if temporal_status == "planned" and temporal_anchor is not None:
                if temporal_anchor <= now:
                    temporal_status = "lapsed_unverified"
                    eligibility = "historical"
                    next_review = None
                elif next_review is None:
                    next_review = temporal_anchor + timedelta(days=1)
            elif temporal_status == "planned" and next_review is None:
                next_review = now + timedelta(days=1)
            structured = {
                "atom_schema_version": 1,
                "atom_kind": str(raw.get("atom_kind") or "fact")[:64],
                "canonical_key": str(raw.get("canonical_key") or "")[:240],
                "ledger_status": "active",
                "temporal_status": temporal_status,
                "summary_eligibility": eligibility,
                "event_at": event_at.isoformat() if event_at else None,
                "valid_from": raw.get("valid_from"),
                "valid_until": valid_until.isoformat() if valid_until else None,
                "next_review_at": next_review.isoformat() if next_review else None,
                "last_verified_at": now.isoformat(),
                "timezone_name": request.timezone_name,
                "evidence_ids": [evidence.id],
                "provenance": {
                    "authorship": "user",
                    "source_kinds": ["user_statement"],
                    "profile_eligible": True,
                },
            }
            draft = self.memory.create_draft(
                MemoryDraftCreateRequest(
                    operation=operation,  # type: ignore[arg-type]
                    memory_type=str(raw.get("memory_type") or "semantic_memory"),
                    target_memory_id=target,
                    proposed_scope_type="workspace",
                    title=title,
                    content=statement,
                    structured_payload=structured,
                    source_refs=[
                        {
                            "type": "memory_evidence",
                            "id": evidence.id,
                            "source_kind": "user_statement",
                        }
                    ],
                    confidence=confidence,
                    importance=importance,
                    created_by="user_profile_intent",
                    auto_commit=False,
                )
            )
            drafts_created += 1
            if confidence >= 0.7:
                committed = self.memory.decide_draft(
                    draft.id,
                    MemoryDraftDecisionRequest(
                        decision="commit",
                        reason="explicit_user_profile_intent",
                    ),
                )
                auto_committed += 1
                if committed.result_memory_id:
                    affected.append(committed.result_memory_id)
        self.db.commit()
        profile_status = "stale"
        if auto_committed:
            try:
                profile_status = self.refresh_profile(force=True).status
            except AppError:
                profile_status = "stale"
        return MemoryProfileIntentResult(
            status="ok" if drafts_created else "no_change",
            drafts_created=drafts_created,
            auto_committed=auto_committed,
            affected_memory_ids=affected,
            profile_status=profile_status,
        )

    def migrate_legacy_atoms(self, *, limit: int = 20) -> dict[str, int]:
        """LLM-normalize legacy entries without treating unverified sources as facts."""

        records = list(
            self.db.scalars(
                select(MemoryRecord)
                .where(
                    MemoryRecord.workspace_id == self.workspace_id,
                    MemoryRecord.state == "active",
                    MemoryRecord.atom_schema_version == 0,
                )
                .order_by(MemoryRecord.updated_at, MemoryRecord.id)
                .limit(max(1, min(limit, 50)))
            ).all()
        )
        if not records:
            return {"reviewed": 0, "migrated": 0, "created": 0, "deferred": 0}
        inputs: list[dict[str, Any]] = []
        by_id = {record.id: record for record in records}
        bodies: dict[str, str] = {}
        for record in records:
            revision = self.db.scalar(
                select(MemoryRevision)
                .where(
                    MemoryRevision.workspace_id == self.workspace_id,
                    MemoryRevision.memory_id == record.id,
                    MemoryRevision.revision == record.revision,
                )
                .limit(1)
            )
            if revision is None or not revision.content:
                continue
            bodies[record.id] = revision.content
            inputs.append(
                {
                    "id": record.id,
                    "type": record.record_kind,
                    "title": record.title,
                    "content": revision.content,
                    "source": record.source,
                    "updated_at": record.updated_at.isoformat(),
                }
            )
        if not inputs:
            return {
                "reviewed": len(records),
                "migrated": 0,
                "created": 0,
                "deferred": len(records),
            }
        prompt = (
            "你是 LearnGraph 旧记忆迁移器。把每条 LEGACY_MEMORY 拆成一个或多个"
            "最小原子。只能重述输入已有事实，不能补全或推断。每个原子必须带"
            " source_memory_id。一次事件不能改写成习惯；旧计划若时间已经过去且"
            "没有完成证据，temporal_status=lapsed_unverified、"
            "summary_eligibility=historical。无长期价值的内容可以不输出。"
            "statement 使用中性、精确中文，不复制冗余上下文。\n"
            f"可信当前时间：{utc_now().isoformat()}。\n\n"
            f"LEGACY_MEMORY JSON:\n{json.dumps(inputs, ensure_ascii=False)}"
        )
        payload, _ = self._model_json(
            prompt=prompt,
            schema_name="memory_legacy_atom_migration",
            schema=_legacy_migration_schema(),
            feature="memory_legacy_migration",
            estimated_output_tokens=2_400,
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for raw in payload.get("atoms") or []:
            if not isinstance(raw, dict):
                continue
            source_id = str(raw.get("source_memory_id") or "")
            if source_id not in by_id:
                continue
            if not str(raw.get("statement") or "").strip():
                continue
            grouped.setdefault(source_id, []).append(raw)
        migrated = 0
        created_count = 0
        now = utc_now()
        for source_id, atoms in grouped.items():
            source = by_id[source_id]
            user_confirmed = source.source == "user" or source.source.startswith(
                ("user_", "draft:user")
            )
            evidence = MemoryEvidence(
                workspace_id=self.workspace_id,
                source_kind=(
                    "legacy_user_confirmed"
                    if user_confirmed
                    else "legacy_unverified"
                ),
                source_id=source.id,
                authorship="user" if user_confirmed else "unknown",
                observed_at=source.updated_at,
                content_hash=_content_hash(bodies[source_id]),
                excerpt=bodies[source_id][:_EXCERPT_CHARS],
                profile_eligible=user_confirmed,
                eligibility_reason=(
                    "legacy_user_managed_memory"
                    if user_confirmed
                    else "legacy_source_not_message_granular"
                ),
            )
            self.db.add(evidence)
            self.db.flush()
            for index, raw in enumerate(atoms[:8]):
                temporal = str(raw.get("temporal_status") or "timeless")
                requested_eligibility = str(
                    raw.get("summary_eligibility") or "durable"
                )
                eligibility = (
                    requested_eligibility if user_confirmed else "excluded"
                )
                valid_until = _parse_iso(raw.get("valid_until"))
                event_at = _parse_iso(raw.get("event_at"))
                temporal_anchor = valid_until or event_at
                next_review = _parse_iso(raw.get("next_review_at"))
                if (
                    temporal == "planned"
                    and temporal_anchor is not None
                    and temporal_anchor <= now
                ):
                    temporal = "lapsed_unverified"
                    eligibility = "historical"
                    next_review = None
                elif temporal == "planned" and next_review is None:
                    next_review = (
                        temporal_anchor or now
                    ) + timedelta(days=1)
                structured = {
                    "atom_schema_version": 1,
                    "atom_kind": str(raw.get("atom_kind") or "fact")[:64],
                    "canonical_key": str(
                        raw.get("canonical_key") or ""
                    )[:240],
                    "ledger_status": "active",
                    "temporal_status": temporal,
                    "summary_eligibility": eligibility,
                    "event_at": event_at.isoformat() if event_at else None,
                    "valid_until": (
                        valid_until.isoformat() if valid_until else None
                    ),
                    "next_review_at": (
                        next_review.isoformat() if next_review else None
                    ),
                    "last_verified_at": (
                        now.isoformat() if user_confirmed else None
                    ),
                    "timezone_name": "Asia/Shanghai",
                    "evidence_ids": [evidence.id],
                    "provenance": {
                        "authorship": evidence.authorship,
                        "source_kinds": [evidence.source_kind],
                        "profile_eligible": user_confirmed,
                        "migrated_from": source.id,
                    },
                }
                title = str(raw.get("title") or source.title).strip()[:240]
                statement = str(raw.get("statement") or "").strip()[:4_000]
                confidence = min(
                    1.0, max(0.0, float(raw.get("confidence") or source.confidence))
                )
                importance = min(
                    1.0, max(0.0, float(raw.get("importance") or source.importance))
                )
                if index == 0:
                    self.memory.update(
                        source.id,
                        MemoryUpdateRequest(
                            title=title,
                            content=statement,
                            source_ids=[evidence.id],
                            structured_payload=structured,
                            confidence=confidence,
                            importance=importance,
                            reason="legacy_atom_migration",
                        ),
                    )
                    migrated += 1
                else:
                    self.memory.create(
                        MemoryCreateRequest(
                            title=title,
                            content=statement,
                            record_kind=str(
                                raw.get("memory_type") or source.record_kind
                            ),
                            source="legacy_migration",
                            source_ids=[evidence.id],
                            structured_payload=structured,
                            confidence=confidence,
                            importance=importance,
                        )
                    )
                    created_count += 1
        deferred = len(records) - len(grouped)
        return {
            "reviewed": len(records),
            "migrated": migrated,
            "created": created_count,
            "deferred": deferred,
        }


def reconcile_workspace_temporal_atoms(
    db: Session,
    workspace: Workspace,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Expire unverified plans without ever pretending that they completed."""

    current = _as_utc(now or utc_now())
    records = list(
        db.scalars(
            select(MemoryRecord).where(
                MemoryRecord.workspace_id == workspace.id,
                MemoryRecord.state == "active",
                MemoryRecord.ledger_status == "active",
                MemoryRecord.temporal_status == "planned",
                MemoryRecord.next_review_at.is_not(None),
                MemoryRecord.next_review_at <= current,
            )
        ).all()
    )
    if not records:
        return {"reviewed": 0, "lapsed": 0}
    service = MemoryService(
        db,
        workspace,
        "system:memory-temporal-reconciliation",
        memory_provider_for_workspace(
            db,
            workspace,
            "system:memory-temporal-reconciliation",
            settings,
        ),
        settings.memory_root,
    )
    lapsed = 0
    for record in records:
        structured = dict(record.structured_payload or {})
        structured["temporal_status"] = "lapsed_unverified"
        structured["summary_eligibility"] = "historical"
        structured["next_review_at"] = None
        service.update(
            record.id,
            # Existing content/title remain unchanged; this creates an audited
            # lifecycle revision rather than mutating the atom silently.
            MemoryUpdateRequest(
                structured_payload=structured,
                reason="planned_time_passed_without_completion_evidence",
            ),
        )
        lapsed += 1
    return {"reviewed": len(records), "lapsed": lapsed}
