from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.api.routers import (
    artifact_gateway,
    audit_settings,
    auth,
    chat,
    components,
    dashboard,
    deployment,
    document_learning,
    fetch_authorizations,
    egress_approvals,
    evidence,
    exercises,
    files,
    goals,
    graphs,
    health,
    image_generations,
    learning_state,
    learning_packages,
    memory,
    memory_eval,
    memory_v2,
    tasks,
    episodes,
    mcp_skills,
    migrations,
    plugins,
    practice,
    providers,
    research,
    sources,
    subapps,
    sandbox_net,
    usage,
    workflow,
    voice,
    voice_relay,
    sandbox,
    session_sharing,
)


api_router = APIRouter(prefix="/api/v1")

for router in (
    health.router,
    deployment.router,
    artifact_gateway.router,
    auth.router,
    dashboard.router,
    goals.router,
    graphs.router,
    chat.router,
    image_generations.router,
    files.router,
    document_learning.router,
    fetch_authorizations.router,
    egress_approvals.router,
    research.router,
    sources.router,
    subapps.router,
    sandbox_net.router,
    evidence.router,
    exercises.router,
    practice.router,
    # Memory V2 static routes must be registered before V1 /memory/{memory_id}.
    memory_v2.router,    tasks.router,
    episodes.router,
    learning_state.router,
    learning_packages.router,
    memory.router,
    mcp_skills.router,
    providers.router,
    usage.router,
    plugins.router,
    components.router,
    migrations.router,
    audit_settings.router,
    workflow.router,
    voice.router,
    voice_relay.router,
    sandbox.router,
    session_sharing.router,
):
    api_router.include_router(router)

# 临时记忆评测接口：默认不挂载（LEARNGRAPH_MEMORY_EVAL_ENABLED=false）。
# 它不校验 bearer / X-Workspace-ID，所以只在显式开启时才出现在路由表里，
# 关闭状态下连 404 之外的任何痕迹都没有。评测结束后整个模块可删除。
if get_settings().memory_eval_enabled:
    api_router.include_router(memory_eval.router)
