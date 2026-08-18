from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.config import Settings
from app.core.security import Principal
from app.domain.models import User, Workspace
from app.providers.factory import (
    fetch_provider_for_workspace,
    image_provider_for_workspace,
    image_search_provider_for_workspace,
    memory_provider_for_workspace,
    model_provider_for_workspace,
    search_provider_for_workspace,
    vision_provider_for_workspace,
)
from app.services.agent_runtime import AgentToolRuntime
from app.services.authorization import AuthorizationService
from app.services.chat import ChatService
from app.services.context_builder import ContextBuilder
from app.services.mcp_skills import MCPAndSkillService
from app.services.memory import MemoryService
from app.services.memory_retrieval import MemoryHybridRetriever
from app.services.memory_router import MemoryRouter
from app.services.sandbox import SandboxAgentWorkspaceService
from app.services.session_retrieval import SessionRetrievalService


def build_agent_tool_runtime(
    db: Session,
    context: WorkspaceContext,
    settings: Settings,
) -> AgentToolRuntime:
    """Assemble an AgentToolRuntime service graph on the given session.

    Shared by the request-scoped ChatService and the isolated tool workers
    (which pass a fresh SessionLocal so concurrent tools never share a
    SQLAlchemy Session/transaction).
    """
    authorization = AuthorizationService(db, context.principal)
    search_provider = search_provider_for_workspace(
        db, context.workspace_id, settings
    )
    image_search_provider = image_search_provider_for_workspace(
        db, context.workspace_id, settings
    )
    memory_service = MemoryService(
        db,
        context.workspace,
        context.principal.user_id,
        memory_provider_for_workspace(
            db,
            context.workspace,
            context.principal.user_id,
            settings,
        ),
        settings.memory_root,
    )
    sandbox_authorized = "workspace.manage" in authorization.workspace_permissions(
        context.workspace
    )
    extension_service = MCPAndSkillService(
        db,
        context.workspace_id,
        context.principal.user_id,
        settings,
        workspace=context.workspace,
        principal=context.principal,
    )
    sandbox = (
        SandboxAgentWorkspaceService(
            db,
            context.workspace_id,
            context.principal.user_id,
            settings,
            workspace=context.workspace,
            principal=context.principal,
        )
        if sandbox_authorized and settings.sandbox_agent_enabled
        else None
    )
    return AgentToolRuntime(
        workspace_id=context.workspace_id,
        actor_id=context.principal.user_id,
        search_provider=search_provider,
        extensions=extension_service,
        sandbox=sandbox,
        sandbox_authorized=sandbox_authorized,
        memory_tools=memory_service,
        session_retrieval=SessionRetrievalService(
            db,
            context.workspace,
            context.principal.user_id,
            authorization,
        ),
        image_provider=image_provider_for_workspace(
            db, context.workspace_id, settings
        ),
        image_provider_resolver=lambda image_provider_id, image_model_id: (
            image_provider_for_workspace(
                db,
                context.workspace_id,
                settings,
                provider_id=image_provider_id,
                model_id=image_model_id,
            )
        ),
        settings=settings,
        can_manage_providers="workspace.manage" in context.permissions,
        fetch_provider=fetch_provider_for_workspace(db, context.workspace_id, settings),
        image_search_provider=image_search_provider,
    )


def build_agent_tool_worker_runtime(
    db: Session,
    *,
    workspace_id: str,
    actor_id: str,
    tenant_id: str,
    permissions: frozenset[str],
    settings: Settings,
) -> AgentToolRuntime:
    """Rebuild an AgentToolRuntime on an isolated worker session.

    Called from a tool worker thread with a fresh ``SessionLocal``.  Reloads
    the authenticated identity/workspace from that session so no ORM object
    from the request-scoped Session is touched across threads.  The permission
    set is snapshotted from the request (it cannot change mid-generation).
    """
    workspace = db.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
        )
    )
    if workspace is None:
        raise LookupError(f"Workspace {workspace_id} no longer exists")
    user = db.scalar(
        select(User).where(
            User.id == actor_id,
            User.tenant_id == tenant_id,
        )
    )
    if user is None or user.status != "active":
        raise LookupError(f"Identity {actor_id} is no longer active")
    principal = Principal(
        user_id=user.id,
        username=user.username,
        tenant_id=user.tenant_id,
        session_id="agent-tool-worker",
        display_name=user.display_name or user.username,
        is_system_admin=user.is_system_admin,
        must_change_password=user.must_change_password,
    )
    context = WorkspaceContext(
        principal=principal,
        workspace=workspace,
        permissions=frozenset(permissions),
    )
    return build_agent_tool_runtime(db, context, settings)


