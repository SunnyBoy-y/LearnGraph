from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.schemas.common import ORMModel
from app.domain.schemas.management import PluginView


SEMVER_PATTERN = (
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
COMPONENT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{1,119}$"
DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)


class ComponentPermissions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    network_domains: list[str] = Field(default_factory=list, max_length=32)
    file_read: bool = False
    clipboard_write: bool = False
    message_actions: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("network_domains")
    @classmethod
    def validate_domains(cls, domains: list[str]) -> list[str]:
        normalized: list[str] = []
        for domain in domains:
            candidate = domain.strip().lower()
            if candidate == "*" or "://" in candidate or not DOMAIN_PATTERN.fullmatch(candidate):
                raise ValueError("network_domains must contain exact DNS hostnames without schemes or wildcards")
            if candidate not in normalized:
                normalized.append(candidate)
        return normalized

    @field_validator("message_actions")
    @classmethod
    def validate_actions(cls, actions: list[str]) -> list[str]:
        normalized: list[str] = []
        for action in actions:
            candidate = action.strip()
            if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", candidate):
                raise ValueError("message_actions must be bounded lowercase action identifiers")
            if candidate not in normalized:
                normalized.append(candidate)
        return normalized


class ComponentSizeLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_height: int = Field(default=80, ge=40, le=2_000)
    max_height: int = Field(default=720, ge=40, le=2_000)

    @model_validator(mode="after")
    def validate_range(self) -> "ComponentSizeLimits":
        if self.min_height > self.max_height:
            raise ValueError("min_height cannot exceed max_height")
        return self


class ComponentSignatureDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["ed25519", "ecdsa-p256-sha256"]
    key_id: str = Field(min_length=1, max_length=160)
    signature_base64: str = Field(min_length=16, max_length=16_384)


class ComponentInteractionContract(BaseModel):
    """Schemas for the isolated subapp event and server-authored state channels."""

    model_config = ConfigDict(extra="forbid")

    event_schema: dict[str, Any]
    state_schema: dict[str, Any]


class ComponentManifestImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str = Field(pattern=COMPONENT_ID_PATTERN)
    version: str = Field(pattern=SEMVER_PATTERN, max_length=40)
    display_name: str = Field(min_length=1, max_length=160)
    renderer: Literal["sandbox", "trusted-bundle", "trusted-bundle-or-sandbox"] = "sandbox"
    author: str = Field(default="", max_length=160)
    source: str = Field(min_length=1, max_length=160)
    package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature: ComponentSignatureDeclaration | None = None
    compatible_learngraph: dict[str, Any] = Field(default_factory=dict)
    uninstall_behavior: Literal["retain_data", "delete_plugin_data"] = "retain_data"
    data_schema: dict[str, Any]
    event_schema: dict[str, Any]
    interaction_contract: ComponentInteractionContract | None = None
    permissions: ComponentPermissions = Field(default_factory=ComponentPermissions)
    size_limits: ComponentSizeLimits = Field(default_factory=ComponentSizeLimits)
    skill_triggers: list[str] = Field(default_factory=list, max_length=64)
    example_data: dict[str, Any] = Field(default_factory=dict)


class ComponentManifestVersionView(ORMModel):
    id: str
    workspace_id: str
    plugin_id: str
    component_id: str
    version: str
    display_name: str
    renderer: str
    source: str
    author: str
    package_hash: str
    package_hash_status: str
    signature_status: str
    signature_info: dict[str, Any]
    compatible_learngraph: dict[str, Any]
    uninstall_behavior: str
    data_schema: dict[str, Any]
    event_schema: dict[str, Any]
    interaction_contract: ComponentInteractionContract | None = None
    permissions: dict[str, Any]
    size_limits: dict[str, Any]
    skill_triggers: list[str]
    example_data: dict[str, Any]
    schema_hash: str
    permissions_hash: str
    manifest_hash: str
    issuer_id: str | None = None
    trusted_bundle_eligible: bool = False
    created_at: datetime


class ComponentCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version_id: str = Field(min_length=1, max_length=36)
    check_type: Literal["health", "render"]
    sample_data: dict[str, Any] | None = None


class ComponentCheckRecordView(ORMModel):
    id: str
    workspace_id: str
    plugin_id: str
    manifest_version_id: str
    check_type: str
    status: str
    executor: str
    runtime_executed: bool
    details: dict[str, Any]
    artifact_metadata: dict[str, Any]
    checked_by: str
    checked_at: datetime
    created_at: datetime


class ComponentRegistrationView(BaseModel):
    plugin: PluginView
    manifest: ComponentManifestVersionView
    reauthorization_required: bool
    reauthorization_reasons: list[str]
    checks: list[ComponentCheckRecordView]


class ComponentAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version_id: str = Field(min_length=1, max_length=36)
    scope: Literal["current_workspace"] = "current_workspace"


class ComponentAuthorizationRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="user_revoked", min_length=1, max_length=240)


class ComponentAuthorizationView(ORMModel):
    id: str
    workspace_id: str
    plugin_id: str
    manifest_version_id: str
    scope: str
    status: str
    manifest_hash: str
    permissions_hash: str
    authorized_by: str
    authorized_at: datetime
    revoked_by: str | None
    revoked_at: datetime | None
    revoke_reason: str


class ComponentArtifactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version_id: str = Field(min_length=1, max_length=36)
    data: dict[str, Any]


class ComponentArtifactView(BaseModel):
    delivery_mode: Literal["trusted_component", "sandbox_artifact"]
    component_id: str
    version: str
    manifest_version_id: str
    authorization_id: str
    runtime_status: str
    sandbox_executed: bool
    trusted_component: dict[str, Any] | None = None
    sandbox_artifact: dict[str, Any] | None = None
    # Trusted renderer channel decision for third-party components. ``delivery_mode``
    # stays ``sandbox_artifact`` for anything not fully eligible; these fields surface
    # the eligibility decision and the sealed envelope lives in ``sandbox_artifact``.
    trusted_renderer_eligible: bool = False
    trusted_renderer_reason: str | None = None


class ComponentEventValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version_id: str = Field(min_length=1, max_length=36)
    event: dict[str, Any]


class ComponentEventValidationView(BaseModel):
    accepted: bool
    component_id: str
    version: str
    event_hash: str
    executed: bool = False
