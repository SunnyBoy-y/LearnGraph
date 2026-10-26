"""Deterministic admission and read-time guards for long-term memory.

An evidence ID proves provenance, not entailment. Automatic extraction only
admits conservative, verbatim user assertions; paraphrases require review.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import MemoryEvidence, MemoryRecord, Message
from app.services.memory_sensitive_filter import SensitiveDataFilter
from app.services.memory_usage import evidence_has_preference


def _normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


_INSTRUCTIONS = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s*(?:instructions?|rules?|prompts?)?"
    r"|忽略(?:之前|以上|所有|先前).{0,12}(?:指令|规则|要求)"
    r"|(?:系统|开发者)消息.{0,10}(?:改成|设为|规则)"
    r"|(?:system|developer)\s*prompt"
    r"|<\s*/?\s*(?:system|developer|assistant|user_memory_summary|eligible_user_evidence|evidence)\b"
    r"|\[\s*(?:SYSTEM|INST)\s*\]|<\|(?:im_start|im_end|system|endoftext)\|>"
    r"|(?:绕过|关闭|禁用).{0,8}(?:安全|审核|权限|校验)"
    r"|(?:bypass|disable).{0,20}(?:safety|security|permission|verification)", re.I,
)
_EXTRA_SECRETS = re.compile(
    r"\bsk-[A-Za-z0-9_-]{12,}\b|\b(?:\d[ -]?){13,19}\b|\b\d{17}[\dXx]\b"
    r"|\b(?:password|passwd|api[_ -]?key|secret)\s*[:=]\s*\S+", re.I,
)


def unsafe_memory_reason(text: str) -> str | None:
    value = _normalized(text)
    if _EXTRA_SECRETS.search(value) or SensitiveDataFilter().scan({"text": value}):
        return "sensitive_content"
    if _INSTRUCTIONS.search(value):
        return "instruction_shaped_memory"
    return None


def require_safe_memory(title: str, content: str) -> None:
    reason = unsafe_memory_reason(f"{title}\n{content}")
    if reason:
        raise AppError(422, "memory_content_blocked", "这段内容不能保存为长期记忆", {"reason": reason})


def direct_user_assertions(text: str) -> str:
    """Exclude quoted/code/document material, questions and hypothetical speech.

    This intentionally favors false negatives. The removed text remains in the
    conversation and can still be explicitly saved by the user after review.
    """
    if unsafe_memory_reason(text):
        return ""
    if re.search(r"(?:翻译|改写|润色|总结|分析|阅读).{0,20}(?:以下|下面|这段|文本|文档)|\b(?:translate|rewrite|summarize)\b", text, re.I):
        return ""
    text = re.sub(r"(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:\n\1|\Z)", "", text)
    text = re.sub(r"<(?P<tag>quote|blockquote|document|file|tool_result)\b[^>]*>[\s\S]*?</(?P=tag)\s*>", "", text, flags=re.I)
    text = re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"|`[^`\n]*`', "", text)
    assertions = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:请(?:你)?(?:帮我)?记住|帮我记住|记住|请记录)\s*[:：]?\s*", "", line)
        if line.lstrip().startswith((">", "```", "~~~")):
            continue
        # A pasted document may occupy the rest of the message without fences.
        if re.search(r"(?:以下|下面).{0,10}(?:原文|文档|文章|引用|示例|例子|内容|文本|材料)|(?:文档|文章|原文|引用|示例|例子|文本|材料)(?:如下)?\s*[:：]|\b(?:document|quote|example)\s*:", line, re.I):
            break
        for sentence in re.split(r"(?<=[。！？!?；;\n])", line):
            if re.search(r"[?？]|假如|假设|如果|例如|比如|角色扮演|他说|她说|文中|作者|这次|本次|当前任务|临时|仅限|\b(?:if|suppose|imagine|said|says|roleplay|temporary)\b", sentence, re.I):
                continue
            # Third-party facts and task imperatives are not personal assertions.
            if re.search(r"(?:^|[\s，,:：])(?:我(?:们)?(?:一直|通常|平时|长期|一般|更|最)?(?:是|在|有|住|喜欢|偏好|习惯|希望|需要|计划|打算|决定|不|已|每|做过|完成|开发|制作|正在)|I\s+(?:am|have|live|like|prefer|want|need|plan|will|do|don't|built|created))", sentence, re.I):
                assertions.append(sentence.strip())
    return "\n".join(assertions)


def _assertion_key(text: str) -> str:
    value = _normalized(text).replace("用户", "我")
    # Preserve decimals, signs, slashes and operators: stripping punctuation
    # would turn 1.5 into 15 and allow an altered numeric claim to pass.
    return re.sub(r"\s+", "", value).strip("。.!！?？;；")


def profile_statement_supported(text: str, statement: str) -> bool:
    """Conservative support check for new facts from the memory composer.

    Accept the common first/third-person rewrite and optional Chinese 的.
    Other paraphrases remain reviewable instead of silently gaining authority.
    """
    key = _assertion_key(statement).replace("的", "")
    return bool(key) and any(
        key == _assertion_key(sentence).replace("的", "")
        for sentence in direct_user_assertions(text).splitlines()
    )


def profile_source_reason(db: Session, workspace_id: str, evidence_ids: list[str], content: str, *, require_verbatim: bool = True) -> str | None:
    rows = list(db.scalars(select(MemoryEvidence).where(
        MemoryEvidence.workspace_id == workspace_id, MemoryEvidence.id.in_(evidence_ids),
    ).execution_options(populate_existing=True)))
    if not evidence_ids or {row.id for row in rows} != set(evidence_ids):
        return "unknown_user_evidence"
    for evidence in rows:
        if evidence.deleted_at or not evidence.profile_eligible or evidence.authorship != "user":
            return "untrusted_user_evidence"
    if not require_verbatim:
        return None
    if not any(
        hashlib.sha256(row.excerpt.encode("utf-8")).hexdigest() == row.content_hash
        and profile_statement_supported(row.excerpt, content)
        for row in rows
    ):
        return "statement_requires_review"
    return None


def extraction_source_reason(
    db: Session, workspace_id: str, evidence_ids: list[str], *,
    content: str, source_quote: str, session_id: str | None = None,
    require_verbatim: bool = True,
) -> str | None:
    if not evidence_ids:
        return "missing_user_evidence"
    rows = list(db.scalars(select(MemoryEvidence).where(
        MemoryEvidence.workspace_id == workspace_id, MemoryEvidence.id.in_(evidence_ids),
    ).execution_options(populate_existing=True)))
    if {row.id for row in rows} != set(evidence_ids):
        return "unknown_user_evidence"
    safe_sources = []
    for evidence in rows:
        if evidence.deleted_at or not evidence.profile_eligible or evidence.authorship != "user" or evidence.source_kind != "user_statement" or not evidence.message_id:
            return "untrusted_user_evidence"
        message = db.scalar(select(Message).where(
            Message.id == evidence.message_id, Message.workspace_id == workspace_id,
        ).execution_options(populate_existing=True))
        if message is None or message.role != "user" or message.status != "completed" or (session_id and message.session_id != session_id):
            return "invalid_source_message"
        digest = hashlib.sha256((message.content or "").strip().encode("utf-8")).hexdigest()
        if digest != evidence.content_hash:
            return "source_content_changed"
        safe_sources.append(direct_user_assertions(message.content or ""))
    if not require_verbatim:
        return None
    quote_key = _assertion_key(source_quote)
    if not quote_key or not any(
        quote_key == _assertion_key(sentence)
        for source in safe_sources for sentence in source.splitlines()
    ):
        return "unverified_source_quote"
    # A real quote must support the whole statement. Possessing a valid ID or
    # attaching an unrelated real quote cannot authorize a fabricated claim.
    statement = _assertion_key(content)
    if not statement or statement != quote_key:
        return "statement_requires_review"
    return None


def record_source_reason(db: Session, record: MemoryRecord, content: str, *, strict_legacy: bool = False) -> str | None:
    reason = unsafe_memory_reason(f"{record.title}\n{content}")
    if reason:
        return reason
    provenance = (record.structured_payload or {}).get("provenance") or {}
    if provenance.get("profile_eligible") is False or provenance.get("authorship") in {"assistant", "tool", "unknown"}:
        return "untrusted_provenance"
    if record.source in {"draft:memory_extraction", "draft:user_profile_intent"}:
        # A later explicit edit replaces the model attribution. Only the actual
        # server-created edit evidence can authorize this, not a JSON flag.
        confirmed = db.scalar(select(MemoryEvidence.id).where(
            MemoryEvidence.workspace_id == record.workspace_id,
            MemoryEvidence.id.in_(list(record.evidence_ids or [])),
            MemoryEvidence.source_kind == "user_memory_edit",
            MemoryEvidence.authorship == "user", MemoryEvidence.profile_eligible.is_(True),
            MemoryEvidence.deleted_at.is_(None), MemoryEvidence.content_hash == record.content_hash,
        ))
        if confirmed:
            return None
        # Old records predate admission proofs. Missing proof is an audit/review
        # signal, not proof of falsehood. Still enforce all live source checks.
        require_verbatim = strict_legacy or provenance.get("admission_version") == 1
        if record.source == "draft:user_profile_intent":
            return profile_source_reason(db, record.workspace_id, list(record.evidence_ids or []), content, require_verbatim=require_verbatim)
        return extraction_source_reason(
            db, record.workspace_id, list(record.evidence_ids or []), content=content,
            source_quote=str((record.structured_payload or {}).get("source_quote") or ""),
            require_verbatim=require_verbatim,
        )
    return None


def record_preference_verified(db: Session, record: MemoryRecord, content: str) -> bool:
    if record.source.startswith("user"):
        return True
    rows = db.scalars(select(MemoryEvidence).where(
        MemoryEvidence.workspace_id == record.workspace_id,
        MemoryEvidence.id.in_(list(record.evidence_ids or [])),
        MemoryEvidence.authorship == "user", MemoryEvidence.profile_eligible.is_(True),
        MemoryEvidence.deleted_at.is_(None),
    )).all()
    for evidence in rows:
        if evidence.source_kind == "user_memory_edit" and evidence.content_hash == record.content_hash:
            return True
        source = evidence.excerpt
        if evidence.message_id:
            message = db.get(Message, evidence.message_id)
            if message is None or message.workspace_id != record.workspace_id or message.role != "user":
                continue
            source = message.content or ""
        if evidence_has_preference(direct_user_assertions(source), content):
            return True
    return False
