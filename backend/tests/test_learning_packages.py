"""Learning contract regression tests; provider calls are always local stubs."""
from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select, event
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.errors import AppError
from app.domain.models import Graph, GraphNode, Goal, Evidence, DurableJob, utc_now
from app.domain.learning_package_models import LearningEligibility, LearningBuild, LearningPackage, LearningAttempt, LearningAchievement
from app.domain.schemas.learning_packages import Blueprint, Lesson, Activity, ActivityScene, Exam, PolicyPatch, ProgressPatch
from app.services import learning_packages as lp
from app.repositories.domain import GraphNodeRepository
from app.services.durable_queue import DurableQueue

ACTIVITY = {"title":"炒菜实验", "instructions":"先热锅再放菜，观察温度。", "variables":[{"id":"heat","label":"温度","initial":0,"minimum":0,"maximum":2}], "actions":[
    {"id":"heat","label":"热锅","requires":[{"variable":"heat","operator":"eq","value":0}],"effects":[{"variable":"heat","operation":"set","value":1}],"feedback":"锅已热"},
    {"id":"cook","label":"下菜","requires":[{"variable":"heat","operator":"eq","value":1}],"effects":[{"variable":"heat","operation":"set","value":2}],"feedback":"完成"}],
    "success":[{"variable":"heat","operator":"eq","value":2}],"solution":["heat","cook"]}
EXAM = {"title":"实践关卡","pass_score":80,"practical_points":30,"questions":[
    {"id":f"q{i}","kind":"single_choice","prompt":"请选择正确操作","options":["先热锅","先下菜"],"points":p,"answers":["0"],"rubric":"选对得分","explanation":"需要先热锅","critical":False}
    for i,p in enumerate([20,20,30])]}
LESSON = {"sections":[{"id":f"s{i}","title":"步骤","body":"先了解热锅的作用，观察食物的变化并判断是否完成。","takeaway":"观察后操作"} for i in range(2)],"svg":'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="80" height="80"/></svg>',"caption":"锅具图解","html":""}
BLUEPRINT = {"title":"烹饪课","objectives":["掌握顺序"],"sections":["热锅","下菜"],"activity_brief":"烹饪小剧场","image_prompt":"","estimated_minutes":15}

