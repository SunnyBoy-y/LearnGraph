from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import gzip
import zlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.router import api_router
from app.core.config import get_settings
from app.core.database import init_database
from app.core.errors import install_error_handlers
from app.frontend_static import install_frontend_static
from app.core.seed import ensure_demo_data
from app.core.scheduler import (
    hot_table_archive_scheduler,
    mastery_scheduler,
    memory_extraction_scheduler,
    memory_retention_scheduler,
    mcp_runner_cleanup_scheduler,
    sandbox_cleanup_scheduler,
    sandbox_execution_scheduler,
    stream_events_retention_scheduler,
    voice_session_reaper_scheduler,
    voice_turn_sweep_scheduler,
    wal_checkpoint_scheduler,
)
from app.services.durable_queue import (
    supervise_durable_queue,
)
from app.services.memory_outbox_runtime import memory_outbox_scheduler
from app.services.model_pricing_sync import model_pricing_sync_worker
from app.services.document_learning import mark_interrupted_document_jobs
from app.services.chat import mark_interrupted_message_streams
from app.services.chat_durable import enqueue_interrupted_chat_resumes
from app.voice.embedded_runtime import close_embedded_runtime, install_embedded_runtime


def _prune_dead_egress_policy_snapshots() -> None:
    """Reclaim derived egress-policy snapshots that can authorize nothing anymore.

    Startup is the safest moment to do this: no sandbox container is created yet,
    and the same gates as the periodic sweep apply (expired beyond a grace period,
    no live sandbox session, no remaining authorization). Best-effort by design —
    housekeeping must never block the API from starting.
    """
    import logging

    try:
        from app.core.database import SessionLocal
        from app.services.egress_policy_maintenance import prune_derived_egress_policies

        with SessionLocal() as db:
            totals = prune_derived_egress_policies(db, get_settings())
        if totals.get("pruned"):
            logging.getLogger(__name__).info(
                "Startup egress policy prune removed %s file(s)", totals["pruned"]
            )
    except Exception:
        logging.getLogger(__name__).exception("Startup egress policy prune failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    settings.memory_root.mkdir(parents=True, exist_ok=True)
    # Reap expired encrypted downloads left by a process/container restart.
    try:
        from app.services.backup_download import prune_downloads
        prune_downloads(settings,schedule=True)
    except Exception:
        import logging
        logging.getLogger(__name__).warning('Expired backup download cleanup deferred')
    init_database()
    from app.core.database import SessionLocal
    from app.services.billing import BillingService
    with SessionLocal() as billing_db:
        BillingService.reconcile_expired(billing_db)
    _profile_warnings = settings.profile_validate()
    if _profile_warnings:
        import logging
        logging.getLogger(__name__).warning(
            "Deployment profile conflicts detected:\n  %s",
            "\n  ".join(_profile_warnings),
        )
    ensure_demo_data()
    from app.services.database_migration import controller as primary_migration_controller
    primary_migration_controller(settings).recover_worker()
    mark_interrupted_document_jobs()
    mark_interrupted_message_streams()
    _prune_dead_egress_policy_snapshots()
    durable_queue_stop: asyncio.Event | None = None
    durable_queue_tasks: list[asyncio.Task[None]] = []
    if settings.durable_queue_enabled:
        # Reschedule interrupted chat streams that reached a checkpoint so the
        # worker can perform an audited resume attempt (provider continuation
        # gated by adapter capability; otherwise parked with retry path intact).
        enqueue_interrupted_chat_resumes()
        durable_queue_stop = asyncio.Event()
        durable_queue_tasks = [
            asyncio.create_task(supervise_durable_queue(durable_queue_stop, goal_lane=False), name="durable-general"),
            asyncio.create_task(supervise_durable_queue(durable_queue_stop, goal_lane=True), name="durable-goal"),
        ]
    scheduler_stop: asyncio.Event | None = None
    scheduler_task: asyncio.Task[None] | None = None
    retention_stop: asyncio.Event | None = None
    retention_task: asyncio.Task[None] | None = None
    extraction_stop: asyncio.Event | None = None
    extraction_task: asyncio.Task[None] | None = None
    sandbox_stop: asyncio.Event | None = None
    sandbox_task: asyncio.Task[None] | None = None
    mcp_runner_stop: asyncio.Event | None = None
    mcp_runner_task: asyncio.Task[None] | None = None
    outbox_stop: asyncio.Event | None = None
    outbox_task: asyncio.Task[None] | None = None
    wal_stop: asyncio.Event | None = None
    wal_task: asyncio.Task[None] | None = None
    sandbox_exec_stop: asyncio.Event | None = None
    sandbox_exec_task: asyncio.Task[None] | None = None
    stream_events_retention_stop: asyncio.Event | None = None
    stream_events_retention_task: asyncio.Task[None] | None = None
    voice_turn_sweep_stop: asyncio.Event | None = None
    voice_turn_sweep_task: asyncio.Task[None] | None = None
    voice_reaper_stop: asyncio.Event | None = None
    voice_reaper_task: asyncio.Task[None] | None = None
    if settings.mastery_embedded_scheduler_enabled:
        scheduler_stop = asyncio.Event()
        scheduler_task = asyncio.create_task(mastery_scheduler(scheduler_stop))
    if settings.memory_retention_scheduler_enabled:
        retention_stop = asyncio.Event()
        retention_task = asyncio.create_task(memory_retention_scheduler(retention_stop))
    if settings.memory_extraction_scheduler_enabled:
        extraction_stop = asyncio.Event()
        extraction_task = asyncio.create_task(memory_extraction_scheduler(extraction_stop))
    if settings.sandbox_cleanup_scheduler_enabled:
        sandbox_stop = asyncio.Event()
        sandbox_task = asyncio.create_task(sandbox_cleanup_scheduler(sandbox_stop))
    if settings.sandbox_execution_scheduler_enabled:
        sandbox_exec_stop = asyncio.Event()
        sandbox_exec_task = asyncio.create_task(sandbox_execution_scheduler(sandbox_exec_stop))
    if settings.mcp_stdio_cleanup_scheduler_enabled:
        mcp_runner_stop = asyncio.Event()
        mcp_runner_task = asyncio.create_task(mcp_runner_cleanup_scheduler(mcp_runner_stop))
    if settings.memory_outbox_worker_enabled:
        outbox_stop = asyncio.Event()
        outbox_task = asyncio.create_task(memory_outbox_scheduler(outbox_stop))
    if settings.wal_checkpoint_interval_seconds > 0:
        wal_stop = asyncio.Event()
        wal_task = asyncio.create_task(wal_checkpoint_scheduler(wal_stop))
    if settings.stream_event_retention_enabled:
        stream_events_retention_stop = asyncio.Event()
        stream_events_retention_task = asyncio.create_task(
            stream_events_retention_scheduler(stream_events_retention_stop)
        )
    if settings.hot_table_archive_interval_seconds > 0:
        hot_archive_stop = asyncio.Event()
        hot_archive_task = asyncio.create_task(
            hot_table_archive_scheduler(hot_archive_stop)
        )
    if settings.voice_turn_sweep_enabled and settings.voice_turn_stale_timeout > 0:
        # Startup is where a voice turn left hanging by a previous process gets
        # settled: the worker that owned it may never come back, and until it is
        # closed the page keeps waiting and the question stays out of the
        # transcript and out of memory.
        voice_turn_sweep_stop = asyncio.Event()
        voice_turn_sweep_task = asyncio.create_task(
            voice_turn_sweep_scheduler(voice_turn_sweep_stop)
        )
    if (
        settings.voice_session_reaper_enabled
        and settings.voice_session_idle_timeout_seconds > 0
    ):
        # Session TTL: an abandoned call (closed lid, phone in a pocket) would
        # otherwise hold a realtime ASR session and a bidirectional TTS stream open
        # for ever, because nothing in the call path ends them.
        voice_reaper_stop = asyncio.Event()
        voice_reaper_task = asyncio.create_task(
            voice_session_reaper_scheduler(voice_reaper_stop)
        )
    from app.services.provider_balance_lifecycle import balance_worker
    balance_stop = asyncio.Event()
    balance_task = asyncio.create_task(balance_worker(balance_stop))
    pricing_stop = asyncio.Event()
    pricing_task = asyncio.create_task(model_pricing_sync_worker(pricing_stop)) if settings.models_dev_refresh_enabled else None
    from app.services.scheduled_tasks import scheduled_task_worker
    schedules_stop = asyncio.Event()
    schedules_task = asyncio.create_task(scheduled_task_worker(schedules_stop, settings)) if settings.scheduled_tasks_enabled else None
    from app.services.im_feishu_manager import feishu_listener_manager
    from app.services.im_wechat_manager import wechat_listener_manager
    feishu_stop: asyncio.Event | None = None
    feishu_task: asyncio.Task[None] | None = None
    wechat_stop: asyncio.Event | None = None
    wechat_task: asyncio.Task[None] | None = None
    if settings.durable_queue_enabled:
        feishu_stop = asyncio.Event()
        feishu_task = asyncio.create_task(feishu_listener_manager.supervise(feishu_stop), name="feishu-listeners")
        wechat_stop = asyncio.Event()
        wechat_task = asyncio.create_task(wechat_listener_manager.supervise(wechat_stop), name="wechat-listeners")
    try:
        yield
    finally:
        if wechat_stop is not None and wechat_task is not None:
            wechat_stop.set()
            await wechat_task
        if feishu_stop is not None and feishu_task is not None:
            feishu_stop.set()
            await feishu_task
        schedules_stop.set()
        if schedules_task is not None:
            await schedules_task
        pricing_stop.set()
        if pricing_task is not None:
            await pricing_task
        balance_stop.set()
        await balance_task
        await close_embedded_runtime(app)
        if durable_queue_stop is not None:
            durable_queue_stop.set()
            await asyncio.gather(*durable_queue_tasks)
        if scheduler_stop is not None and scheduler_task is not None:
            scheduler_stop.set()
            await scheduler_task
        if retention_stop is not None and retention_task is not None:
            retention_stop.set()
            await retention_task
        if extraction_stop is not None and extraction_task is not None:
            extraction_stop.set()
            await extraction_task
        if sandbox_stop is not None and sandbox_task is not None:
            sandbox_stop.set()
            await sandbox_task
        if sandbox_exec_stop is not None and sandbox_exec_task is not None:
            sandbox_exec_stop.set()
            await sandbox_exec_task
        if mcp_runner_stop is not None and mcp_runner_task is not None:
            mcp_runner_stop.set()
            await mcp_runner_task
        if outbox_stop is not None and outbox_task is not None:
            outbox_stop.set()
            await outbox_task
        if wal_stop is not None and wal_task is not None:
            wal_stop.set()
            await wal_task
        if (
            stream_events_retention_stop is not None
            and stream_events_retention_task is not None
        ):
            stream_events_retention_stop.set()
            await stream_events_retention_task
        if hot_archive_stop is not None and hot_archive_task is not None:
            hot_archive_stop.set()
            await hot_archive_task
        if voice_turn_sweep_stop is not None and voice_turn_sweep_task is not None:
            voice_turn_sweep_stop.set()
            await voice_turn_sweep_task
        if voice_reaper_stop is not None and voice_reaper_task is not None:
            voice_reaper_stop.set()
            await voice_reaper_task


_GZIP_COMPRESSIBLE_EXACT = frozenset({
    "application/json",
    "application/javascript",
    "application/x-javascript",
    "application/xml",
    "application/xhtml+xml",
    "image/svg+xml",
})


def _accepts_gzip(headers: Headers) -> bool:
    raw = headers.get("accept-encoding", "")
    if not raw:
        return False
    for part in raw.split(","):
        token, *params = [item.strip().lower() for item in part.split(";")]
        if token != "gzip":
            continue
        for param in params:
            if param.startswith("q="):
                try:
                    if float(param[2:]) <= 0.0:
                        return False
                except ValueError:
                    pass
        return True
    return False


def _is_gzip_compressible(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if not media_type or media_type == "text/event-stream":
        return False
    if media_type.startswith("text/"):
        return True
    return media_type in _GZIP_COMPRESSIBLE_EXACT or media_type.endswith("+json") or media_type.endswith("+xml")


def _append_vary_accept_encoding(headers: MutableHeaders) -> None:
    existing = headers.get("vary", "")
    parts = [part.strip() for part in existing.split(",") if part.strip()]
    if not any(part.lower() == "accept-encoding" or part == "*" for part in parts):
        parts.append("Accept-Encoding")
        headers["Vary"] = ", ".join(parts)


class SafeGZipMiddleware:
    """Compress JSON/text/SVG responses over GZip without ever buffering SSE."""

    def __init__(self, app: ASGIApp, minimum_size: int = 1024, compresslevel: int = 5) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not _accepts_gzip(Headers(scope=scope)):
            await self.app(scope, receive, send)
            return

        start_message: Message | None = None
        eligible = False
        compressor: zlib._Compress | None = None

        async def send_compressed(message: Message) -> None:
            nonlocal start_message, eligible, compressor
            msg_type = message["type"]
            if msg_type == "http.response.start":
                status_code = int(message.get("status", 200))
                headers = MutableHeaders(scope=message)
                content_type = headers.get("content-type", "")
                cache_control = headers.get("cache-control", "").lower()
                content_length_raw = headers.get("content-length")
                too_small = False
                if content_length_raw is not None:
                    try:
                        too_small = int(content_length_raw) < self.minimum_size
                    except ValueError:
                        too_small = False

                if (
                    status_code < 200
                    or status_code in {204, 205, 206, 304}
                    or "content-encoding" in headers
                    or "content-range" in headers
                    or "no-transform" in cache_control
                    or not _is_gzip_compressible(content_type)
                    or too_small
                ):
                    eligible = False
                    await send(message)
                    return

                eligible = True
                start_message = message
                return

            if msg_type == "http.response.body" and eligible:
                body = message.get("body", b"")
                more_body = bool(message.get("more_body", False))

                if start_message is not None:
                    headers = MutableHeaders(scope=start_message)
                    if not more_body:
                        if len(body) < self.minimum_size:
                            _append_vary_accept_encoding(headers)
                            await send(start_message)
                            start_message = None
                            eligible = False
                            await send(message)
                            return
                        compressed = gzip.compress(body, compresslevel=self.compresslevel)
                        headers["Content-Encoding"] = "gzip"
                        headers["Content-Length"] = str(len(compressed))
                        _append_vary_accept_encoding(headers)
                        await send(start_message)
                        start_message = None
                        await send({"type": "http.response.body", "body": compressed, "more_body": False})
                        return

                    # Streaming compressible body (e.g. FileResponse for JS/CSS bundles).
                    compressor = zlib.compressobj(
                        self.compresslevel, zlib.DEFLATED, 16 + zlib.MAX_WBITS
                    )
                    headers["Content-Encoding"] = "gzip"
                    if "content-length" in headers:
                        del headers["content-length"]
                    _append_vary_accept_encoding(headers)
                    await send(start_message)
                    start_message = None
                    chunk = compressor.compress(body) if body else b""
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                    return

                assert compressor is not None
                chunk = compressor.compress(body) if body else b""
                if not more_body:
                    chunk += compressor.flush()
                await send({"type": "http.response.body", "body": chunk, "more_body": more_body})
                return

            await send(message)

        await self.app(scope, receive, send_compressed)


class SecurityHeadersMiddleware:
    """Add clickjacking headers without buffering SSE or streaming responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault(
                    "Content-Security-Policy",
                    "frame-ancestors 'none'",
                )
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


settings = get_settings()
app = FastAPI(
    title="LearnGraph API",
    version=settings.app_version,
    description=(
        "# LearnGraph 后端 API\n\n"
        "LearnGraph 是一个按工作区隔离的目标驱动学习图谱服务。API 负责目标澄清、" 
        "图谱审核、学习会话、来源与证据、掌握度以及下一步行动的持久化编排。\n\n"
        "## 请求约定\n"
        "- 除 `/health` 和开发环境的登录接口外，必须携带 `Authorization: Bearer <token>`。\n"
        "- 工作区资源必须携带 `X-Workspace-ID: <workspace_id>`；该 Header 只用于选择作用域，" 
        "服务端仍会重新校验用户是否有权访问资源。\n"
        "- JSON 请求使用 `application/json`；文件上传使用 `multipart/form-data`；流式对话使用 SSE。\n"
        "- 成功响应的具体字段由每个接口的 `response_model` 展开显示；失败响应统一为 "
        "`{\"error\": {\"code\": \"...\", \"message\": \"...\", \"details\": {}}}`。\n\n"
        "## 外部能力\n"
        "模型、搜索、网页抓取、研究、Memory 和 MCP 只通过显式配置的 Provider 调用。" 
        "Provider 未配置或不可用时，接口会返回明确错误，不会伪造结果或静默切换。"
    ),
    openapi_tags=[
        {"name": "health", "description": "健康检查：验证服务进程与 SQLite 是否可用，并报告远程能力开关。"},
        {"name": "authentication-rbac", "description": "认证、会话、用户、组织、工作区和权限管理。"},
        {"name": "dashboard", "description": "工作区首页聚合数据、指标和下一步行动。"},
        {"name": "goals", "description": "目标澄清、Goal 确认、候选图谱生成与发布。"},
        {"name": "graphs", "description": "目标图谱查询、节点编辑、合并审核、修订和学习关联。"},
        {"name": "chat", "description": "学习会话、消息、消息分支和 SSE 流式对话。"},
        {"name": "files", "description": "文件上传、解析、文本块、引用和删除影响预检。"},
        {"name": "search", "description": "搜索和可追溯网页来源。"},
        {"name": "evidence-mastery", "description": "证据审核、掌握度计算和复习调度。"},
        {"name": "mcp-skills", "description": "MCP Server 与 Skill 的能力发现、授权、调用和撤销。"},
        {"name": "providers", "description": "Provider 配置、能力探测、模型发现和启用状态。"},
        {"name": "usage", "description": "Token、美元/人民币原币费用、预算和价格记录。"},
        {"name": "sandbox", "description": "按工作区和会话隔离的固定文件任务、执行事实与清理。"},
    ],
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Direct API access from a LAN-hosted frontend is supported as well as
    # the normal same-origin Vite proxy. Keep credentials enabled while
    # limiting the regex to private/local network addresses.
    allow_origin_regex=(
        r"^https?://(?:localhost|127\.0\.0\.1|10\.(?:\d{1,3}\.){2}\d{1,3}|"
        r"192\.168\.(?:\d{1,3}\.)?\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])(?:\.\d{1,3}){2})"
        r"(?::\d+)?$"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SafeGZipMiddleware, minimum_size=1024, compresslevel=5)
app.add_middleware(SecurityHeadersMiddleware)
install_error_handlers(app)
app.include_router(api_router)
install_embedded_runtime(app)
install_frontend_static(app, settings.resolved_frontend_dist)
