from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.schemas.learning import (
    AnswerRequest,
    AnswerResult,
    ExerciseBankItemView,
    ExerciseGenerateRequest,
    ExerciseView,
)
from app.providers.factory import model_provider_for_workspace
from app.services.authorization import AuthorizationService
from app.services.learning import ExerciseService


router = APIRouter(prefix="/exercises", tags=["exercises"])


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> ExerciseService:
    return ExerciseService(
        db,
        context.workspace_id,
        context.principal.user_id,
        model_provider_for_workspace(db, context.workspace_id, settings),
        settings,
    )


@router.get("", response_model=list[ExerciseBankItemView])
def list_exercises(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    wrong_only: bool = Query(default=False),
    node_id: str | None = Query(default=None),
    question_type: str | None = Query(default=None),
    batch_id: str | None = Query(default=None),
) -> list[ExerciseBankItemView]:
    authz = AuthorizationService(db, context.principal)
    items = service(db, context, settings).list(
        wrong_only=wrong_only,
        node_id=node_id,
        question_type=question_type,
        batch_id=batch_id,
    )
    return [
        item
        for item in items
        if authz.can_access_resource(context.workspace, "exercise", item.id, "read")
    ]


@router.post("/generate", response_model=list[ExerciseView], status_code=status.HTTP_201_CREATED)
def generate_exercises(
    payload: ExerciseGenerateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[ExerciseView]:
    return [
        ExerciseView.model_validate(item)
        for item in service(db, context, settings).generate(payload)
    ]


@router.post("/{exercise_id}/answer", response_model=AnswerResult, status_code=status.HTTP_201_CREATED)
def answer_exercise(
    exercise_id: str,
    payload: AnswerRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> AnswerResult:
    return service(db, context, settings).answer(exercise_id, payload)
