"""AI cover generation for the graph bookshelf: two phases, two engines.

Phase 1 (draft) is a cheap text call whose only goal is to give the *user*
something to read and edit before any image-model money is spent. Phase 2
(generate) is a durable background job, because an image call takes tens of
seconds and must survive the tab being closed.

Engines:

* ``svg``   — the text model draws a vector cover. No image-model spend, crisp
              at any size, and the result must pass the existing static-SVG
              whitelist before it can be stored.
* ``image`` — the workspace image provider draws a raster cover, which then goes
              through the same decode/crop/re-encode pipeline as a user upload.

The vector engine failing does **not** silently fall back to the deterministic
cover: a user who asked for AI artwork must be told it did not happen rather
than be shown the algorithmic cover and believe it succeeded.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.errors import AppError
from app.domain.graph_cover_models import (
    COVER_ACTIVE_STATUSES,
    COVER_ENGINES,
    GraphCoverJob,
)
from app.domain.models import (
    DurableJob,
    FileRecord,
    Graph,
    GraphNode,
    User,
    Workspace,
    new_id,
    utc_now,
)
from app.domain.schemas.files import FileReferenceCreate
from app.repositories.audit import AuditRepository
from app.services.file_references import FileReferenceService
from app.services.graph_cover import validate_custom_svg
from app.services.graph_cover_management import normalize_cover_bytes

# The model-authored draft is user-facing text, so it is bounded before storage.
DRAFT_MAX_CHARS = 1200
# Confirmed prompts are re-validated here; the API layer applies the same bound.
PROMPT_MAX_CHARS = 2000
# A vector cover must stay well under the whitelist's own 64 KB ceiling.
SVG_MAX_BYTES = 40 * 1024
# Image models may return a larger PNG than an upload would; we crop it anyway.
AI_IMAGE_MAX_BYTES = 8 * 1024 * 1024
# Upstream may happily stream a runaway response; stop reading past this.
_TEXT_READ_LIMIT_FACTOR = 3
_LEASE_SECONDS = 300

IMAGE_ENGINE_FEATURE = "graph_cover_image"
SVG_ENGINE_FEATURE = "graph_cover_svg"
DRAFT_FEATURE = "graph_cover_prompt"


class CoverDraft(BaseModel):
    """Envelope for the draft call: one editable paragraph, nothing else."""

    draft: str = Field(min_length=1, max_length=DRAFT_MAX_CHARS)


class CoverSvg(BaseModel):
    """Envelope for the vector call so the model cannot wrap the SVG in prose."""

    svg: str = Field(min_length=20, max_length=SVG_MAX_BYTES)


DRAFT_PROMPT = (
    "你在为一个学习图谱设计书架封面。请只输出一段中文的「封面画面描述」，供后续绘制使用。\n"
    "图谱标题：{title}\n"
    "图谱节点（按顺序）：{labels}\n"
    "已掌握节点占比：{progress}\n"
    "{hint}"
    "要求：\n"
    "- 只输出一段描述，80~200 字；不要标题、不要分点、不要解释、不要 markdown。\n"
    "- 写清主体画面、构图、配色与氛围；表现知识关联可以用抽象几何、线条、粒子这类语言。\n"
    "- 不要在画面里安排需要精确排版的文字；不要出现真实人物、品牌、商标。\n"
    "- 不要出现暴力、成人或敏感内容。\n"
)

SVG_PROMPT = (
    "根据下面的封面设计描述，输出一个可直接使用的 SVG 封面。\n"
    "设计描述：{prompt}\n"
    "硬性要求（必须全部满足，否则会被安全校验拒绝）：\n"
    "- 只输出 SVG 源码：以 <svg 开头、以 </svg> 结尾。\n"
    '- 根元素必须带 xmlns="http://www.w3.org/2000/svg"、viewBox="0 0 640 300"、'
    'width="640"、height="300"。\n'
    "- 只允许这些元素：svg g defs linearGradient radialGradient stop rect path circle "
    "ellipse line polyline polygon text tspan clipPath mask title desc。\n"
    "- 只允许这些属性：viewBox width height x y x1 y1 x2 y2 cx cy r rx ry d points fill "
    "stroke stroke-width stroke-linecap stroke-linejoin stroke-dasharray opacity "
    "fill-opacity stroke-opacity font-family font-size font-weight text-anchor "
    "dominant-baseline transform id clip-path mask offset stop-color stop-opacity "
    "gradientUnits gradientTransform spreadMethod fx fy fr。\n"
    "- 禁止 style 属性、class 属性、<style>、<script>、<image>、<foreignObject>、href、"
    "任何外部资源、以及 url(...) 里除 url(#局部id) 之外的写法；禁止 DOCTYPE/ENTITY。\n"
    "- 元素总数不超过 60 个；总字节数不超过 40 KB；文字总长不超过 24 个字符。\n"
    "- 画面 640×300 横向，留白合理，不要堆叠过度细节，不要出现乱码文字。\n"
)


# --------------------------------------------------------------------------- #
# Text cleaning
# --------------------------------------------------------------------------- #

def _strip_code_fence(value: str) -> str:
    text = (value or "").strip()
    text = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def clean_draft(value: str) -> str:
    """One editable paragraph: no fences, no bullet lists, bounded length."""
    text = _strip_code_fence(value)
    text = re.sub(r"^\s*(?:封面|设计)?(?:画面)?描述\s*[:：]\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:DRAFT_MAX_CHARS]


def clean_prompt(value: str) -> str:
    """A confirmed prompt is user text: normalize whitespace and bound it."""
    text = re.sub(r"\s+", " ", _strip_code_fence(value)).strip()
    return text[:PROMPT_MAX_CHARS]


def extract_svg(value: str) -> str:
    """Pull a single <svg>...</svg> document out of model output."""
    text = _strip_code_fence(value)
    text = re.sub(r"<\?xml[^>]*\?>", "", text)
    text = re.sub(r"<!\s*DOCTYPE[^>]*>", "", text, flags=re.I)
    start = text.find("<svg")
    end = text.rfind("</svg>")
    if start == -1 or end == -1:
        raise AppError(422, "cover_svg_unusable", "模型没有返回可用的 SVG 封面。")
    return text[start : end + len("</svg>")]


def fallback_draft(title: str, labels: list[str]) -> str:
    """A usable starting point when the drafting model is unavailable.

    The user can still edit this, so a text-model outage degrades the *quality*
    of the draft instead of blocking the whole feature.
    """
    subject = "、".join(labels[:6]) or title
    return clean_draft(
        f"以「{title}」为主题的横向封面：中心是抽象几何主体，"
        f"周围用连线与粒子表现知识点之间的关联（{subject}），"
        "整体色调沉静克制，背景留白，画面简洁现代。"
    )


# --------------------------------------------------------------------------- #
# Provider calls
# --------------------------------------------------------------------------- #

def _record_usage(billing, provider, quote) -> None:
    usage = dict(getattr(provider, "last_usage", {}) or {})
    billing.record_usage(
        quote,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        attempt=1,
        usage_reported=bool(usage),
    )


def _coerce_text_payload(schema: type[BaseModel], text: str) -> dict[str, Any]:
    """Recover a structured payload when the JSON lane failed but text arrived."""
    candidate = _strip_code_fence(text)
    try:
        parsed = json.loads(candidate)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    if schema is CoverSvg:
        return {"svg": extract_svg(candidate)}
    return {"draft": clean_draft(candidate)}


def text_json_call(
    db: Session,
    workspace_id: str,
    actor_id: str,
    prompt: str,
    schema: type[BaseModel],
    *,
    feature: str,
    provider_id: str | None = None,
    model_id: str | None = None,
    estimated_output_tokens: int = 2000,
) -> tuple[Any, str, str]:
    """One text-model call returning ``(value, provider_id, model_id)``.

    JSON output is requested first (providers that support it are far more
    reliable for a structured envelope); a plain-text generation is used as the
    fallback so a provider without JSON mode still works. The failed attempt is
    billed before the fallback runs, because those tokens were spent either way.
    """
    from app.domain.settings import GRAPH_COVER_MODEL_SETTING_KEY
    from app.providers.factory import feature_model_target, model_provider_for_workspace
    from app.services.billing import BillingService

    if provider_id is None and model_id is None:
        target = feature_model_target(db, workspace_id, GRAPH_COVER_MODEL_SETTING_KEY)
    else:
        target = {key: value for key, value in (
            ("provider_id", (provider_id or "").strip()),
            ("model_id", (model_id or "").strip()),
        ) if value}
    provider = model_provider_for_workspace(db, workspace_id, get_settings(), **target)
    if not getattr(provider, "available", True):
        raise AppError(
            503,
            "graph_cover_model_unavailable",
            "请在设置中配置可用的生成模型后再试（或改用「按图谱绘制」）。",
        )
    billing = BillingService(db, workspace_id, actor_id)
    quote = billing.preflight_model_call(
        provider_id=provider.provider_id,
        model_id=provider.model_id,
        feature=feature,
        estimated_input_tokens=max(1, len(prompt) // 2),
        estimated_output_tokens=estimated_output_tokens,
        remote_capability=provider.remote_capability,
    )
    db.commit()
    try:
        payload = provider.generate_json(prompt, schema.__name__, schema.model_json_schema())
    except AppError:
        # A refusal is not an outage: falling back to a second call would both
        # hide the reason from the user and spend their budget twice.
        _record_usage(billing, provider, quote)
        db.commit()
        raise
    except Exception:
        _record_usage(billing, provider, quote)
        db.commit()
        chunks: list[str] = []
        total = 0
        limit = estimated_output_tokens * _TEXT_READ_LIMIT_FACTOR
        for piece in provider.stream_answer(prompt):
            if not piece:
                continue
            chunks.append(piece)
            total += len(piece)
            if total > limit:
                break
        _record_usage(billing, provider, quote)
        db.commit()
        return (
            schema.model_validate(_coerce_text_payload(schema, "".join(chunks))),
            provider.provider_id,
            provider.model_id,
        )
    _record_usage(billing, provider, quote)
    db.commit()
    return schema.model_validate(payload), provider.provider_id, provider.model_id


def _render_svg_cover(
    db: Session,
    workspace_id: str,
    actor_id: str,
    prompt: str,
    *,
    provider_id: str | None,
    model_id: str | None,
) -> tuple[str, str, str]:
    """Return (cover data URL, provider_id, model_id); validates and retries once."""
    attempt_prompt = SVG_PROMPT.format(prompt=prompt)
    last_error = ""
    for attempt in (1, 2):
        value, used_provider, used_model = text_json_call(
            db,
            workspace_id,
            actor_id,
            attempt_prompt,
            CoverSvg,
            feature=SVG_ENGINE_FEATURE,
            provider_id=provider_id,
            model_id=model_id,
            estimated_output_tokens=6000,
        )
        svg = extract_svg(value.svg)
        if len(svg.encode("utf-8")) > SVG_MAX_BYTES:
            last_error = f"生成的 SVG 过大（{len(svg.encode('utf-8'))} 字节）"
        else:
            try:
                return validate_custom_svg(svg), used_provider, used_model
            except (ValueError, UnicodeError, SyntaxError) as exc:
                last_error = str(exc) or "SVG 未通过安全校验"
        if attempt == 1:
            # Hand the concrete rejection back to the model instead of asking for
            # a blind retry: most failures are one forbidden attribute away.
            attempt_prompt = (
                SVG_PROMPT.format(prompt=prompt)
                + f"\n上一次输出被拒绝，原因：{last_error}\n请修正后重新输出完整 SVG。"
            )
    raise AppError(502, "cover_svg_rejected", f"生成的 SVG 未通过安全校验：{last_error}"[:300])


def _generate_image_cover(
    db: Session,
    workspace_id: str,
    actor_id: str,
    prompt: str,
    *,
    provider_id: str | None,
    model_id: str | None,
) -> tuple[bytes, str, str, str]:
    """Return (raw bytes, mime, provider_id, model_id) from the image provider."""
    from app.providers.factory import image_provider_for_workspace
    from app.providers.ports.image_generation import ImageGenerationRequest
    from app.services.billing import BillingService

    provider = image_provider_for_workspace(
        db, workspace_id, get_settings(), model_id=model_id, provider_id=provider_id
    )
    if not getattr(provider, "available", False):
        raise AppError(
            503,
            "graph_cover_image_unavailable",
            "工作区未启用图片生成模型，请到 Provider 管理启用后再试。",
        )
    billing = BillingService(db, workspace_id, actor_id)
    quote = billing.preflight_model_call(
        provider_id=provider.provider_id,
        model_id=provider.model_id,
        feature=IMAGE_ENGINE_FEATURE,
        estimated_input_tokens=max(1, len(prompt) // 2),
        estimated_output_tokens=2000,
        remote_capability=provider.remote_capability,
    )
    db.commit()

    def draw(size: str):
        final = None
        for event in provider.stream_generate(
            ImageGenerationRequest(prompt=prompt, partial_images=0, size=size)
        ):
            if event.type == "completed":
                final = event
        return final

    try:
        # Landscape first: a 2.13:1 cover crops far less out of a landscape frame
        # than out of a square default.
        final = draw("1536x1024")
    except AppError:
        raise
    except Exception:
        final = draw("auto")
    _record_usage(billing, provider, quote)
    db.commit()
    if (
        final is None
        or not final.image_bytes
        or final.mime_type not in {"image/png", "image/jpeg", "image/webp"}
    ):
        raise AppError(502, "cover_image_empty", "图片模型没有返回可用的图片。")
    return final.image_bytes, final.mime_type, provider.provider_id, provider.model_id


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #

class GraphCoverAIService:
    """Permission boundary and job queue for AI cover generation."""

    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        *,
        can_access,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.can_access = can_access

    # -- helpers ---------------------------------------------------------- #

    def _graph(self, graph_id: str, permission: str) -> Graph:
        graph = self.db.scalar(
            select(Graph).where(
                Graph.workspace_id == self.workspace_id, Graph.id == graph_id
            )
        )
        if graph is None or not self.can_access(graph_id, permission):
            raise AppError(404, "graph_not_found", "Graph was not found")
        return graph

    def _nodes(self, graph_id: str) -> list[GraphNode]:
        return list(
            self.db.scalars(
                select(GraphNode)
                .where(
                    GraphNode.workspace_id == self.workspace_id,
                    GraphNode.graph_id == graph_id,
                )
                .order_by(GraphNode.id)
            ).all()
        )

    def active_job(self, graph_id: str) -> GraphCoverJob | None:
        return self.db.scalar(
            select(GraphCoverJob)
            .where(
                GraphCoverJob.workspace_id == self.workspace_id,
                GraphCoverJob.graph_id == graph_id,
                GraphCoverJob.status.in_(COVER_ACTIVE_STATUSES),
            )
            .order_by(GraphCoverJob.created_at.desc())
        )

    def latest_job(self, graph_id: str) -> GraphCoverJob | None:
        return self.db.scalar(
            select(GraphCoverJob)
            .where(
                GraphCoverJob.workspace_id == self.workspace_id,
                GraphCoverJob.graph_id == graph_id,
            )
            .order_by(GraphCoverJob.created_at.desc())
        )

    @staticmethod
    def _view(job: GraphCoverJob | None) -> dict[str, Any]:
        if job is None:
            return {
                "id": None,
                "graph_id": None,
                "engine": None,
                "status": "idle",
                "prompt": "",
                "prompt_source": "",
                "provider_id": None,
                "model_id": None,
                "file_id": None,
                "error": None,
                "active": False,
                "created_at": None,
                "updated_at": None,
            }
        return {
            "id": job.id,
            "graph_id": job.graph_id,
            "engine": job.engine,
            "status": job.status,
            "prompt": job.prompt,
            "prompt_source": job.prompt_source,
            "provider_id": job.provider_id,
            "model_id": job.model_id,
            "file_id": job.file_id,
            "error": job.error,
            "active": job.status in COVER_ACTIVE_STATUSES,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    # -- phase 1: draft --------------------------------------------------- #

    def draft(
        self,
        graph_id: str,
        *,
        engine: str,
        hint: str = "",
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Draft the cover brief the user will read and edit. Never draws."""
        graph = self._graph(graph_id, "write")
        engine = engine if engine in COVER_ENGINES else "svg"
        nodes = self._nodes(graph_id)
        labels = [node.label for node in nodes if node.label][:8]
        mastered = sum(node.mastery_stars >= 3 for node in nodes)
        progress = f"{round(mastered / len(nodes) * 100)}%" if nodes else "0%"
        clean_hint = clean_prompt(hint)
        prompt = DRAFT_PROMPT.format(
            title=graph.title,
            labels="、".join(labels) or graph.title,
            progress=progress,
            hint=f"补充要求：{clean_hint}\n" if clean_hint else "",
        )
        try:
            value, _, _ = text_json_call(
                self.db,
                self.workspace_id,
                self.actor_id,
                prompt,
                CoverDraft,
                feature=DRAFT_FEATURE,
                provider_id=provider_id,
                model_id=model_id,
                estimated_output_tokens=800,
            )
            draft = clean_draft(value.draft)
            if not draft:
                raise AppError(502, "cover_draft_empty", "模型没有返回可用的封面描述。")
            return {"engine": engine, "prompt": draft, "prompt_source": "model"}
        except AppError as exc:
            # A drafting outage (or no text model at all) degrades to a template
            # draft the user can still edit. Budget and permission refusals must
            # surface instead: they are the user's to fix, not ours to paper over.
            if exc.status_code < 500:
                raise
            return {
                "engine": engine,
                "prompt": fallback_draft(graph.title, labels),
                "prompt_source": "fallback",
            }
        except Exception:
            return {
                "engine": engine,
                "prompt": fallback_draft(graph.title, labels),
                "prompt_source": "fallback",
            }

    # -- phase 2: submit -------------------------------------------------- #

    def submit(
        self,
        graph_id: str,
        *,
        engine: str,
        prompt: str,
        prompt_source: str = "user_edited",
        provider_id: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        graph = self._graph(graph_id, "write")
        if engine not in COVER_ENGINES:
            raise AppError(422, "cover_engine_invalid", "请选择矢量或位图引擎。")
        confirmed = clean_prompt(prompt)
        if not confirmed:
            raise AppError(422, "cover_prompt_required", "请先确认封面描述。")
        existing = self.active_job(graph_id)
        if existing is not None:
            # One active generation per graph: a second click must not buy a
            # second image behind the user's back.
            return self._view(existing)
        cover_job_id, durable_id = new_id(), new_id()
        job = GraphCoverJob(
            id=cover_job_id,
            workspace_id=self.workspace_id,
            graph_id=graph.id,
            actor_id=self.actor_id,
            engine=engine,
            status="queued",
            prompt=confirmed,
            prompt_source=(prompt_source or "user_edited")[:16],
            provider_id=(provider_id or None),
            model_id=(model_id or None),
            job_id=durable_id,
        )
        self.db.add(job)
        self.db.add(
            DurableJob(
                id=durable_id,
                workspace_id=self.workspace_id,
                kind="graph.cover.ai",
                dedupe_key=f"graph.cover.ai:{cover_job_id}",
                payload={"cover_job_id": cover_job_id},
                max_attempts=1,
            )
        )
        self.db.commit()
        return self._view(job)

    # -- status / cancel -------------------------------------------------- #

    def status(self, graph_id: str) -> dict[str, Any]:
        self._graph(graph_id, "read")
        return self._view(self.latest_job(graph_id))

    def cancel(self, graph_id: str) -> dict[str, Any]:
        self._graph(graph_id, "write")
        job = self.active_job(graph_id)
        if job is None:
            raise AppError(409, "cover_job_not_active", "当前没有正在进行的封面生成。")
        job.status = "cancelled"
        job.error = None
        if job.job_id:
            # A queued job must not start after the fact; a running one cannot
            # recall the upstream request, so its late result is dropped instead.
            self.db.execute(
                update(DurableJob)
                .where(DurableJob.id == job.job_id, DurableJob.status.in_(("queued", "leased")))
                .values(status="cancelled", lease_expires_at=None)
            )
        self.db.commit()
        return self._view(job)


# --------------------------------------------------------------------------- #
# Background runner
# --------------------------------------------------------------------------- #

def _actor_can_write_graph(db: Session, workspace_id: str, actor_id: str, graph_id: str) -> bool:
    """Re-check at execution time: a queued job must not outlive the grant."""
    from app.core.security import Principal
    from app.services.authorization import AuthorizationService

    user, workspace = db.get(User, actor_id), db.get(Workspace, workspace_id)
    if not user or user.status != "active" or not workspace or user.tenant_id != workspace.tenant_id:
        return False
    principal = Principal(
        user_id=user.id,
        username=user.username,
        tenant_id=user.tenant_id,
        session_id="graph-cover-ai",
        is_system_admin=user.is_system_admin,
    )
    return AuthorizationService(db, principal).can_access_bindings(
        workspace, "write", graph_id=graph_id
    )


def _lease_ok(db: Session, job_id: str, token: str) -> bool:
    """Validate and renew this worker's lease in one write."""
    changed = db.execute(
        update(DurableJob)
        .where(
            DurableJob.id == job_id,
            DurableJob.status == "leased",
            DurableJob.lease_token == token,
            DurableJob.lease_expires_at > utc_now(),
        )
        .values(lease_expires_at=utc_now() + timedelta(seconds=_LEASE_SECONDS))
    )
    db.commit()
    return changed.rowcount == 1


def _readable_reason(exc: BaseException) -> str:
    """A short, safe reason: no model output, no provider diagnostics dump."""
    from app.services.learning_generation import safe_failure_detail

    if isinstance(exc, AppError):
        return exc.message[:300]
    detail = safe_failure_detail(exc)
    lowered = detail.lower()
    if "timeout" in lowered or "timed out" in lowered:
        return "上游响应超时，请稍后重试。"
    if "rate" in lowered and "limit" in lowered:
        return "上游限流，请稍后重试。"
    return f"生成失败：{detail}"[:300]


def _store_original(db: Session, job: GraphCoverJob, raw: bytes, mime: str) -> str | None:
    """Keep the un-cropped artifact so a future re-crop costs nothing.

    Storage is a convenience here: the cover is already computed, so a storage
    failure leaves ``file_id`` empty instead of failing the generation.
    """
    from app.services.image_generations import ImageGenerationService

    try:
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
        filename = f"cover-{job.id}.{extension}"
        storage = ImageGenerationService(db, job.workspace_id, job.actor_id, get_settings())
        stored = storage._store_bytes(filename, raw)
        record = FileRecord(
            workspace_id=job.workspace_id,
            original_name=filename,
            object_key=stored.object_key,
            mime_type=mime,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            storage_status="stored",
            parse_capability="optional_processor",
            parse_status="not_requested",
        )
        db.add(record)
        db.flush()
        FileReferenceService(db, job.workspace_id).add(
            record.id,
            FileReferenceCreate(
                target_type="graph",
                target_id=job.graph_id,
                relation="cover",
                metadata={"cover_job_id": job.id, "engine": job.engine},
            ),
        )
        return record.id
    except Exception:  # noqa: BLE001 - convenience artifact only
        db.rollback()
        return None


def run_cover_ai_job(job_id: str, token: str, cover_job_id: str) -> None:
    """Execute one confirmed cover generation. Terminal by construction.

    Never raises for an expected failure: the row carries the readable reason,
    which is what the editor and the model tool read back. Retrying a paid
    generation automatically is not a decision this product should make for the
    user, so the queue job always completes.
    """
    with SessionLocal() as db:
        job = db.get(GraphCoverJob, cover_job_id)
        if job is None or job.status not in COVER_ACTIVE_STATUSES:
            return
        if job.status == "running":
            # Resumed after a crash (the lease expired while this row was already
            # running): the upstream outcome is unknown, and silently paying for a
            # second generation is worse than asking the user to retry.
            job.status = "failed"
            job.error = "上次生成中断，远端结果未知。请手动重试（可能再次产生费用）。"
            db.commit()
            return
        if not _lease_ok(db, job_id, token):
            return
        graph = db.scalar(
            select(Graph).where(
                Graph.workspace_id == job.workspace_id, Graph.id == job.graph_id
            )
        )
        if graph is None or not _actor_can_write_graph(
            db, job.workspace_id, job.actor_id, job.graph_id
        ):
            job.status, job.error = "failed", "图谱已不可访问，本次生成已取消。"
            db.commit()
            return
        job.status = "running"
        db.commit()
        file_id: str | None = None
        try:
            if job.engine == "svg":
                cover, provider_id, model_id = _render_svg_cover(
                    db,
                    job.workspace_id,
                    job.actor_id,
                    job.prompt,
                    provider_id=job.provider_id,
                    model_id=job.model_id,
                )
            else:
                raw, mime, provider_id, model_id = _generate_image_cover(
                    db,
                    job.workspace_id,
                    job.actor_id,
                    job.prompt,
                    provider_id=job.provider_id,
                    model_id=job.model_id,
                )
                cover = normalize_cover_bytes(raw, max_bytes=AI_IMAGE_MAX_BYTES)
                file_id = _store_original(db, job, raw, mime)
        except Exception as exc:  # noqa: BLE001 - the row carries the reason
            db.rollback()
            job = db.get(GraphCoverJob, cover_job_id)
            if job is None or job.status == "cancelled":
                return
            job.status, job.error = "failed", _readable_reason(exc)
            db.commit()
            return
        # A cancel that landed while the provider was working wins: the result is
        # dropped rather than written behind the user's back.
        db.rollback()
        job = db.get(GraphCoverJob, cover_job_id)
        if job is None or job.status == "cancelled":
            return
        graph = db.get(Graph, job.graph_id)
        if graph is None:
            job.status, job.error = "failed", "图谱已不存在，本次生成已丢弃。"
            db.commit()
            return
        graph.cover_svg = cover
        job.status = "ready"
        job.error = None
        job.provider_id, job.model_id = provider_id, model_id
        job.file_id = file_id
        AuditRepository(db, job.workspace_id).record(
            actor_id=job.actor_id,
            action="graph.cover_updated",
            resource_type="graph",
            resource_id=graph.id,
            details={
                "mode": "ai",
                "engine": job.engine,
                "prompt": job.prompt[:200],
                "prompt_source": job.prompt_source,
                "provider_id": provider_id,
                "model_id": model_id,
                "file_id": file_id,
            },
        )
        db.commit()
