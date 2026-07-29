from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.domain.schemas.management import (
    CodexDeviceLoginPollRequest,
    GitHubCopilotDeviceLoginPollRequest,
    GitHubCopilotDeviceLoginPollView,
    GitHubCopilotDeviceLoginStartView,
    CodexDeviceLoginPollView,
    CodexDeviceLoginStartView,
    MasterKeyRotationView,
    ProviderCreateRequest,
    ProviderTypeCatalogView,
    ProviderSecretLifecycleView,
    ProviderSecretRotateRequest,
    ProviderUpdateRequest,
    ProviderView,
    ProviderModelCapabilityUpdateRequest,
    ProviderModelCapabilityView,
    ProviderModelCatalogSyncRequest,
    ProviderModelCatalogSyncView,
    ProviderModelStateUpdateRequest,
    ProviderModelStateView,
    ProviderModelStatesUpdateRequest,
    ProviderModelStatesView,
    ProviderBalanceQueryConfigUpdateRequest,
    ProviderBalanceQueryConfigView,
    ProviderBalanceQueryExecuteRequest,
    ProviderBalanceQueryExecuteView,
    ProviderBalanceQueryResultRequest,
    ProviderBalanceQueryResultView,
    ProviderBalanceView,
    SecretStoreStatusView,
    WorkspaceSecretReferenceUpsertRequest,
    WorkspaceSecretReferenceView,
)
from app.core.errors import AppError
from app.domain.schemas.common import ActionResponse
from app.services.management import ProviderService
from app.services.secret_references import SecretReferenceService


router = APIRouter(prefix="/providers", tags=["providers"])


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> ProviderService:
    return ProviderService(db, context.workspace_id, context.principal.user_id, settings)


def secret_reference_service(
    db: DB, context: CurrentWorkspace, settings: AppSettings
) -> SecretReferenceService:
    return SecretReferenceService(
        db,
        context.workspace_id,
        context.principal.user_id,
        settings,
    )


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


