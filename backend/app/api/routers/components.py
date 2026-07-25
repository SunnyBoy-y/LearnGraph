from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentWorkspace, DB
from app.domain.schemas.components import (
    ComponentArtifactRequest,
    ComponentArtifactView,
    ComponentAuthorizationRequest,
    ComponentAuthorizationRevokeRequest,
    ComponentAuthorizationView,
    ComponentCheckRecordView,
    ComponentCheckRequest,
    ComponentEventValidationRequest,
    ComponentEventValidationView,
    ComponentManifestImportRequest,
    ComponentManifestVersionView,
    ComponentRegistrationView,
)
from app.domain.schemas.management import PluginView
from app.services.components import ComponentService


router = APIRouter(prefix="/plugins/components", tags=["trusted-components"])


def service(db: DB, context: CurrentWorkspace) -> ComponentService:
    return ComponentService(db, context.workspace_id, context.principal.user_id)


@router.post("", response_model=ComponentRegistrationView, status_code=status.HTTP_201_CREATED)
def register_component(
    payload: ComponentManifestImportRequest,
    db: DB,
    context: CurrentWorkspace,
) -> ComponentRegistrationView:
    plugin, manifest, required, reasons, checks = service(db, context).register(payload)
    return ComponentRegistrationView(
        plugin=PluginView.model_validate(plugin),
        manifest=ComponentManifestVersionView.model_validate(manifest),
        reauthorization_required=required,
        reauthorization_reasons=reasons,
        checks=[ComponentCheckRecordView.model_validate(item) for item in checks],
    )


@router.get("/{plugin_id}/manifests", response_model=list[ComponentManifestVersionView])
def list_component_manifests(
    plugin_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> list[ComponentManifestVersionView]:
    return [
        ComponentManifestVersionView.model_validate(item)
        for item in service(db, context).list_manifests(plugin_id)
    ]


@router.get("/{plugin_id}/authorizations", response_model=list[ComponentAuthorizationView])
def list_component_authorizations(
    plugin_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> list[ComponentAuthorizationView]:
    return [
        ComponentAuthorizationView.model_validate(item)
        for item in service(db, context).list_authorizations(plugin_id)
    ]


@router.post("/{plugin_id}/authorizations", response_model=ComponentAuthorizationView)
def authorize_component(
    plugin_id: str,
    payload: ComponentAuthorizationRequest,
    db: DB,
    context: CurrentWorkspace,
) -> ComponentAuthorizationView:
    return ComponentAuthorizationView.model_validate(
        service(db, context).authorize(plugin_id, payload)
    )


@router.post(
    "/{plugin_id}/authorizations/revoke",
    response_model=ComponentAuthorizationView,
)
def revoke_component_authorization(
    plugin_id: str,
    payload: ComponentAuthorizationRevokeRequest,
    db: DB,
    context: CurrentWorkspace,
) -> ComponentAuthorizationView:
    return ComponentAuthorizationView.model_validate(
        service(db, context).revoke(plugin_id, payload)
    )


@router.get("/{plugin_id}/checks", response_model=list[ComponentCheckRecordView])
def list_component_checks(
    plugin_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> list[ComponentCheckRecordView]:
    return [
        ComponentCheckRecordView.model_validate(item)
        for item in service(db, context).list_checks(plugin_id)
    ]


@router.post(
    "/{plugin_id}/checks",
    response_model=ComponentCheckRecordView,
    status_code=status.HTTP_201_CREATED,
)
def run_component_check(
    plugin_id: str,
    payload: ComponentCheckRequest,
    db: DB,
    context: CurrentWorkspace,
) -> ComponentCheckRecordView:
    return ComponentCheckRecordView.model_validate(
        service(db, context).run_check(plugin_id, payload)
    )


@router.post(
    "/{plugin_id}/artifacts",
    response_model=ComponentArtifactView,
    status_code=status.HTTP_201_CREATED,
)
def prepare_component_artifact(
    plugin_id: str,
    payload: ComponentArtifactRequest,
    db: DB,
    context: CurrentWorkspace,
) -> ComponentArtifactView:
    return service(db, context).create_artifact(plugin_id, payload)


@router.post(
    "/{plugin_id}/events/validate",
    response_model=ComponentEventValidationView,
)
def validate_component_event(
    plugin_id: str,
    payload: ComponentEventValidationRequest,
    db: DB,
    context: CurrentWorkspace,
) -> ComponentEventValidationView:
    return service(db, context).validate_event(plugin_id, payload)

