"""临时记忆评测接口（temporary memory-evaluation harness）。

**这不是产品功能，评测结束就删。**

只在 ``LEARNGRAPH_MEMORY_EVAL_ENABLED=true`` 时挂载（见 ``app/api/router.py``），
挂载后**不校验 Bearer / X-Workspace-ID**：评测脚本要能一行 curl 打通「写入 → 提问」，
而不是先过一遍登录态。因此该开关默认关闭，且只应在本地/受信网络里短时打开。

四个端点：

* ``POST /memory-eval/bootstrap`` —— 一次性建好评测环境：全新账号 + 全新工作区
  （= 全新记忆库）、DeepSeek provider、``deepseek-flash`` 模型、把「记忆整理模型」
  指到它、打开工作区记忆开关、把对话默认模型也指到它。返回可直接使用的
  bearer token 与 workspace_id，方便你随时改调正式接口做对照。
* ``POST /memory-eval/write`` —— 记忆写入接口。输入一段文本，走**记忆建立通道**：
  ``MemoryEvidence(user_statement) → 记忆整理模型原子化 → MemoryDraft → 自动提交
  → MemoryRecord + 事件流 + 检索投影``（``MemoryProfileService.apply_intent``，
  即正式接口 ``POST /api/v1/memory/profile/intents`` 的同一条链路）。
  全程不计算 embedding：``memory.enhancement.embedding.enabled`` 被显式写成 false，
  且写入链路本身从不调用 embedding provider。
* ``POST /memory-eval/ask`` —— 正常文本问答路径。默认**每次调用新建一个会话**，
  这样回答只能来自长期记忆而不是同一段对话的历史；请求体固定
  ``agent_mode=True`` + ``thinking_mode="low"`` + ``model_id="deepseek-flash"``，
  生成走的就是 ``POST /api/v1/sessions/{id}/messages/stream`` 的同一个
  ``_detached_message_stream``（独立 worker 线程、事件照常落库）。
  响应里附带本轮提问在同一检索路径上召回的候选记忆，便于核对命中。
* ``GET /memory-eval/status`` / ``GET /memory-eval/memories`` —— 只读核对：
  当前生效的 provider / 记忆整理模型 / 策略 / 已落库的记忆条目与正文。
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.deps import AppSettings, DB, WorkspaceContext
from app.core.errors import AppError
from app.core.security import Principal
from app.domain.memory_event_models import MemoryScopeContext, MemorySearchDocument
from app.domain.models import Message, User, Workspace, WorkspaceSetting
from app.domain.schemas.auth import RegisterRequest
from app.domain.schemas.chat import MessageCreateRequest, SessionCreateRequest
from app.domain.schemas.management import (
    ProviderCreateRequest,
    ProviderUpdateRequest,
    SettingUpdateRequest,
)
from app.services.auth import AuthService
from app.services.management import ProviderService, SettingsService
from app.services.memory_enhancement import load_enhancement_config, save_enhancement_config
from app.services.memory_profile import MemoryProfileService
from app.services.memory_retrieval import MemoryHybridRetriever
from app.services.memory_router import MemoryRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory-eval", tags=["memory-eval"])

# 评测上下文（workspace_id / user_id / provider / model）存在这里，
# 这样 /write 与 /ask 不必每次都带工作区参数。
EVAL_CONTEXT_KEY = "memory.eval_context"
FUNCTIONAL_MODEL_DEFAULTS_KEY = "models.functional_defaults"
MEMORY_POLICY_KEY = "memory.shared_policy"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL_ID = "deepseek-flash"
PROVIDER_DISPLAY_NAME = "DeepSeek（记忆评测）"

# 检索口径与 ContextBuilder 内部完全一致（MemoryRouter.route 里硬编码的
# top_k=6 / min_score=0.20），所以这里复算出来的候选就是真正会被注入提示词的那一批。


# ---------------------------------------------------------------------------
# 评测上下文
# ---------------------------------------------------------------------------


def _eval_context(db: DB, workspace_id: str | None) -> dict[str, Any]:
    statement = select(WorkspaceSetting).where(WorkspaceSetting.key == EVAL_CONTEXT_KEY)
    if workspace_id:
        statement = statement.where(WorkspaceSetting.workspace_id == workspace_id)
    setting = db.scalars(statement.order_by(WorkspaceSetting.created_at.desc())).first()
    if setting is None or not isinstance(setting.value, dict):
        raise AppError(
            404,
            "memory_eval_not_bootstrapped",
            "Call POST /api/v1/memory-eval/bootstrap first",
        )
    return dict(setting.value)


def _require_workspace(db: DB, context: dict[str, Any]) -> Workspace:
    workspace = db.get(Workspace, str(context.get("workspace_id") or ""))
    if workspace is None:
        raise AppError(404, "memory_eval_workspace_missing", "The eval workspace no longer exists")
    return workspace


def _require_user(db: DB, context: dict[str, Any]) -> User:
    user = db.get(User, str(context.get("user_id") or ""))
    if user is None:
        raise AppError(404, "memory_eval_user_missing", "The eval user no longer exists")
    return user


def _principal(user: User) -> Principal:
    return Principal(
        user_id=user.id,
        username=user.username,
        tenant_id=user.tenant_id,
        session_id="",
        display_name=user.display_name or user.username,
    )


def _workspace_context(user: User, workspace: Workspace) -> WorkspaceContext:
    return WorkspaceContext(
        principal=_principal(user),
        workspace=workspace,
        permissions=frozenset({"workspace.read", "workspace.write", "workspace.manage"}),
    )


def _generated_password() -> str:
    """满足 ``validate_new_password``：≥8 位、同时含字母与数字。"""

    return f"MemoEval{secrets.randbelow(9000) + 1000}{secrets.token_urlsafe(8)}"


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------


class BootstrapRequest(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=120)
    password: str | None = Field(default=None, min_length=8, max_length=1024)
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    base_url: str = Field(default=DEFAULT_BASE_URL, min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    model_id: str = Field(default=DEFAULT_MODEL_ID, min_length=1, max_length=160)
    extra_model_ids: list[str] = Field(default_factory=list, max_length=20)


@router.post("/bootstrap")
def bootstrap(
    payload: BootstrapRequest,
    db: DB,
    settings: AppSettings,
) -> dict[str, Any]:
    """建一个全新的账号 + 工作区，并把记忆整理模型指到 DeepSeek。"""

    api_key = (payload.api_key or settings.memory_eval_deepseek_api_key or "").strip()
    if not api_key:
        raise AppError(
            400,
            "memory_eval_api_key_missing",
            "Pass api_key in the body or set LEARNGRAPH_MEMORY_EVAL_DEEPSEEK_API_KEY",
        )

    username = (payload.username or f"memeval-{uuid4().hex[:8]}").strip()
    password = payload.password or _generated_password()
    display_name = (payload.display_name or "记忆评测").strip()

    # 1) 全新账号 → 自动带一个全新 personal 工作区（记忆按 workspace 隔离，
    #    所以这等价于"全新记忆库"）。
    login = AuthService(db, settings).register(
        RegisterRequest(username=username, display_name=display_name, password=password),
        user_agent="learngraph-memory-eval",
    )
    workspace_id = login.default_workspace_id
    if not workspace_id:
        raise AppError(500, "memory_eval_workspace_missing", "Registration created no workspace")
    user_id = login.user_id

    # 2) DeepSeek provider（OpenAI 兼容类型 + 官方 base_url，
    #    运行期由 dialects 判定走 DeepSeek 原生适配器）。
    provider_service = ProviderService(db, workspace_id, user_id, settings)
    provider = provider_service.create(
        ProviderCreateRequest(
            display_name=PROVIDER_DISPLAY_NAME,
            provider_type="openai_compatible_chat",
            base_url=payload.base_url,
            api_key=SecretStr(api_key),
            capabilities={},
        )
    )
    provider_id = provider.id

    # 3) 登记模型并显式设成默认模型 + 启用（create() 的自动探测可能已经做了，
    #    这里做的是幂等兜底：探测失败时也能强制启用）。
    model_ids = [payload.model_id, *[m for m in payload.extra_model_ids if m.strip()]]
    try:
        provider_service.sync_model_catalog_defaults(provider_id, model_ids)
    except AppError as exc:
        logger.warning("memory-eval: model catalog sync failed: %s", exc.message)
    provider = provider_service.update(
        provider_id,
        ProviderUpdateRequest(default_model=payload.model_id, enabled=True),
    )

    # 4) 「功能模型 → 记忆整理模型」= 这个 provider + deepseek-flash。
    #    两个 section 一起写：运行时优先读 summarization，只写一个会让
    #    "抽取用的模型"和"摘要用的模型"分叉。
    #    embedding 显式关掉且留空 provider/model —— 本次评测不参与。
    config, _cache_invalidated = save_enhancement_config(
        db,
        workspace_id,
        {
            "extraction": {
                "enabled": True,
                "provider_id": provider_id,
                "model_id": payload.model_id,
                "follow_conversation": False,
                "auto_commit": True,
            },
            "summarization": {
                "enabled": True,
                "provider_id": provider_id,
                "model_id": payload.model_id,
                "follow_conversation": False,
            },
            "embedding": {"enabled": False, "provider_id": "", "model_id": ""},
        },
    )
    db.commit()

    # 5) 打开工作区记忆开关，并把对话默认模型也指到同一个模型，
    #    这样即使不显式传 model_id，问答路径也落在 deepseek-flash 上。
    settings_service = SettingsService(db, workspace_id, user_id)
    settings_service.update(
        MEMORY_POLICY_KEY,
        SettingUpdateRequest(value={"workspace_enabled": True}),
    )
    settings_service.update(
        FUNCTIONAL_MODEL_DEFAULTS_KEY,
        SettingUpdateRequest(value={"chat": {"provider_id": provider_id, "model_id": payload.model_id}}),
    )

    context = {
        "workspace_id": workspace_id,
        "user_id": user_id,
        "tenant_id": db.get(User, user_id).tenant_id,
        "username": username,
        "password": password,
        "access_token": login.access_token,
        "provider_id": provider_id,
        "model_id": payload.model_id,
        "base_url": payload.base_url,
    }
    db.add(WorkspaceSetting(workspace_id=workspace_id, key=EVAL_CONTEXT_KEY, value=context))
    db.commit()

    return {
        "status": "ok",
        "workspace_id": workspace_id,
        "user_id": user_id,
        "username": username,
        "password": password,
        "access_token": login.access_token,
        "provider_id": provider_id,
        "provider_display_name": provider.display_name,
        "provider_status": provider.status,
        "provider_enabled": bool(provider.enabled),
        "model_id": payload.model_id,
        "registered_model_ids": model_ids,
        "memory_enhancement": config,
        "memory_policy": {"workspace_enabled": True},
        "chat_default_model": {"provider_id": provider_id, "model_id": payload.model_id},
    }


# ---------------------------------------------------------------------------
# 记忆写入（记忆建立通道）
# ---------------------------------------------------------------------------


class WriteRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4_000)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=64)
    timezone_name: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)


@router.post("/write")
def write_memory(payload: WriteRequest, db: DB, settings: AppSettings) -> dict[str, Any]:
    """一段文本 → 记忆建立通道（记忆整理模型原子化 → 草稿 → 自动提交 → 落库）。"""

    context = _eval_context(db, payload.workspace_id)
    workspace = _require_workspace(db, context)
    user_id = str(context["user_id"])

    from app.domain.schemas.management import MemoryProfileIntentRequest

    service = MemoryProfileService(db, workspace, user_id, settings)
    result = service.apply_intent(
        MemoryProfileIntentRequest(
            text=payload.content,
            timezone_name=payload.timezone_name,
        )
    )
    db.commit()
    return {
        "status": result.status,
        "drafts_created": result.drafts_created,
        "auto_committed": result.auto_committed,
        "affected_memory_ids": list(result.affected_memory_ids),
        "profile_status": result.profile_status,
        "model": {"provider_id": context["provider_id"], "model_id": context["model_id"]},
    }


# ---------------------------------------------------------------------------
# 正常文本问答路径
# ---------------------------------------------------------------------------


class AskRequest(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=64)
    provider_id: str | None = Field(default=None, min_length=1, max_length=36)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)
    thinking_mode: str = Field(default="low", min_length=1, max_length=16)
    agent_mode: bool = True
    search_route: str = Field(default="disabled", min_length=1, max_length=32)
    include_retrieved_memories: bool = True


def _drain_stream(events: Any) -> dict[str, Any]:
    """消费 detached SSE 流，抽最终回答文本 + 失败原因。

    事件格式与 ``POST /sessions/{id}/messages/stream`` 完全一致；
    正文只在 ``part.delta`` 的 ``payload.part.content_delta``（type=text）里。
    """

    text_chunks: list[str] = []
    reasoning_chunks: list[str] = []
    message_id = ""
    failure: dict[str, Any] | None = None
    event_name: str | None = None

    for chunk in events:
        for line in str(chunk).splitlines():
            if line.startswith("event: "):
                event_name = line[7:].strip()
                continue
            if not line.startswith("data: "):
                continue
            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if not message_id:
                message_id = str(data.get("message_id") or "")
            payload = data.get("payload") or {}
            part = payload.get("part") or {}
            part_type = str(part.get("type") or "")
            if event_name == "part.delta":
                delta = part.get("content_delta") or ""
                if part_type == "text":
                    text_chunks.append(delta)
                elif part_type == "reasoning_summary":
                    reasoning_chunks.append(delta)
            elif event_name == "message.failed":
                failure = payload.get("error") or {"code": "stream_failed"}

    return {
        "message_id": message_id,
        "text": "".join(text_chunks),
        "reasoning": "".join(reasoning_chunks),
        "failure": failure,
    }


@router.post("/ask")
def ask(payload: AskRequest, db: DB, settings: AppSettings) -> dict[str, Any]:
    """真实文本问答路径：智能体模式 + 思维力度低 + deepseek-flash + 长期记忆注入。"""

    from app.api.routers.chat import _detached_message_stream
    from app.services.chat_service_factory import build_chat_service

    context = _eval_context(db, payload.workspace_id)
    workspace = _require_workspace(db, context)
    user = _require_user(db, context)
    workspace_context = _workspace_context(user, workspace)

    provider_id = payload.provider_id or str(context["provider_id"])
    model_id = payload.model_id or str(context["model_id"])

    message_payload = MessageCreateRequest(
        content=payload.content,
        provider_id=provider_id,
        model_id=model_id,
        thinking_mode=payload.thinking_mode,  # type: ignore[arg-type]
        agent_mode=payload.agent_mode,
        search_route=payload.search_route,  # type: ignore[arg-type]
    )

    # 默认每次新建会话：这样回答只能来自长期记忆，而不是同一段对话的历史。
    session_id = payload.session_id
    if session_id is None:
        session = build_chat_service(
            db,
            workspace_context=workspace_context,
            settings=settings,
            agent_mode=False,
        ).create_session(
            SessionCreateRequest(
                title=f"记忆评测 · {payload.content[:20]}",
                memory_enabled=True,
            )
        )
        session_id = session.id

    # preflight 用 agent_mode=False（与正式端点一致：避免在建流前拉起 MCP/Sandbox），
    # 真正的生成在 detached worker 里按 payload.agent_mode 重新装配。
    preflight = build_chat_service(
        db,
        workspace_context=workspace_context,
        settings=settings,
        model_id=model_id,
        provider_id=provider_id,
        thinking_mode=payload.thinking_mode,
        search_route=payload.search_route,
        agent_mode=False,
    )
    preflight.preflight_create_stream(
        session_id,
        message_payload,
        idempotency_key=None,
        last_event_id=None,
    )

    events = _detached_message_stream(
        context=workspace_context,
        settings=settings,
        session_id=session_id,
        payload=message_payload,
        idempotency_key=None,
        last_event_id=None,
        session_factory=sessionmaker(
            bind=db.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        ),
        lease=None,
    )

    # 生成本身跑在独立线程 + 独立 session 里，这里先结束本请求的事务，
    # 避免 SQLite 读事务在整个生成期间挂住。
    db.commit()

    drained = _drain_stream(events)

    # 落库结果才是权威答案（worker 已经把 message.content 写完）。
    db.rollback()
    answer = drained["text"]
    provider_trace: dict[str, Any] = {}
    if drained["message_id"]:
        message = db.get(Message, drained["message_id"])
        if message is not None:
            answer = message.content or answer
            if isinstance(message.provider_trace, dict):
                provider_trace = message.provider_trace

    result: dict[str, Any] = {
        "session_id": session_id,
        "message_id": drained["message_id"],
        "answer": answer,
        "reasoning": drained["reasoning"],
        "failure": drained["failure"],
        "model": {
            "provider_id": provider_id,
            "model_id": model_id,
            "thinking_mode": payload.thinking_mode,
            "agent_mode": payload.agent_mode,
            "search_route": payload.search_route,
        },
        "provider_trace": {
            key: provider_trace.get(key)
            for key in ("agent_mode", "thinking_mode", "usage", "model_id", "provider_id")
            if key in provider_trace
        },
    }

    if payload.include_retrieved_memories:
        # 同一条检索路径复算：MemoryRouter.route 的 top_k/min_score 是硬编码的，
        # ContextBuilder 走的也是这一次调用，所以这里列出的就是会进提示词的候选。
        scope = MemoryScopeContext(
            tenant_id=user.tenant_id,
            principal_user_id=user.id,
            workspace_id=workspace.id,
            conversation_id=session_id,
            agent_id="main_agent",
        )
        routed = MemoryRouter(MemoryHybridRetriever(db), db=db).route(scope, payload.content)
        db.commit()
        result["retrieved_memories"] = [
            {
                "target_id": item.target_id,
                "target_type": item.target_type,
                "title": item.title,
                "content": item.content,
                "score": item.score,
                # 分量：semantic 恒为 0（embedding 关）、lexical 是词面命中、
                # 其余是 scope/entity/importance/recency/confidence 的先验。
                # 中文在没有 embedding 时 lexical 基本打不出来，排序主要靠先验 ——
                # 这正是评测要看的信号。
                "component_scores": dict(item.component_scores),
            }
            for item in routed.retrieval.candidates
        ]
        result["retrieval_excluded"] = dict(routed.retrieval.excluded)
        result["retrieval_routes"] = [
            getattr(route, "value", str(route)) for route in routed.routes
        ]
        result["retrieval_degraded_modes"] = list(routed.retrieval.degraded_modes)

    return result


# ---------------------------------------------------------------------------
# 只读核对
# ---------------------------------------------------------------------------


@router.get("/status")
def eval_status(
    db: DB,
    settings: AppSettings,
    workspace_id: str | None = Query(default=None, max_length=64),
) -> dict[str, Any]:
    context = _eval_context(db, workspace_id)
    workspace = _require_workspace(db, context)
    config = load_enhancement_config(db, workspace.id)

    provider_service = ProviderService(db, workspace.id, str(context["user_id"]), settings)
    providers = [
        {
            "id": item.id,
            "display_name": item.display_name,
            "provider_type": item.provider_type,
            "base_url": item.base_url,
            "enabled": bool(item.enabled),
            "status": item.status,
            "default_model": (item.capabilities or {}).get("default_model"),
            "protocol_family": (item.capabilities or {}).get("protocol_family"),
        }
        for item in provider_service.list()
    ]

    policy = db.scalar(
        select(WorkspaceSetting).where(
            WorkspaceSetting.workspace_id == workspace.id,
            WorkspaceSetting.key == MEMORY_POLICY_KEY,
        )
    )
    functional = db.scalar(
        select(WorkspaceSetting).where(
            WorkspaceSetting.workspace_id == workspace.id,
            WorkspaceSetting.key == FUNCTIONAL_MODEL_DEFAULTS_KEY,
        )
    )
    memory_count = len(
        db.scalars(
            select(MemorySearchDocument.id).where(
                MemorySearchDocument.workspace_id == workspace.id,
                MemorySearchDocument.target_type == "memory",
                MemorySearchDocument.status == "active",
            )
        ).all()
    )
    return {
        "workspace_id": workspace.id,
        "user_id": context["user_id"],
        "providers": providers,
        "memory_enhancement": config,
        "memory_policy": (policy.value if policy is not None else None),
        "functional_model_defaults": (functional.value if functional is not None else None),
        "memory_document_count": memory_count,
        "eval_workspace_count": len(
            db.scalars(
                select(WorkspaceSetting.id).where(WorkspaceSetting.key == EVAL_CONTEXT_KEY)
            ).all()
        ),
    }


@router.get("/memories")
def list_eval_memories(
    db: DB,
    settings: AppSettings,
    workspace_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=500),
    include_content: bool = Query(default=True),
) -> dict[str, Any]:
    context = _eval_context(db, workspace_id)
    workspace = _require_workspace(db, context)
    rows = db.scalars(
        select(MemorySearchDocument)
        .where(
            MemorySearchDocument.workspace_id == workspace.id,
            MemorySearchDocument.target_type == "memory",
            MemorySearchDocument.status == "active",
        )
        .order_by(MemorySearchDocument.created_at.desc())
        .limit(limit)
    ).all()
    items = []
    for row in rows:
        item: dict[str, Any] = {
            "memory_id": row.target_id,
            "subject": row.subject,
            "slot_key": row.slot_key,
            "memory_layer": row.memory_layer,
            "memory_type": row.memory_type,
            "zone": row.zone,
            "confidence": row.confidence,
            "importance": row.importance,
            "created_at": row.created_at.isoformat(),
        }
        if include_content:
            item["content"] = row.content
        items.append(item)
    return {"workspace_id": workspace.id, "count": len(items), "items": items}
