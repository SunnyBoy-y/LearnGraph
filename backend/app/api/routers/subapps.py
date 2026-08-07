from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.services.subapp_bundles import SubAppBundleService
from app.core.errors import AppError
from app.domain.schemas.subapps import (
    SubAppEventIngestedView,
    SubAppEventIngestRequest,
    SubAppEventListView,
    SubAppSessionCreateRequest,
    SubAppSessionCreatedView,
    SubAppSessionEventAcceptedView,
    SubAppSessionEventRequest,
    SubAppSessionView,
    SubAppStateListView,
    SubAppStateView,
)
from app.services.subapps import (
    MAX_SUBAPP_EVENT_LIST_LIMIT,
    MAX_SUBAPP_STATE_LIST_LIMIT,
    SubAppService,
)


router = APIRouter(prefix="/subapps", tags=["subapps"])

SessionPath = Annotated[str, Path(min_length=1, max_length=36)]


def service(db: DB, context: CurrentWorkspace) -> SubAppService:
    return SubAppService(db, context.workspace_id, context.principal.user_id)


@router.post(
    "/events",
    response_model=SubAppEventIngestedView,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_subapp_event(
    payload: SubAppEventIngestRequest,
    db: DB,
    context: CurrentWorkspace,
) -> SubAppEventIngestedView:
    """Accept a P1 host-relayed event; T2 iframe token enforcement is pending."""
    return SubAppEventIngestedView(event=service(db, context).ingest(payload))


@router.get("/events", response_model=SubAppEventListView)
def list_subapp_events(
    db: DB,
    context: CurrentWorkspace,
    session_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
    event_type: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_SUBAPP_EVENT_LIST_LIMIT)] = 50,
) -> SubAppEventListView:
    if created_after is not None and created_before is not None and created_after > created_before:
        raise AppError(
            422,
            "subapp_event_time_range_invalid",
            "created_after must not be later than created_before",
        )
    return service(db, context).list_events(
        session_id=session_id,
        event_type=event_type,
        created_after=created_after,
        created_before=created_before,
        offset=offset,
        limit=limit,
    )


@router.get("/bundles/{bundle_id}/preview")
def mint_bundle_preview(
    bundle_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
):
    result = SubAppBundleService(
        db, context.workspace_id, context.principal.user_id, settings
    ).mint_preview(bundle_id)
    return result


@router.post(
    "/sessions",
    response_model=SubAppSessionCreatedView,
    status_code=status.HTTP_201_CREATED,
)
def create_subapp_session(
    payload: SubAppSessionCreateRequest,
    db: DB,
    context: CurrentWorkspace,
) -> SubAppSessionCreatedView:
    """Instantiate a published sub-application version as a live session.

    Returns the raw session capability token exactly once, plus a ready-to-forward
    ``renderer.unlock`` envelope for the sandboxed iframe.
    """
    return service(db, context).create_session(payload)


@router.post(
    "/sessions/{session_id}/events",
    response_model=SubAppSessionEventAcceptedView,
    status_code=status.HTTP_202_ACCEPTED,
)
def accept_subapp_session_event(
    session_id: SessionPath,
    payload: SubAppSessionEventRequest,
    db: DB,
    context: CurrentWorkspace,
) -> SubAppSessionEventAcceptedView:
    """Redeem the session token, persist one user event, and rotate the token.

    Deliberately returns 202 without invoking the agent; the host polls
    ``GET /sessions/{id}/states`` (or the next ``renderer.state`` push) for the
    resulting state.
    """
    return service(db, context).accept_session_event(session_id, payload)


@router.get("/sessions/{session_id}", response_model=SubAppSessionView)
def get_subapp_session(
    session_id: SessionPath,
    db: DB,
    context: CurrentWorkspace,
) -> SubAppSessionView:
    return service(db, context).get_session(session_id)


@router.get("/sessions/{session_id}/states", response_model=SubAppStateListView)
def list_subapp_states(
    session_id: SessionPath,
    db: DB,
    context: CurrentWorkspace,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_SUBAPP_STATE_LIST_LIMIT)] = 50,
) -> SubAppStateListView:
    return service(db, context).list_states(session_id, offset=offset, limit=limit)


@router.get("/sessions/{session_id}/states/{version}", response_model=SubAppStateView)
def get_subapp_state(
    session_id: SessionPath,
    version: Annotated[int, Path(ge=1)],
    db: DB,
    context: CurrentWorkspace,
) -> SubAppStateView:
    return service(db, context).get_state(session_id, version)


@router.post("/sessions/{session_id}/pause", response_model=SubAppSessionView)
def pause_subapp_session(
    session_id: SessionPath,
    db: DB,
    context: CurrentWorkspace,
) -> SubAppSessionView:
    return service(db, context).pause_session(session_id)


@router.post("/sessions/{session_id}/terminate", response_model=SubAppSessionView)
def terminate_subapp_session(
    session_id: SessionPath,
    db: DB,
    context: CurrentWorkspace,
) -> SubAppSessionView:
    return service(db, context).terminate_session(session_id)
