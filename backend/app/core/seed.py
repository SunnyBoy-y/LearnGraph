from __future__ import annotations

import logging
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password, normalize_identity
from app.domain.models import (
    ChatSession,
    Evidence,
    Exercise,
    Goal,
    Graph,
    GraphEdge,
    GraphNode,
    PluginRecord,
    ProviderConfig,
    Project,
    UsageEvent,
    Tenant,
    User,
    Workspace,
    WorkspaceSetting,
)
from app.services.authorization import ensure_permission_catalog
from app.services.auth import validate_new_password


DEMO_WORKSPACE_ID = "demo-workspace"
LOCAL_TENANT_ID = "local-tenant"
logger = logging.getLogger(__name__)


def ensure_auth_identities(db: Session) -> None:
    settings = get_settings()
    tenant = db.get(Tenant, LOCAL_TENANT_ID)
    if tenant is None:
        tenant = Tenant(id=LOCAL_TENANT_ID, name="LearnGraph Local", status="active")
        db.add(tenant)
        db.flush()
    ensure_permission_catalog(db)

    admin_name = settings.bootstrap_admin_username.strip()
    admin = db.scalar(
        select(User).where(
            User.tenant_id == LOCAL_TENANT_ID,
            User.username_normalized == normalize_identity(admin_name),
        )
    )
    if admin is None:
        bootstrap_password = settings.bootstrap_admin_password or (
            "Lg!" + secrets.token_urlsafe(24) + "9"
        )
        if settings.bootstrap_admin_password:
            validate_new_password(bootstrap_password, username=admin_name)
        admin = User(
            id="bootstrap-admin",
            tenant_id=LOCAL_TENANT_ID,
            username=admin_name,
            username_normalized=normalize_identity(admin_name),
            display_name="LearnGraph Administrator",
            password_hash=hash_password(bootstrap_password),
            is_system_admin=True,
            must_change_password=True,
        )
        db.add(admin)
        db.flush()
        db.add(
            Workspace(
                id="admin-workspace",
                tenant_id=LOCAL_TENANT_ID,
                owner_user_id=admin.id,
                workspace_kind="personal",
                name="管理员私有空间",
                description="系统管理身份的私有工作区；系统管理员权限不绕过其他用户 ACL。",
            )
        )
        if settings.bootstrap_admin_password:
            logger.warning(
                "Created bootstrap admin '%s' from LEARNGRAPH_BOOTSTRAP_ADMIN_PASSWORD; password change is required",
                admin_name,
            )
        else:
            logger.warning(
                "ONE-TIME LearnGraph bootstrap credentials: username=%s password=%s (change required)",
                admin_name,
                bootstrap_password,
            )

    demo = db.get(User, "demo-user")
    if settings.demo_seed_enabled and demo is None:
        demo = User(
            id="demo-user",
            tenant_id=LOCAL_TENANT_ID,
            username=settings.demo_username,
            username_normalized=normalize_identity(settings.demo_username),
            display_name="Demo User",
            password_hash=hash_password(settings.demo_password),
            is_system_admin=True,
            must_change_password=False,
        )
        db.add(demo)
    elif demo is not None and not settings.demo_seed_enabled:
        demo.status = "disabled"
    db.commit()