@router.get("/secret-labels", response_model=list[WorkspaceSecretReferenceView])
def list_secret_labels(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[WorkspaceSecretReferenceView]:
    """Return label metadata only; secrets and ciphertext are never serialized."""

    context.require_permission("workspace.manage")
    return [
        WorkspaceSecretReferenceView.model_validate(item)
        for item in secret_reference_service(db, context, settings).list()
    ]


@router.put("/secret-labels/{label}", response_model=WorkspaceSecretReferenceView)
def inject_secret_label(
    label: str,
    payload: WorkspaceSecretReferenceUpsertRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> WorkspaceSecretReferenceView:
    """Trusted UI injection; Agent tools may reference this label but cannot read it."""

    context.require_permission("workspace.manage")
    return WorkspaceSecretReferenceView.model_validate(
        secret_reference_service(db, context, settings).upsert(
            label,
            payload.secret,
            payload.purpose,
        )
    )


@router.get("/model-defaults/{model_id:path}")
def model_defaults(
    model_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    provider_type: str | None = None,
) -> dict:
    return service(db, context, settings).default_model_capabilities(
        model_id,
        provider_type=provider_type,
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


@router.post("/copilot/device-login", response_model=GitHubCopilotDeviceLoginStartView)
def start_copilot_device_login(
    db: DB, context: CurrentWorkspace, settings: AppSettings
) -> GitHubCopilotDeviceLoginStartView:
    if "workspace.manage" not in context.permissions:
        raise AppError(403, "permission_denied", "Permission 'workspace.manage' is required to sign in to GitHub Copilot")
    return GitHubCopilotDeviceLoginStartView.model_validate(
        service(db, context, settings).github_copilot_device_login_start()
    )


@router.post("/copilot/device-login/poll", response_model=GitHubCopilotDeviceLoginPollView)
def poll_copilot_device_login(
    payload: GitHubCopilotDeviceLoginPollRequest,
    db: DB, context: CurrentWorkspace, settings: AppSettings
) -> GitHubCopilotDeviceLoginPollView:
    if "workspace.manage" not in context.permissions:
        raise AppError(403, "permission_denied", "Permission 'workspace.manage' is required to sign in to GitHub Copilot")
    return GitHubCopilotDeviceLoginPollView.model_validate(
        service(db, context, settings).github_copilot_device_login_poll(
            device_auth_id=payload.device_auth_id, user_code=payload.user_code
        )
    )


@router.post("/codex/device-login", response_model=CodexDeviceLoginStartView)
def start_codex_device_login(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> CodexDeviceLoginStartView:
    if "workspace.manage" not in context.permissions:
        raise AppError(
            403,
            "permission_denied",
            "Permission 'workspace.manage' is required to sign in to Codex",
        )
    return CodexDeviceLoginStartView.model_validate(
        service(db, context, settings).codex_device_login_start()
    )


@router.post("/codex/device-login/poll", response_model=CodexDeviceLoginPollView)
def poll_codex_device_login(
    payload: CodexDeviceLoginPollRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> CodexDeviceLoginPollView:
    if "workspace.manage" not in context.permissions:
        raise AppError(
            403,
            "permission_denied",
            "Permission 'workspace.manage' is required to sign in to Codex",
        )
    return CodexDeviceLoginPollView.model_validate(
        service(db, context, settings).codex_device_login_poll(
            device_auth_id=payload.device_auth_id,
            user_code=payload.user_code,
        )
    )


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


def _require_balance_permission(context: CurrentWorkspace) -> None:
    if "workspace.manage" not in context.permissions:
        raise AppError(
            403,
            "permission_denied",
            "Permission 'workspace.manage' is required to query an account balance",
        )


@router.get(
    "/{provider_id}/balance-query",
    response_model=ProviderBalanceQueryConfigView,
)
def get_provider_balance_query_config(
    provider_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderBalanceQueryConfigView:
    _require_balance_permission(context)
    return ProviderBalanceQueryConfigView.model_validate(
        service(db, context, settings).balance_query_config(provider_id)
    )


@router.put(
    "/{provider_id}/balance-query",
    response_model=ProviderBalanceQueryConfigView,
)
def update_provider_balance_query_config(
    provider_id: str,
    payload: ProviderBalanceQueryConfigUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderBalanceQueryConfigView:
    _require_balance_permission(context)
    return ProviderBalanceQueryConfigView.model_validate(
        service(db, context, settings).update_balance_query_config(
            provider_id, payload.config
        )
    )


@router.post(
    "/{provider_id}/balance-query/execute",
    response_model=ProviderBalanceQueryExecuteView,
)
def execute_provider_balance_query(
    provider_id: str,
    payload: ProviderBalanceQueryExecuteRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderBalanceQueryExecuteView:
    _require_balance_permission(context)
    return ProviderBalanceQueryExecuteView.model_validate(
        service(db, context, settings).execute_balance_query(provider_id, payload)
    )


@router.put(
    "/{provider_id}/balance-query/result",
    response_model=ProviderBalanceQueryResultView,
)
def save_provider_balance_query_result(
    provider_id: str,
    payload: ProviderBalanceQueryResultRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderBalanceQueryResultView:
    _require_balance_permission(context)
    return ProviderBalanceQueryResultView.model_validate(
        service(db, context, settings).save_balance_query_result(
            provider_id, payload
        )
    )


@router.get(
    "/{provider_id}/model-capabilities",
    response_model=ProviderModelCapabilityView,
)
def get_model_capabilities_by_query(
    provider_id: str,
    model_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderModelCapabilityView:
    """Read capabilities without placing slash-bearing model IDs in the URL path."""
    return ProviderModelCapabilityView.model_validate(
        service(db, context, settings).model_capabilities(provider_id, model_id)
    )


@router.put(
    "/{provider_id}/model-capabilities",
    response_model=ProviderModelCapabilityView,
)
def update_model_capabilities_by_query(
    provider_id: str,
    model_id: str,
    payload: ProviderModelCapabilityUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderModelCapabilityView:
    """Update capabilities without placing slash-bearing model IDs in the URL path."""
    return ProviderModelCapabilityView.model_validate(
        service(db, context, settings).update_model_capabilities(
            provider_id, model_id, payload
        )
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


@router.post(
    "/{provider_id}/models/sync-catalog-defaults",
    response_model=ProviderModelCatalogSyncView,
)
def sync_model_catalog_defaults(
    provider_id: str,
    payload: ProviderModelCatalogSyncRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ProviderModelCatalogSyncView:
    return ProviderModelCatalogSyncView.model_validate(
        service(db, context, settings).sync_model_catalog_defaults(
            provider_id, payload.model_ids
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
