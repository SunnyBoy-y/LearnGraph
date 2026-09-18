from fastapi import APIRouter
from sqlalchemy import select, update

from app.api.deps import CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.models import Graph, DurableJob
from app.domain.learning_package_models import LearningBuild, LearningAttempt, LearningEnrollment, LearningPackage
from app.domain.schemas.learning_packages import PolicyPatch, BuildRequest, ProgressPatch, AttemptStart, AnswerPatch
from app.services.authorization import AuthorizationService
from app.services.learning_packages import LearningPackageService

router = APIRouter(prefix="/learning-pages", tags=["learning-pages"])


def service(db, context):
    return LearningPackageService(db, context.workspace_id, context.principal.user_id)


def authorize_graph(db, context, graph_id, write=False):
    graph = db.scalar(select(Graph).where(Graph.id == graph_id, Graph.workspace_id == context.workspace_id))
    if graph is None or not AuthorizationService(db, context.principal).can_access_bindings(
        context.workspace, "write" if write else "read", graph_id=graph_id
    ):
        raise AppError(404, "graph_not_found", "图谱不存在或无权访问。")


def node_service(db, context, node_id, write=False):
    svc = service(db, context)
    authorize_graph(db, context, svc.node(node_id).graph_id, write)
    return svc


@router.get("/graphs/{graph_id}/policy")
def get_policy(graph_id: str, db: DB, context: CurrentWorkspace):
    authorize_graph(db, context, graph_id)
    return service(db, context).policy_view(graph_id)


@router.patch("/graphs/{graph_id}/policy")
def patch_policy(graph_id: str, payload: PolicyPatch, db: DB, context: CurrentWorkspace):
    authorize_graph(db, context, graph_id, True)
    return service(db, context).set_policy(graph_id, payload)


@router.get("/graphs/{graph_id}/builds")
def builds(graph_id: str, db: DB, context: CurrentWorkspace):
    authorize_graph(db, context, graph_id)
    from app.domain.models import GraphNode
    rows = db.scalars(select(LearningBuild).join(GraphNode, GraphNode.id == LearningBuild.node_id).where(
        GraphNode.graph_id == graph_id, LearningBuild.workspace_id == context.workspace_id).order_by(LearningBuild.created_at.desc()).limit(100)).all()
    jobs = {job.id: job for job in db.scalars(select(DurableJob).where(DurableJob.id.in_([row.job_id for row in rows])))}
    return [LearningPackageService.build_view(row, jobs.get(row.job_id)) for row in rows]


@router.get("/nodes/{node_id}")
def page(node_id: str, db: DB, context: CurrentWorkspace):
    return node_service(db, context, node_id).page(node_id)


@router.post("/nodes/{node_id}/build", status_code=202)
def build(node_id: str, payload: BuildRequest, db: DB, context: CurrentWorkspace):
    svc = node_service(db, context, node_id, True)
    row = svc.enqueue(node_id, payload.trigger)
    db.commit()
    return svc.build_view(row, db.get(DurableJob, row.job_id))


@router.post("/builds/{build_id}/{action}")
def control_build(build_id: str, action: str, db: DB, context: CurrentWorkspace):
    row = db.scalar(select(LearningBuild).where(LearningBuild.id == build_id, LearningBuild.workspace_id == context.workspace_id))
    if row is None:
        raise AppError(404, "build_not_found", "构建任务不存在。")
    node_service(db, context, row.node_id, True)
    # Use the worker's lock order (job, then build), and re-read after locking.
    db.execute(update(DurableJob).where(DurableJob.id == row.job_id).values(dedupe_key=DurableJob.dedupe_key))
    db.refresh(row)
    job = db.get(DurableJob, row.job_id)
    if job is not None:
        db.refresh(job)
    visible_status = LearningPackageService.build_view(row, job)["status"]
    if action == "cancel":
        if row.status in {"queued", "running"}:
            row.status = "cancelled"
            db.execute(update(DurableJob).where(DurableJob.id == row.job_id).values(status="cancelled", lease_token=None))
    elif action == "retry" and visible_status == "failed":
        from app.services.learning_packages import build_allowed
        if not build_allowed(db, row, service(db, context).node(row.node_id)):
            raise AppError(409, "build_ineligible", "节点资格已变化，无法继续预构建。")
        from app.domain.models import utc_now
        row.status, row.error = "queued", None
        row.checkpoints = {k: v for k, v in row.checkpoints.items() if k != "inflight_stage"}
        db.execute(update(DurableJob).where(DurableJob.id == row.job_id).values(status="queued", lease_token=None,
            lease_owner=None, lease_expires_at=None, available_at=utc_now(), attempt_count=0))
    else:
        raise AppError(409, "build_action_invalid", "当前任务不能执行此操作。")
    db.commit()
    return LearningPackageService.build_view(row, db.get(DurableJob, row.job_id))


@router.post("/nodes/{node_id}/start")
def start(node_id: str, db: DB, context: CurrentWorkspace):
    return node_service(db, context, node_id, True).start(node_id)


@router.patch("/enrollments/{enrollment_id}")
def progress(enrollment_id: str, payload: ProgressPatch, db: DB, context: CurrentWorkspace):
    svc = service(db, context)
    enrollment = svc.owned(LearningEnrollment, enrollment_id)
    package = db.get(LearningPackage, enrollment.package_id)
    node_service(db, context, package.node_id, True)
    return svc.progress(enrollment_id, payload)


@router.post("/nodes/{node_id}/attempts")
def start_attempt(node_id: str, payload: AttemptStart, db: DB, context: CurrentWorkspace):
    return node_service(db, context, node_id, True).start_attempt(node_id, payload.request_key)


def attempt_service(db, context, attempt_id, write=False):
    svc = service(db, context)
    row = svc.owned(LearningAttempt, attempt_id)
    node_service(db, context, row.node_id, write)
    return svc, row


@router.get("/attempts/{attempt_id}")
def attempt(attempt_id: str, db: DB, context: CurrentWorkspace):
    svc, row = attempt_service(db, context, attempt_id)
    return svc.attempt_view(row)


@router.patch("/attempts/{attempt_id}/answers")
def answers(attempt_id: str, payload: AnswerPatch, db: DB, context: CurrentWorkspace):
    svc, _ = attempt_service(db, context, attempt_id, True)
    return svc.patch_attempt(attempt_id, payload.expected_revision, answers=payload.answers)


@router.patch("/attempts/{attempt_id}/activity")
def activity(attempt_id: str, payload: ProgressPatch, db: DB, context: CurrentWorkspace):
    if payload.section_id:
        raise AppError(422, "activity_operation_invalid", "正式测评只接受实验操作或重置。")
    svc, _ = attempt_service(db, context, attempt_id, True)
    return svc.patch_attempt(attempt_id, payload.expected_revision, action_id=payload.action_id, reset=payload.reset_activity)


@router.post("/attempts/{attempt_id}/submit")
def submit(attempt_id: str, db: DB, context: CurrentWorkspace):
    svc, _ = attempt_service(db, context, attempt_id, True)
    return svc.submit(attempt_id)