def build_chat_service(
    db: Session,
    *,
    workspace_context: WorkspaceContext,
    settings: Settings,
    model_id: str | None = None,
    provider_id: str | None = None,
    thinking_mode: str | None = None,
    search_route: str | None = None,
) -> ChatService:
    """Assemble a ChatService for a workspace/actor in one place.

    The HTTP router and the event-driven subapp Agent worker share this factory
    so both paths use the same Provider, Memory, Search and AgentToolRuntime
    composition.
    """
    context = workspace_context
    authorization = AuthorizationService(db, context.principal)
    search_provider = search_provider_for_workspace(
        db, context.workspace_id, settings, route=search_route
    )
    memory_service = MemoryService(
        db,
        context.workspace,
        context.principal.user_id,
        memory_provider_for_workspace(
            db,
            context.workspace,
            context.principal.user_id,
            settings,
        ),
        settings.memory_root,
    )
    model_kwargs: dict[str, str] = {}
    if model_id is not None:
        model_kwargs["model_id"] = model_id
    if provider_id is not None:
        model_kwargs["provider_id"] = provider_id
    if thinking_mode is not None:
        model_kwargs["thinking_mode"] = thinking_mode
    if search_route not in {None, "disabled"}:
        model_kwargs["search_route"] = search_route
    agent_tool_runtime = build_agent_tool_runtime(db, context, settings)
    return ChatService(
        db,
        context.workspace_id,
        context.principal.user_id,
        model_provider_for_workspace(db, context.workspace_id, settings, **model_kwargs),
        tenant_id=context.principal.tenant_id,
        context_builder=(
            ContextBuilder(db, MemoryRouter(MemoryHybridRetriever(db)))
            if settings.memory_context_builder_v2
            else None
        ),
        search_provider=search_provider,
        memory_context_loader=(
            None
            if settings.memory_context_builder_v2
            else memory_service.context_for_session
        ),
        memory_cache_context_loader=(
            None
            if settings.memory_read_mode == "events"
            else memory_service.context_for_session
        ),
        suggested_prompt_context_access_checker=lambda session, session_permission: (
            authorization.can_access_resource(
                context.workspace,
                "session",
                session.id,
                session_permission,
            )
            and authorization.can_access_bindings(
                context.workspace,
                "read",
                project_id=session.project_id,
                goal_id=session.goal_id,
                graph_id=session.graph_id,
            )
        ),
        learning_context_access_checker=lambda resource_type, resource_id: (
            authorization.can_access_bindings(
                context.workspace,
                "read",
                node_id=resource_id,
            )
            if resource_type == "node"
            else authorization.can_access_resource(
                context.workspace,
                resource_type,
                resource_id,
                "read",
            )
        ),
        session_binding_access_checker=lambda project_id, goal_id, graph_id: (
            authorization.can_access_bindings(
                context.workspace,
                "read",
                project_id=project_id,
                goal_id=goal_id,
                graph_id=graph_id,
            )
        ),
        agent_tool_runtime=agent_tool_runtime,
        vision_provider=vision_provider_for_workspace(
            db, context.workspace_id, settings
        ),
        tool_worker_factory=lambda worker_db: build_agent_tool_worker_runtime(
            worker_db,
            workspace_id=context.workspace_id,
            actor_id=context.principal.user_id,
            tenant_id=context.principal.tenant_id,
            permissions=context.permissions,
            settings=settings,
        ),
    )
