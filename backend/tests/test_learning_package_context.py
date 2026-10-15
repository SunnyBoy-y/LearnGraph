"""Tests for the learner-safe package projection used by chat prompts."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.domain.learning_package_models import LearningBuild, LearningEligibility, LearningPackage
from app.domain.models import Graph, GraphNode, Goal
from app.services.learning_package_context import learning_package_prompt_context


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'context.db').as_posix()}")
    Base.metadata.create_all(engine)
    return engine


def _node(db: Session, node_id: str, workspace_id: str = "w"):
    if db.get(Goal, f"goal-{workspace_id}") is None:
        db.add(Goal(id=f"goal-{workspace_id}", workspace_id=workspace_id, title="目标", raw_prompt="学习"))
        db.flush()
    if db.get(Graph, f"graph-{workspace_id}") is None:
        db.add(
            Graph(
                id=f"graph-{workspace_id}",
                workspace_id=workspace_id,
                goal_id=f"goal-{workspace_id}",
                title="图谱",
                status="published",
            )
        )
        db.flush()
    node = GraphNode(
        id=node_id,
        workspace_id=workspace_id,
        graph_id=f"graph-{workspace_id}",
        label="热锅与火候",
        description="观察食材变化",
        node_type="concept",
    )
    db.add(node)
    db.flush()
    db.add(LearningEligibility(node_id=node_id, workspace_id=workspace_id, graph_id=node.graph_id, current_package_id=None))
    return node


def test_published_context_excludes_private_answer_material(tmp_path):
    engine = _db(tmp_path)
    with Session(engine) as db:
        node = _node(db, "n1")
        db.add(
            LearningPackage(
                id="pkg1",
                workspace_id="w",
                node_id=node.id,
                build_id="build1",
                fingerprint="f",
                manifest={
                    "blueprint": {"title": "火候课", "objectives": ["判断火候"], "estimated_minutes": 15},
                    "lesson": {"sections": [{"id": "s1", "title": "为何热锅", "body": "先热锅，再下菜。", "takeaway": "先观察"}], "svg": '<svg><text>温度</text></svg>', "caption": "流程图"},
                    "activity": {"title": "练习", "instructions": "调节火力", "variables": [], "actions": [{"id": "heat", "label": "加热"}]},
                    "exam": {"title": "测验", "questions": [{"id": "q1", "prompt": "先做什么？", "options": ["热锅", "下菜"], "points": 10}]},
                },
                private_activity={"title": "练习", "solution": ["secret-action"], "actions": []},
                private_assessment={"answers": ["热锅"], "rubric": "隐藏评分规则"},
            )
        )
        db.get(LearningEligibility, node.id).current_package_id = "pkg1"
        db.commit()
        result = learning_package_prompt_context(db, "w", [node.id])

    assert "火候课" in result
    assert "先热锅，再下菜" in result
    assert "流程图" in result and "温度" in result
    assert "加热" in result and "先做什么？" in result
    assert "secret-action" not in result
    assert "隐藏评分规则" not in result
    assert "answer" not in result.lower()
    engine.dispose()


def test_preview_is_used_when_package_is_not_published_and_is_workspace_scoped(tmp_path):
    engine = _db(tmp_path)
    with Session(engine) as db:
        node = _node(db, "n2")
        db.add(
            LearningBuild(
                id="build2",
                workspace_id="w",
                node_id=node.id,
                actor_id="u",
                job_id="job2",
                trigger="on_demand",
                epoch=0,
                fingerprint="f",
                status="running",
                stage=2,
                checkpoints={
                    "blueprint": {"title": "预览课", "objectives": ["目标"], "estimated_minutes": 5},
                    "lesson": {"sections": [{"id": "s", "title": "概念", "body": "预览正文", "takeaway": "小结"}], "svg": "", "caption": ""},
                    "activity": {"title": "实验", "instructions": "按顺序操作", "variables": [], "actions": [{"id": "a", "label": "执行"}], "solution": ["hidden"]},
                    "exam": {"title": "试卷", "questions": [{"id": "q", "prompt": "公开题面", "options": ["A"] , "answers": ["secret"]}]},
                },
            )
        )
        db.commit()
        result = learning_package_prompt_context(db, "w", [node.id])
        other_workspace = learning_package_prompt_context(db, "other", [node.id])

    assert "预览课" in result and "预览正文" in result and "按顺序操作" in result
    assert "hidden" not in result and "secret" not in result
    assert other_workspace == ""
    engine.dispose()