@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path/'learning.db').as_posix()}")
    @event.listens_for(engine, "connect")
    def foreign_keys(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(lp, "SessionLocal", factory)
    # 生成层自带会话（并发时每路一个），测试里也要指向临时库。
    from app.services import learning_generation as lg
    monkeypatch.setattr(lg, "SessionLocal", factory)
    monkeypatch.setattr(lp, "actor_can_build", lambda *args: True)
    with factory() as session:
        goal = Goal(id="goal",workspace_id="w",title="烹饪",raw_prompt="学炒菜")
        session.add(goal); session.commit()
        session.add(Graph(id="graph",workspace_id="w",goal_id=goal.id,title="烹饪",status="published")); session.commit()
        yield session
    engine.dispose()

def node(db, id="n"):
    # Mirrors production: nodes are created through GraphNodeRepository, which
    # flushes the node row and only then opens its eligibility guard row.
    row = GraphNodeRepository(db, "w").add(
        GraphNode(id=id, workspace_id="w",graph_id="graph",label="热锅",description="学会热锅",node_type="practice"))
    db.commit()
    return row

def svc(db, actor="learner"):
    return lp.LearningPackageService(db,"w",actor)

def enable(db, revision=0, enabled=True):
    svc(db).set_policy("graph",PolicyPatch(enabled=enabled,expected_revision=revision))

def published(db):
    node(db)
    package = LearningPackage(id="pkg",workspace_id="w",node_id="n",build_id="build",fingerprint=lp.fingerprint(db.get(GraphNode,"n")),
        manifest={"lesson":LESSON,"activity":lp.public_activity(ACTIVITY)},private_activity=ACTIVITY,private_assessment=EXAM)
    db.add(package); db.flush()
    db.get(LearningEligibility,"n").current_package_id=package.id
    db.commit()
    return package

def lease(db, kind):
    job = DurableQueue(db).claim_next(kind=kind)
    assert job
    return job


@pytest.mark.parametrize("job_status, expected", [("failed", "failed"), ("completed", "failed"), ("cancelled", "cancelled"), ("queued", "queued"), ("leased", "running")])
def test_build_status_tracks_dispatcher_failure(db, job_status, expected):
    node(db)
    run = svc(db).enqueue("n", "on_demand")
    run.status, run.stage = "running", 1
    job = db.get(DurableJob, run.job_id)
    job.status, job.last_error = job_status, "private provider diagnostic"
    db.commit()
    view = svc(db).page("n")["build"]
    assert view["status"] == expected
    assert view["completed_stages"] == 1
    assert "private provider diagnostic" not in str(view)


def test_failed_dispatcher_can_resume_same_build_checkpoint(db, monkeypatch):
    from app.api.routers import learning_packages as router
    node(db)
    run = svc(db).enqueue("n", "on_demand")
    run.status, run.stage = "running", 1
    run.checkpoints = {"blueprint": BLUEPRINT, "inflight_stage": 1}
    db.get(DurableJob, run.job_id).status = "failed"
    db.commit()
    from types import SimpleNamespace
    context = SimpleNamespace(workspace_id="w", principal=SimpleNamespace(user_id="learner"))
    monkeypatch.setattr(router, "node_service", lambda *args: svc(db))
    view = router.control_build(run.id, "retry", db, context)
    assert view["status"] == "queued"
    assert view["id"] == run.id
    assert run.stage == 1
    assert run.checkpoints == {"blueprint": BLUEPRINT}
    assert db.get(DurableJob, run.job_id).lease_token is None

def test_only_future_unengaged_nodes_and_window(db):
    node(db,"old"); enable(db)
    for i in range(5): node(db,f"new{i}")
    db.add(Evidence(workspace_id="w",node_id="new0",source_type="dialogue",summary="已问",confidence=.7)); db.commit()
    assert lp.reconcile_learning_builds()==3
    assert lp.reconcile_learning_builds()==0
    db.expire_all()
    assert {r.node_id for r in db.scalars(select(LearningBuild))}=={"new1","new2","new3"}
    svc(db).engage(db.get(GraphNode,"new1")); db.commit()
    assert lp.reconcile_learning_builds()==1
    with pytest.raises(AppError): svc(db).enqueue("old","manual")

def test_policy_off_cancels_and_reenable_does_not_sweep_old_epoch(db):
    enable(db); node(db)
    run=svc(db).enqueue("n","auto")
    enable(db,revision=1,enabled=False)
    assert db.get(LearningBuild,run.id).status=="cancelled"
    assert db.get(DurableJob,run.job_id).status=="cancelled"
    enable(db,revision=2)
    assert lp.reconcile_learning_builds()==0

def test_activity_rules_and_private_projection(db):
    published(db)
    lp.validate_activity(ACTIVITY)
    with pytest.raises(AppError): lp.transition(ACTIVITY,lp.initial_activity(ACTIVITY),"cook")
    training=svc(db).start("n")
    training=svc(db).progress(training["id"],ProgressPatch(expected_revision=0,action_id="heat"))
    with pytest.raises(AppError): svc(db).progress(training["id"],ProgressPatch(expected_revision=0,action_id="cook"))
    db.rollback()
    attempt=svc(db).start_attempt("n","request-01")
    assert not attempt["activity"].get("completed")
    assert attempt["activity"]["values"]=={"heat":0}
    assert "answers" not in attempt["paper"]["questions"][0]
    assert "success" not in attempt["activity_spec"]
    with pytest.raises(AppError): svc(db,"intruder").owned(LearningAttempt,attempt["id"])


def test_build_preview_redacts_private_answers_and_exposes_completed_sections(db):
    node(db)
    build = LearningBuild(
        id="preview-build", workspace_id="w", node_id="n", actor_id="learner", job_id="preview-job",
        trigger="on_demand", epoch=0, fingerprint="f", status="failed", stage=3,
        checkpoints={"blueprint": BLUEPRINT, "lesson": LESSON, "activity": ACTIVITY,
                     "scene": {"html": "<main>实验</main>"}, "exam": EXAM},
    )
    preview = lp.LearningPackageService.build_preview(build)
    assert preview["available"]["lesson"] and preview["available"]["activity"]
    assert preview["activity"]["html"] == "<main>实验</main>"
    assert "solution" not in preview["activity"]
    assert "answers" not in preview["exam"]["questions"][0]
    assert "rubric" not in preview["exam"]["questions"][0]


def test_training_starts_from_partial_build_without_publishing_formal_package(db):
    node(db)
    build = svc(db).enqueue("n", "on_demand")
    build.stage = 3
    build.checkpoints = {"blueprint": BLUEPRINT, "lesson": LESSON, "activity": ACTIVITY,
                         "scene": {"html": "<main>实验</main>"}}
    db.commit()
    enrollment = svc(db).start("n")
    draft = db.scalar(select(LearningPackage).where(LearningPackage.build_id == build.id))
    assert draft is not None and draft.manifest["draft"] is True
    assert db.get(LearningEligibility, "n").current_package_id is None
    assert enrollment["package_id"] == draft.id
    with pytest.raises(AppError) as error:
        svc(db).start_attempt("n", "formal-before-publish")
    assert error.value.code == "package_not_ready"

@pytest.mark.parametrize("complete,expected", [(False,"failed"),(True,"passed")])
def test_server_grading_requires_practice_and_badge_is_personal(db, monkeypatch,complete,expected):
    published(db)
    monkeypatch.setattr("app.services.mastery.MasteryService.apply_evidence",lambda *a:None)
    api=svc(db); attempt=api.start_attempt("n","request-01")
    attempt=api.patch_attempt(attempt["id"],attempt["revision"],answers={f"q{i}":"0" for i in range(3)})
    if complete:
        for action in ["heat","cook"]: attempt=api.patch_attempt(attempt["id"],attempt["revision"],action_id=action)
    api.submit(attempt["id"]); api.submit(attempt["id"])
    job=lease(db,"learning.package.grade")
    lp.grade_attempt(job.id,job.lease_token,attempt["id"])
    lp.grade_attempt(job.id,job.lease_token,attempt["id"])
    db.expire_all()
    assert db.get(LearningAttempt,attempt["id"]).status==expected
    assert len(db.scalars(select(LearningAchievement)).all())==int(complete)
    assert len(db.scalars(select(Evidence)).all())==1
    assert api.page("n")["achievement"] is not None if complete else api.page("n")["achievement"] is None
    assert svc(db,"other").page("n")["achievement"] is None

def test_pipeline_checkpoints_and_engagement_publication_race(db,monkeypatch):
    enable(db); node(db)
    calls=[]
    def generate_checked(_workspace_id,_actor_id,schema,_prompt,validator=None,normalize=None,*,label="",attempts=None,notes=None,**_kwargs):
        calls.append(schema)
        value=deepcopy({Blueprint:BLUEPRINT,Lesson:LESSON,Activity:ACTIVITY,ActivityScene:{"html":"<main><h1>烹饪实验</h1><p>观察热锅，再选择操作。独立的实验场景。</p></main>"},Exam:EXAM}[schema])
        value=schema.model_validate(normalize(value) if normalize else value).model_dump()
        if validator: validator(value)
        return value
    # 教材‖实验规则、小剧场‖测评试卷现在各自并发产出，但每个阶段仍然各自落一次 checkpoint。
    monkeypatch.setattr(lp,"generate_checked",generate_checked)
    build=svc(db).enqueue("n","auto")
    for stage in range(6):
        job=lease(db,"learning.package.build")
        assert not lp.run_build_stage(job.id,job.lease_token,build.id)
        DurableQueue(db,worker_id=job.lease_owner).rearm(job,now=utc_now()-timedelta(seconds=1))
    svc(db).engage(db.get(GraphNode,"n")); db.commit()
    job=lease(db,"learning.package.build")
    assert lp.run_build_stage(job.id,job.lease_token,build.id)
    db.expire_all()
    assert db.get(LearningBuild,build.id).status=="skipped"
    assert not db.scalars(select(LearningPackage)).all()
    assert len(calls)==5


def test_malformed_scene_uses_local_interactive_fallback_and_keeps_exam(db, monkeypatch):
    enable(db); node(db)
    build=svc(db).enqueue("n", "on_demand")
    build.stage=3
    build.checkpoints={"blueprint":BLUEPRINT,"lesson":LESSON,"activity":ACTIVITY,"inflight_stage":3}
    db.commit()
    def generate_checked(_workspace_id,_actor_id,schema,_prompt,validator=None,normalize=None,*,label="",attempts=None,notes=None,**_kwargs):
        if schema is ActivityScene:
            raise lp.GenerationFailure(lp.CATEGORY_STRUCTURAL, 3, "互动小剧场：Provider response contains invalid JSON", label=label)
        value=deepcopy(EXAM)
        value=schema.model_validate(normalize(value) if normalize else value).model_dump()
        if validator: validator(value)
        return value
    monkeypatch.setattr(lp,"generate_checked",generate_checked)
    job=lease(db,"learning.package.build")
    assert not lp.run_build_stage(job.id,job.lease_token,build.id)
    db.expire_all()
    stored=db.get(LearningBuild,build.id)
    assert stored.status == "running"
    assert stored.stage == 4
    assert stored.checkpoints["scene"]["html"].startswith("<section data-lg-fallback")
    assert "__lgSubapp.emit" in stored.checkpoints["scene"]["html"]
    assert stored.checkpoints["exam"]["title"] == EXAM["title"]
    assert any("内置交互模板" in note for note in stored.checkpoints["notes"])


def test_scene_fallback_is_valid_self_contained_html():
    scene=lp.fallback_scene_html(ACTIVITY)
    lp.validate_html(scene)
    assert "http://" not in scene and "fetch(" not in scene
    assert 'data-action="heat"' in scene

def test_demand_adopts_auto_and_publishes_no_chat(db,monkeypatch):
    enable(db); node(db)
    build=svc(db).enqueue("n","auto")
    assert svc(db).enqueue("n","on_demand").id==build.id
    build.stage=6
    build.checkpoints={"blueprint":BLUEPRINT,"lesson":LESSON,"activity":ACTIVITY,"exam":EXAM,"scene":{"html":""}}
    db.commit()
    job=lease(db,"learning.package.build")
    assert lp.run_build_stage(job.id,job.lease_token,build.id)
    db.expire_all()
    assert svc(db).page("n")["package"]
    assert db.get(LearningBuild,build.id).status=="ready"

def test_lost_lease_never_replays_and_interrupted_stage_replays_within_budget(db,monkeypatch):
    """租约失效绝不触碰模型；被中断的阶段按上限自动重放，超限才交回人工。"""
    node(db); build=svc(db).enqueue("n","on_demand")
    job=lease(db,"learning.package.build")
    calls=[]
    def generate_checked(_workspace_id,_actor_id,schema,_prompt,validator=None,normalize=None,*,label="",attempts=None,notes=None,**_kwargs):
        calls.append(label)
        return deepcopy(BLUEPRINT)
    monkeypatch.setattr(lp,"generate_checked",generate_checked)
    # 1) 失去租约的处理路径不得调用模型。
    with pytest.raises(AppError): lp.run_build_stage(job.id,"wrong-token",build.id)
    db.rollback()
    # 2) 上次生成中断：在上限内自动重放一次，并留下可追溯的记录。
    build.checkpoints={"inflight_stage":0}; db.commit()
    replayed = lp.run_build_stage(job.id,job.lease_token,build.id)
    db.expire_all()
    assert not replayed, f"应当自动重放：status={build.status} error={build.error} stage={build.stage} replays={build.checkpoints.get('replays')}"
    db.expire_all()
    assert len(calls)==1
    assert build.checkpoints["replays"]=={"0":1}
    assert any("已自动重放" in note for note in build.checkpoints["notes"])
    assert "inflight_stage" not in build.checkpoints
    # 3) 超过重放上限：不再花钱，交回人工重试。
    build.stage=1; build.checkpoints={**build.checkpoints,"inflight_stage":1,"replays":{"0":1,"1":1}}; db.commit()
    assert lp.run_build_stage(job.id,job.lease_token,build.id)
    db.expire_all()
    assert len(calls)==1
    assert db.get(LearningBuild,build.id).status=="failed"
    assert "请手动重试本阶段" in db.get(LearningBuild,build.id).error

@pytest.mark.parametrize("payload",['<svg viewBox="0 0 1 1"><script/></svg>','<svg viewBox="0 0 1 1" onload="alert(1)"/>'])
def test_reject_active_svg(payload):
    with pytest.raises(ValueError): lp.validate_svg(payload)

def test_reject_impossible_experiment_and_invalid_exam():
    bad=deepcopy(ACTIVITY); bad["solution"]=["heat","heat"]
    with pytest.raises(ValueError): lp.validate_activity(bad)
    bad=deepcopy(EXAM); bad["questions"][0]["points"]=99
    with pytest.raises(ValueError): Exam.model_validate(bad)
