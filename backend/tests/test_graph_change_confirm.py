"""Regression: confirming a reviewed graph must survive foreign-key enforcement.

The confirm transaction creates both the node rows and each node's
learning-package eligibility guard row. The guard references
``graph_nodes(id)``/``graphs(id)`` and the two mappers declare no relationship,
so the ORM ordered them by ``module.ClassName`` and emitted the guard's INSERT
first -- SQLite rejected it (``PRAGMA foreign_keys=ON``) and the whole confirm
rolled back with ``FOREIGN KEY constraint failed``, leaving the proposal stuck at
``proposed``. The ordering is now explicit in ``GraphNodeRepository.add``.

These tests intentionally enable ``foreign_keys`` the way
``app.core.database._configure_sqlite_connection`` does in production: a test
harness without FK enforcement cannot see this class of bug at all.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.domain.learning_package_models import LearningEligibility
from app.domain.models import (
    ChatSession,
    Goal,
    Graph,
    GraphChangeSet,
    GraphEdge,
    GraphNode,
    Message,
)
from app.repositories.domain import GraphNodeRepository
from app.services.graph_changes import GraphChangeSetService

PROPOSAL = {
    "graph_title": "确认链路回归",
    "summary": "三节点最小提案：单根加两层 contains，专门守住确认路径上的外键写入顺序。",
    "nodes": [
        {
            "ref": "R",
            "change": "add",
            "label": "根主题",
            "description": "图谱的唯一根节点。",
            "node_type": "root",
            "rationale": "create 提案必须恰好一个根。",
        },
        {
            "ref": "A",
            "change": "add",
            "label": "一级板块",
            "description": "挂在根下的一级知识板块。",
            "node_type": "concept",
            "rationale": "构成主干。",
        },
        {
            "ref": "B",
            "change": "add",
            "label": "二级知识点",
            "description": "挂在一级板块下的细分知识点。",
            "node_type": "concept",
            "rationale": "构成细分层。",
        },
    ],
    "edges": [
        {"source_ref": "R", "target_ref": "A", "relation": "contains", "rationale": "主干归属根。"},
        {"source_ref": "A", "target_ref": "B", "relation": "contains", "rationale": "细分归属主干。"},
    ],
}


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'confirm.db').as_posix()}")

    @event.listens_for(engine, "connect")
    def _foreign_keys(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as session:
        yield session
    engine.dispose()


def _goal_and_graph(db):
    goal = Goal(workspace_id="w", title="目标", raw_prompt="做一个图谱", status="confirmed")
    db.add(goal)
    db.flush()
    graph = Graph(workspace_id="w", goal_id=goal.id, title="图谱", status="candidate")
    db.add(graph)
    db.flush()
    return goal, graph


def test_node_repository_opens_the_guard_after_the_node_row(db):
    """The guard row must land after the node it references, never with it."""

    _goal, graph = _goal_and_graph(db)

    node = GraphNodeRepository(db, "w").add(
        GraphNode(workspace_id="w", graph_id=graph.id, label="节点")
    )
    # Committing is the assertion: with FK enforcement on, a guard sharing the
    # node's flush raises FOREIGN KEY constraint failed here.
    db.commit()

    guard = db.get(LearningEligibility, node.id)
    assert guard is not None
    assert guard.graph_id == graph.id
    assert guard.first_engaged_at is None


def test_confirm_materializes_graph_with_an_eligibility_guard_per_node(db):
    """The incident itself: reviewing a create proposal must publish the graph."""

    session = ChatSession(workspace_id="w", title="会话")
    goal = Goal(workspace_id="w", title="目标", raw_prompt="做一个图谱", status="confirmed")
    db.add_all([session, goal])
    db.flush()
    # graph_change_sets references both source messages, so they have to exist.
    user_message = Message(workspace_id="w", session_id=session.id, role="user", content="做一个图谱")
    assistant_message = Message(workspace_id="w", session_id=session.id, role="assistant", content="图谱提案")
    db.add_all([user_message, assistant_message])
    db.flush()
    change_set = GraphChangeSet(
        workspace_id="w",
        session_id=session.id,
        goal_id=goal.id,
        source_user_message_id=user_message.id,
        source_assistant_message_id=assistant_message.id,
        mode="create",
        status="proposed",
        base_revision=0,
        proposal=PROPOSAL,
        provider_trace={},
    )
    db.add(change_set)
    db.commit()

    confirmed = GraphChangeSetService(db, "w", "tester").confirm(session.id, change_set.id)

    assert confirmed.status == "confirmed"
    assert confirmed.graph_id
    nodes = db.scalars(
        select(GraphNode).where(GraphNode.graph_id == confirmed.graph_id)
    ).all()
    assert len(nodes) == len(PROPOSAL["nodes"])
    guards = db.scalars(
        select(LearningEligibility).where(LearningEligibility.graph_id == confirmed.graph_id)
    ).all()
    # Invariant: every materialized node is sweepable by the learning packages.
    assert {guard.node_id for guard in guards} == {node.id for node in nodes}
    assert db.scalars(
        select(GraphEdge).where(GraphEdge.graph_id == confirmed.graph_id)
    ).all()
    graph = db.get(Graph, confirmed.graph_id)
    assert graph is not None and graph.status == "published"
    assert db.get(Goal, goal.id).status == "approved"
