from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.database import init_database
from app.core.errors import install_error_handlers
from app.core.seed import ensure_demo_data
from app.core.scheduler import (
    mastery_scheduler,
    memory_extraction_scheduler,
    memory_retention_scheduler,
    mcp_runner_cleanup_scheduler,
    sandbox_cleanup_scheduler,
)
from app.services.durable_queue import (
    durable_queue_worker,
    reconcile_research_polling,
)
from app.services.chat import mark_interrupted_message_streams
from app.services.chat_durable import enqueue_interrupted_chat_resumes


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    settings.memory_root.mkdir(parents=True, exist_ok=True)
    init_database()
    _profile_warnings = settings.profile_validate()
    if _profile_warnings:
        import logging
        logging.getLogger(__name__).warning(
            "Deployment profile conflicts detected:\n  %s",
            "\n  ".join(_profile_warnings),
        )
    ensure_demo_data()
    mark_interrupted_message_streams()
    durable_queue_stop: asyncio.Event | None = None
    durable_queue_task: asyncio.Task[None] | None = None
    if settings.durable_queue_enabled:
        # Re-arm durable research poll jobs for tasks left in-flight by a prior
        # process before the worker starts claiming work.
        reconcile_research_polling()
        # Reschedule interrupted chat streams that reached a checkpoint so the
        # worker can perform an audited resume attempt (provider continuation
        # gated by adapter capability; otherwise parked with retry path intact).
        enqueue_interrupted_chat_resumes()
        durable_queue_stop = asyncio.Event()
        durable_queue_task = asyncio.create_task(
            durable_queue_worker(durable_queue_stop, f"api-{uuid4()}")
        )
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
    if settings.mcp_stdio_cleanup_scheduler_enabled:
        mcp_runner_stop = asyncio.Event()
        mcp_runner_task = asyncio.create_task(mcp_runner_cleanup_scheduler(mcp_runner_stop))
    try:
        yield
    finally:
        if durable_queue_stop is not None and durable_queue_task is not None:
            durable_queue_stop.set()
            await durable_queue_task
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
        if mcp_runner_stop is not None and mcp_runner_task is not None:
            mcp_runner_stop.set()
            await mcp_runner_task


settings = get_settings()
app = FastAPI(
    title="LearnGraph API",
    version="0.1.0",
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
        {"name": "search-research", "description": "搜索、研究任务和可追溯网页来源。"},
        {"name": "evidence-mastery", "description": "证据审核、掌握度计算和复习调度。"},
        {"name": "mcp-skills", "description": "MCP Server 与 Skill 的能力发现、授权、调用和撤销。"},
        {"name": "providers", "description": "Provider 配置、能力探测、模型发现和启用状态。"},
        {"name": "usage", "description": "Token、费用、预算、价格和汇率记录。"},
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
install_error_handlers(app)
app.include_router(api_router)
