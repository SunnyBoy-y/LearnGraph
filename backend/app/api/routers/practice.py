from __future__ import annotations

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.schemas.practice import (
    PracticeAnswerRequest,
    PracticeAnswerResult,
    PracticeHintView,
    PracticeLearningReportView,
    PracticeOverviewView,
    PracticeRevealView,
    PracticeSessionCreateRequest,
    PracticeSessionReportView,
    PracticeSessionSummaryView,
    PracticeSessionView,
    WrongBookView,
)
from app.domain.settings import PRACTICE_EXERCISE_MODEL_SETTING_KEY
from app.providers.factory import feature_model_target, model_provider_for_workspace
from app.services.practice import PracticeService


router = APIRouter(prefix="/practice", tags=["practice"])


class PracticeCompleteRequest(BaseModel):
    abandon: bool = False


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> PracticeService:
    return PracticeService(
        db,
        context.workspace_id,
        context.principal.user_id,
        model_provider_for_workspace(
            db,
            context.workspace_id,
            settings,
            # 出题与判分走「练习」专属模型；未配置时回落工作区对话模型。
            **feature_model_target(
                db, context.workspace_id, PRACTICE_EXERCISE_MODEL_SETTING_KEY
            ),
        ),
        settings,
    )


@router.get("/overview", response_model=PracticeOverviewView)
def practice_overview(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    tz_offset_minutes: int = Query(default=0, ge=-840, le=840),
) -> PracticeOverviewView:
    return service(db, context, settings).overview(tz_offset_minutes=tz_offset_minutes)


@router.post(
    "/sessions",
    response_model=PracticeSessionView,
    status_code=status.HTTP_201_CREATED,
)
def create_practice_session(
    payload: PracticeSessionCreateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    tz_offset_minutes: int = Query(default=0, ge=-840, le=840),
) -> PracticeSessionView:
    return service(db, context, settings).create_session(
        payload, tz_offset_minutes=tz_offset_minutes
    )


@router.get("/sessions", response_model=list[PracticeSessionSummaryView])
def list_practice_sessions(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    status_filter: str | None = Query(default="completed", alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[PracticeSessionSummaryView]:
    return service(db, context, settings).list_sessions(
        limit=limit, status=status_filter
    )


@router.get("/sessions/{practice_session_id}", response_model=PracticeSessionView)
def get_practice_session(
    practice_session_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> PracticeSessionView:
    return service(db, context, settings).get_session(practice_session_id)


@router.post(
    "/sessions/{practice_session_id}/answer",
    response_model=PracticeAnswerResult,
    status_code=status.HTTP_201_CREATED,
)
def answer_practice_question(
    practice_session_id: str,
    payload: PracticeAnswerRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> PracticeAnswerResult:
    return service(db, context, settings).answer_question(practice_session_id, payload)


@router.post(
    "/sessions/{practice_session_id}/items/{exercise_id}/hint",
    response_model=PracticeHintView,
)
def request_practice_hint(
    practice_session_id: str,
    exercise_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> PracticeHintView:
    return service(db, context, settings).hint_item(practice_session_id, exercise_id)


@router.get(
    "/sessions/{practice_session_id}/items/{exercise_id}/review",
    response_model=PracticeRevealView,
)
def reveal_practice_item(
    practice_session_id: str,
    exercise_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> PracticeRevealView:
    return service(db, context, settings).reveal_item(practice_session_id, exercise_id)


@router.post(
    "/sessions/{practice_session_id}/complete",
    response_model=PracticeSessionReportView,
)
def complete_practice_session(
    practice_session_id: str,
    payload: PracticeCompleteRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> PracticeSessionReportView:
    return service(db, context, settings).complete_session(
        practice_session_id, abandon=payload.abandon
    )


@router.get(
    "/sessions/{practice_session_id}/report",
    response_model=PracticeSessionReportView,
)
def practice_session_report(
    practice_session_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> PracticeSessionReportView:
    return service(db, context, settings).session_report(practice_session_id)


@router.get("/wrong-book", response_model=WrongBookView)
def practice_wrong_book(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    limit: int = Query(default=50, ge=1, le=200),
) -> WrongBookView:
    return service(db, context, settings).wrong_book(limit=limit)


@router.get("/report", response_model=PracticeLearningReportView)
def practice_learning_report(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    window: str = Query(default="7d", pattern="^(7d|30d|all)$"),
    tz_offset_minutes: int = Query(default=0, ge=-840, le=840),
) -> PracticeLearningReportView:
    return service(db, context, settings).learning_report(
        window=window, tz_offset_minutes=tz_offset_minutes
    )
