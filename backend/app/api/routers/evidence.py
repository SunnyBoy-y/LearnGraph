from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentWorkspace, DB
from app.domain.schemas.learning import (
    CapabilityReportView,
    EvidenceCreateRequest,
    EvidenceDecisionRequest,
    EvidenceView,
    MasteryAlignmentView,
    MasteryNodeView,
    MasteryReviewJobView,
    MasteryReviewRunRequest,
    MasterySchedulerTickView,
    MasteryScheduleView,
    MasterySessionStateView,
)
from app.services.learning import EvidenceService
from app.services.mastery import MasteryService


router = APIRouter(tags=["evidence-mastery"])

# 旧版证据模型说明（2026-08-08 决策 D-20260808-01）
# -----------------------------------------------------------
# POST /evidence 是旧版证据写入模型，写入独立的 EvidenceRecord 存储；
# 当前证据链路已统一收敛到事件溯源版 POST /api/v1/learning/evidence
# （事件 -> 投影状态机 -> MasteryService）。前端与 Agent 运行时均未调用
# 旧写入端点，保留它仅为兼容期兼容，不再新增 UI/工具入口。
# GET /evidence 与 POST /evidence/{evidence_id}/decision 仍用于读取和
# 审核既有旧版证据，暂不废弃。
# -----------------------------------------------------------


def service(db: DB, context: CurrentWorkspace) -> EvidenceService:
    return EvidenceService(db, context.workspace_id, context.principal.user_id)


def mastery_service(db: DB, context: CurrentWorkspace) -> MasteryService:
    return MasteryService(db, context.workspace_id, context.principal.user_id)


@router.get("/evidence", response_model=list[EvidenceView])
def list_evidence(db: DB, context: CurrentWorkspace) -> list[EvidenceView]:
    return [EvidenceView.model_validate(item) for item in service(db, context).list()]


@router.post(
    "/evidence",
    response_model=EvidenceView,
    status_code=status.HTTP_201_CREATED,
    deprecated=True,
)
def create_evidence(payload: EvidenceCreateRequest, db: DB, context: CurrentWorkspace) -> EvidenceView:
    """DEPRECATED: 旧版证据写入模型，已由 POST /api/v1/learning/evidence 取代。

    兼容期保留本端点以读取/迁移既有数据链路；新功能一律写入
    /api/v1/learning/evidence，后续版本将移除本端点。
    """
    return EvidenceView.model_validate(service(db, context).create(payload))


@router.post("/evidence/{evidence_id}/decision", response_model=EvidenceView)
def decide_evidence(
    evidence_id: str,
    payload: EvidenceDecisionRequest,
    db: DB,
    context: CurrentWorkspace,
) -> EvidenceView:
    return EvidenceView.model_validate(service(db, context).decide(evidence_id, payload))


@router.get("/mastery", response_model=list[MasteryNodeView])
def mastery(db: DB, context: CurrentWorkspace) -> list[MasteryNodeView]:
    return service(db, context).mastery()


@router.get("/mastery/nodes/{node_id}/alignment", response_model=MasteryAlignmentView)
def mastery_alignment(
    node_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> MasteryAlignmentView:
    return service(db, context).mastery_alignment(node_id)


@router.get("/mastery/capability-report", response_model=CapabilityReportView)
def capability_report(db: DB, context: CurrentWorkspace) -> CapabilityReportView:
    return service(db, context).capability_report()


@router.get("/mastery/schedules", response_model=list[MasteryScheduleView])
def list_mastery_schedules(db: DB, context: CurrentWorkspace) -> list[MasteryScheduleView]:
    return [
        MasteryScheduleView.model_validate(item)
        for item in mastery_service(db, context).list_schedules()
    ]


@router.get("/mastery/review-jobs", response_model=list[MasteryReviewJobView])
def list_mastery_review_jobs(db: DB, context: CurrentWorkspace) -> list[MasteryReviewJobView]:
    return [
        MasteryReviewJobView.model_validate(item)
        for item in mastery_service(db, context).list_review_jobs()
    ]


@router.get("/mastery/session-states", response_model=list[MasterySessionStateView])
def list_mastery_session_states(
    db: DB,
    context: CurrentWorkspace,
) -> list[MasterySessionStateView]:
    return [
        MasterySessionStateView.model_validate(item)
        for item in mastery_service(db, context).list_session_states()
    ]


@router.post("/mastery/scheduler/tick", response_model=MasterySchedulerTickView)
def tick_mastery_scheduler(
    db: DB,
    context: CurrentWorkspace,
) -> MasterySchedulerTickView:
    return mastery_service(db, context).scheduler_tick()


@router.post("/mastery/review-runs", response_model=MasteryReviewJobView, status_code=status.HTTP_201_CREATED)
def run_mastery_review(
    payload: MasteryReviewRunRequest,
    db: DB,
    context: CurrentWorkspace,
) -> MasteryReviewJobView:
    return MasteryReviewJobView.model_validate(
        mastery_service(db, context).run_review(
            trigger=payload.trigger,
            node_ids=payload.node_ids or None,
        )
    )
