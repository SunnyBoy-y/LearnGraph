from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.schemas.management import (
    MasterKeyRotationView,
    ProviderCreateRequest,
    ProviderTypeCatalogView,
    ProviderSecretLifecycleView,
    ProviderSecretRotateRequest,
    ProviderUpdateRequest,
    ProviderView,
    ProviderModelCapabilityUpdateRequest,
    ProviderModelCapabilityView,
    ProviderModelStateUpdateRequest,
    ProviderModelStateView,
    ProviderModelStatesUpdateRequest,
    ProviderModelStatesView,
    ProviderBalanceView,
    SecretStoreStatusView,
)
from app.core.errors import AppError
from app.domain.schemas.common import ActionResponse
from app.services.management import ProviderService


router = APIRouter(prefix="/providers", tags=["providers"])


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> ProviderService:
    return ProviderService(db, context.workspace_id, context.principal.user_id, settings)


@router.get("", response_model=list[ProviderView])
def list_providers(db: DB, context: CurrentWorkspace, settings: AppSettings) -> list[ProviderView]:
    provider_service = service(db, context, settings)
    secret_metadata = provider_service.secret_metadata()
    return [
        ProviderView.model_validate(item).model_copy(
            update=secret_metadata.get(item.id, {})
        )
        for item in provider_service.list()
    ]


@router.post("", response_model=ProviderView, status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: ProviderCreateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderView:
    return ProviderView.model_validate(service(db, context, settings).create(payload))


@router.get("/catalog", response_model=list[ProviderTypeCatalogView])
def provider_catalog(
    db: DB, context: CurrentWorkspace, settings: AppSettings
) -> list[ProviderTypeCatalogView]:
    return [
        ProviderTypeCatalogView.model_validate(item)
        for item in service(db, context, settings).catalog()
    ]


@router.get("/secret-store/status", response_model=SecretStoreStatusView)
def secret_store_status(
    db: DB, context: CurrentWorkspace, settings: AppSettings
) -> SecretStoreStatusView:
    return SecretStoreStatusView.model_validate(
        service(db, context, settings).secret_store_status()
    )


@router.post("/secret-store/rotate-master-key", response_model=MasterKeyRotationView)
def rotate_master_key(
    db: DB, context: CurrentWorkspace, settings: AppSettings
) -> MasterKeyRotationView:
    return MasterKeyRotationView.model_validate(
        service(db, context, settings).rotate_master_key()
    )


@router.post("/{provider_id}/rotate-secret", response_model=ProviderSecretLifecycleView)
def rotate_provider_secret(
    provider_id: str,
    payload: ProviderSecretRotateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderSecretLifecycleView:
    return ProviderSecretLifecycleView.model_validate(
        service(db, context, settings).rotate_secret(provider_id, payload)
    )


@router.get("/{provider_id}/models")
def discover_models(provider_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> dict:
    return service(db, context, settings).models(provider_id)


@router.get("/{provider_id}/balance", response_model=ProviderBalanceView)
def get_provider_balance(
    provider_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderBalanceView:
    if "workspace.manage" not in context.permissions:
        raise AppError(
            403,
            "permission_denied",
            "Permission 'workspace.manage' is required to query an account balance",
        )
    return ProviderBalanceView.model_validate(
        service(db, context, settings).balance(provider_id)
    )


@router.get(
    "/{provider_id}/models/{model_id}/capabilities",
    response_model=ProviderModelCapabilityView,
)
def get_model_capabilities(
    provider_id: str,
    model_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderModelCapabilityView:
    return ProviderModelCapabilityView.model_validate(
        service(db, context, settings).model_capabilities(provider_id, model_id)
    )


@router.put(
    "/{provider_id}/models/{model_id}/capabilities",
    response_model=ProviderModelCapabilityView,
)
def update_model_capabilities(
    provider_id: str,
    model_id: str,
    payload: ProviderModelCapabilityUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderModelCapabilityView:
    return ProviderModelCapabilityView.model_validate(
        service(db, context, settings).update_model_capabilities(
            provider_id, model_id, payload
        )
    )


@router.put(
    "/{provider_id}/models/capabilities",
    response_model=ProviderModelCapabilityView,
)
def update_model_group_capabilities(
    provider_id: str,
    payload: ProviderModelCapabilityUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderModelCapabilityView:
    return ProviderModelCapabilityView.model_validate(
        service(db, context, settings).update_model_group_capabilities(
            provider_id, payload
        )
    )


@router.patch(
    "/{provider_id}/models",
    response_model=ProviderModelStatesView,
)
def update_model_states(
    provider_id: str,
    payload: ProviderModelStatesUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderModelStatesView:
    return ProviderModelStatesView.model_validate(
        service(db, context, settings).update_model_states(
            provider_id, payload.states
        )
    )


@router.patch(
    "/{provider_id}/models/{model_id}",
    response_model=ProviderModelStateView,
)
def update_model_state(
    provider_id: str,
    model_id: str,
    payload: ProviderModelStateUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderModelStateView:
    return ProviderModelStateView.model_validate(
        service(db, context, settings).update_model_state(
            provider_id, model_id, payload.enabled
        )
    )


@router.post("/{provider_id}/probe", response_model=ProviderView)
def probe_provider(provider_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> ProviderView:
    return ProviderView.model_validate(service(db, context, settings).probe(provider_id))


@router.patch("/{provider_id}", response_model=ProviderView)
def update_provider(
    provider_id: str,
    payload: ProviderUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderView:
    return ProviderView.model_validate(service(db, context, settings).update(provider_id, payload))


@router.delete("/{provider_id}", response_model=ActionResponse)
def delete_provider(
    provider_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ActionResponse:
    result = service(db, context, settings).delete(provider_id)
    return ActionResponse(
        status=result["status"],
        message="Provider instance deleted",
        resource_id=result["resource_id"],
    )