def seed_demo_data(db: Session) -> None:
    if db.get(Workspace, DEMO_WORKSPACE_ID) is not None:
        return

    workspace = Workspace(
        id=DEMO_WORKSPACE_ID,
        tenant_id="local-tenant",
        owner_user_id="demo-user",
        name="个人学习区",
        description="LearnGraph 本地演示工作区",
    )
    goal = Goal(
        id="demo-goal",
        workspace_id=DEMO_WORKSPACE_ID,
        title="数据库 3 天速通",
        raw_prompt="我需要 3 天时间学习数据库基础。",
        status="approved",
        intent="学习数据库原理并完成验收练习",
        time_limit="3 天，每天 4 小时",
        desired_outcome="能够解释索引、事务和范式，并完成综合练习",
        constraints={"exclude": ["内核源码级实现"]},
        assumptions=[{"field": "assessment", "value": "练习题", "user_confirmed": True}],
    )
    graph = Graph(
        id="demo-graph",
        workspace_id=DEMO_WORKSPACE_ID,
        goal_id=goal.id,
        title="数据库原理与应用",
        status="published",
        revision=1,
    )
    nodes = [
        GraphNode(id="node-database", workspace_id=DEMO_WORKSPACE_ID, graph_id=graph.id, label="数据库", mastery_stars=3, retrieval_state="fresh", evidence_state="robust"),
        GraphNode(id="node-sql", workspace_id=DEMO_WORKSPACE_ID, graph_id=graph.id, label="SQL", mastery_stars=3, retrieval_state="fresh", evidence_state="multi"),
        GraphNode(id="node-index", workspace_id=DEMO_WORKSPACE_ID, graph_id=graph.id, label="索引", mastery_stars=2, retrieval_state="due", evidence_state="single"),
        GraphNode(id="node-transaction", workspace_id=DEMO_WORKSPACE_ID, graph_id=graph.id, label="事务", mastery_stars=2, retrieval_state="relearning", evidence_state="conflicted", attention_state="focused"),
    ]
    edges = [
        GraphEdge(workspace_id=DEMO_WORKSPACE_ID, graph_id=graph.id, source_node_id="node-database", target_node_id="node-sql", relation="contains"),
        GraphEdge(workspace_id=DEMO_WORKSPACE_ID, graph_id=graph.id, source_node_id="node-sql", target_node_id="node-index", relation="prerequisite"),
        GraphEdge(workspace_id=DEMO_WORKSPACE_ID, graph_id=graph.id, source_node_id="node-database", target_node_id="node-transaction", relation="contains"),
    ]
    session = ChatSession(
        id="demo-session",
        workspace_id=DEMO_WORKSPACE_ID,
        title="数据库速通路线",
        goal_id=goal.id,
        graph_id=graph.id,
        memory_enabled=False,
        model_snapshot={"provider_id": "local_mock", "remote_capability": False},
    )
    evidence = Evidence(
        id="demo-evidence",
        workspace_id=DEMO_WORKSPACE_ID,
        node_id="node-sql",
        source_type="exercise",
        summary="SQL SELECT 基础练习 5/5 正确",
        confidence=0.93,
        status="accepted",
        metadata_json={"demo": True},
    )
    exercise = Exercise(
        id="demo-exercise",
        workspace_id=DEMO_WORKSPACE_ID,
        node_id="node-index",
        question_type="single_choice",
        prompt="为什么数据库索引常使用 B+ 树？",
        options=["适合范围查询且磁盘 IO 较少", "无需维护平衡", "只能做等值查询", "永不分裂"],
        answer_key="适合范围查询且磁盘 IO 较少",
        explanation="B+ 树的高扇出和有序叶子链适合磁盘访问与范围查询。",
    )
    local_demo_enabled = get_settings().enable_local_demo_provider
    provider = ProviderConfig(
        id="local-mock-provider",
        workspace_id=DEMO_WORKSPACE_ID,
        display_name="Local Mock Provider",
        provider_type="local_mock",
        enabled=local_demo_enabled,
        remote_capability=False,
        capabilities={"streaming_demo": True, "structured_output": False, "web_search": False},
        status="healthy_local" if local_demo_enabled else "disabled",
    )
    plugin = PluginRecord(
        workspace_id=DEMO_WORKSPACE_ID,
        plugin_key="local-file-parser",
        name="本地文本解析器",
        version="0.1.0",
        plugin_type="document_processor",
        status="enabled",
        enabled=True,
        permissions=["read_task_input", "write_task_output"],
        capabilities=["text", "markdown"],
    )
    setting = WorkspaceSetting(
        workspace_id=DEMO_WORKSPACE_ID,
        key="ui.preferences",
        value={"theme": "light", "high_density": True, "reduced_motion": False},
    )
    usage = UsageEvent(
        workspace_id=DEMO_WORKSPACE_ID,
        provider_id="local_mock",
        model_id="deterministic-demo",
        feature="chat_demo",
        input_tokens=0,
        output_tokens=0,
        attempt=1,
        cost_usd=0,
        cost_cny=0,
        cost_status="non_billable",
        latency_ms=0,
    )
    # Explicit flush boundaries keep seed insertion valid when SQLite foreign keys are enabled.
    db.add(workspace)
    db.flush()
    db.add(goal)
    db.flush()
    db.add(graph)
    db.flush()
    db.add_all(nodes)
    db.flush()
    db.add_all(edges)
    db.flush()
    db.add_all([session, evidence, exercise, provider, plugin, setting, usage])
    db.commit()


def ensure_demo_data() -> None:
    from app.core.database import SessionLocal
    from app.services.components import ensure_builtin_components
    from app.services.skill_package import ensure_system_canvas_skill_package

    with SessionLocal() as db:
        ensure_auth_identities(db)
        if not get_settings().demo_seed_enabled:
            return
        seed_demo_data(db)
        ensure_builtin_components(db, DEMO_WORKSPACE_ID)
        ensure_system_canvas_skill_package(db, DEMO_WORKSPACE_ID)
        if db.get(Project, "database") is None:
            db.add(Project(id="database", workspace_id=DEMO_WORKSPACE_ID, title="数据库", primary_goal_id="demo-goal", primary_graph_id="demo-graph", position=0))
        if db.get(Project, "systems") is None:
            db.add(Project(id="systems", workspace_id=DEMO_WORKSPACE_ID, title="系统设计", position=1))
        demo_session = db.get(ChatSession, "demo-session")
        if demo_session is not None and demo_session.project_id is None:
            demo_session.project_id = "database"
        db.commit()
