from __future__ import annotations

import logging
from datetime import datetime
import json
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.core.config import Settings
from app.core.database import retry_sqlite_locked
from app.core.errors import AppError
from app.core.secret_store import SecretStoreUnavailable, secret_store_from_settings
from app.core.security import SecretCipher, mask_secret
from app.providers.provider_plan_cache import invalidate_provider_plan_cache
from app.domain.models import (
    AuditEvent,
    MigrationJob,
    PluginRecord,
    ProviderConfig,
    ProviderSecret,
    UsageEvent,
    Workspace,
    WorkspaceSetting,
    utc_now,
)
from app.domain.settings import (
    CHAT_AUTO_TITLE_MODEL_SETTING_KEY,
    CHAT_CONTEXT_USAGE_SETTING_KEY,
    CHAT_DEFAULT_RESPONSE_MODE_SETTING_KEY,
    CHAT_DICTATION_CLEANUP_MODEL_SETTING_KEY,
    CHAT_DICTATION_CLEANUP_SETTING_KEY,
    CHAT_RESPONSE_STYLE_SETTING_KEY,
    CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY,
    CHAT_SUGGESTED_PROMPTS_SETTING_KEY,
    FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY,
)
from app.domain.schemas.management import (
    AccessAllowlistSettingValue,
    ChatContextUsageSettingValue,
    ChatDefaultResponseModeSettingValue,
    ChatDictationCleanupSettingValue,
    ChatFeatureModelSettingValue,
    ChatResponseStyleSettingValue,
    ChatSuggestedPromptsSettingValue,
    FunctionalModelDefaultsSettingValue,
    ResearchPolicySettingValue,
    WebFetchPolicySettingValue,
    MigrationPreflightRequest,
    PluginToggleRequest,
    ProviderBalanceQueryConfig,
    ProviderBalanceQueryExecuteRequest,
    ProviderBalanceQueryResultRequest,
    ProviderCreateRequest,
    ProviderSecretRotateRequest,
    ProviderUpdateRequest,
    ProviderModelCapabilityUpdateRequest,
    SettingUpdateRequest,
    UsageSummary,
)
from app.repositories.audit import AuditRepository
from app.repositories.domain import (
    MigrationRepository,
    PluginRepository,
    ProviderRepository,
    SettingRepository,
    UsageRepository,
)
from app.providers.catalog import (
    DEEP_RESEARCH_PROVIDER_TYPES,
    EMBEDDING_PROVIDER_TYPES,
    FETCH_PROVIDER_TYPES,
    IMAGE_GENERATION_PROVIDER_TYPES,
    IMAGE_SEARCH_PROVIDER_TYPES,
    MEMORY_PROVIDER_TYPES,
    MODEL_PROVIDER_TYPES,
    REST_IMAGE_SEARCH_PROVIDER_TYPES,
    SEARCH_PROVIDER_TYPES,
    TRANSCRIPTION_PROVIDER_TYPES,
    VISION_PROVIDER_TYPES,
    provider_catalog,
    provider_type_spec,
)
from app.providers.model_catalog import unified_model_defaults
from app.providers.remote.fetch import (
    Crawl4AIHTTPFetchProvider,
    FetchProviderError,
    FetchProviderTimeout,
    FirecrawlFetchProvider,
)
from app.providers.remote.openai import (
    ProviderHTTPError,
    ProviderInvalidUrlError,
    ProviderResponseError,
    ProviderTimeoutError,
    discover_remote_models,
)
from app.providers.remote.deepseek import (
    DeepSeekBalanceError,
    fetch_deepseek_balance,
    is_deepseek_chat_configuration,
    is_official_deepseek_api_base_url,
)
from app.providers.remote.copilot import (
    ProviderHTTPError as CopilotProviderHTTPError,
    ProviderResponseError as CopilotProviderResponseError,
    ProviderTimeoutError as CopilotProviderTimeoutError,
    discover_copilot_models,
    poll_copilot_device_login,
    start_copilot_device_login,
)
from app.providers.remote.codex import (
    CODEX_DEFAULT_MODEL,
    CODEX_KNOWN_MODELS,
    CODEX_UNSUPPORTED_CHATGPT_MODELS,
    CodexAuthError,
    ensure_fresh_codex_credentials,
    fetch_codex_usage,
    parse_codex_credentials,
    poll_codex_device_login,
    start_codex_device_login,
)
from app.providers.remote.research_vendors import QwenDeepResearchProvider
from app.providers.remote.custom_balance import (
    API_KEY_PLACEHOLDER as CUSTOM_BALANCE_API_KEY_PLACEHOLDER,
    BASE_URL_PLACEHOLDER as CUSTOM_BALANCE_BASE_URL_PLACEHOLDER,
    CustomBalanceQueryError,
    execute_custom_balance_request,
)
from app.providers.remote.balance import (
    BalanceInfo,
    BalanceReport,
    ProviderBalanceError,
    detect_balance_vendor,
    fetch_dashscope_balance,
    fetch_gateway_billing_balance,
    fetch_moonshot_balance,
    fetch_openrouter_balance,
    fetch_siliconflow_balance,
    official_no_balance_notice,
    supports_gateway_billing,
)
from app.providers.remote.memory import Mem0PlatformAdapter, mem0_entity_id
from app.providers.remote.anysearch import AnySearchSearchProvider
from app.providers.remote.research import (
    DeepResearchProviderError,
    DeepResearchProviderTimeout,
    HTTPDeepResearchProvider,
)
from app.providers.remote.search import (
    CloudSearchProvider,
    SearchProviderError,
    SearchProviderResponseError,
    SearchProviderTimeout,
    SearXNGSearchProvider,
)
from app.providers.model_options import (
    ModelCapabilityError,
    catalog_capability_snapshot,
    model_capabilities_for_model,
    validate_model_capability_update,
)
from app.providers.qwen_catalog import (
    PROTOCOL_FAMILY_OPENAI_COMPATIBLE,
    protocol_family_for,
)
from app.providers.model_catalog import unified_model_defaults
from app.services.provider_secrets import (
    PROVIDER_SECRET_ALGORITHM,
    ProviderSecretUnavailable,
    decrypt_provider_secret,
    encrypt_provider_secret,
)

# Roles whose models are manageable through the discovery snapshot.  This is
# exactly the set ``models()`` supports, so model enablement, capability
# snapshots, catalog sync, and default-model fallbacks stay coherent for
# image / vision / ASR / embedding / deep-research Providers — not just
# chat-model Providers.
_MODEL_MANAGEMENT_PROVIDER_TYPES = frozenset(
    {
        *MODEL_PROVIDER_TYPES,
        *IMAGE_GENERATION_PROVIDER_TYPES,
        *IMAGE_SEARCH_PROVIDER_TYPES,
        *VISION_PROVIDER_TYPES,
        *TRANSCRIPTION_PROVIDER_TYPES,
        *EMBEDDING_PROVIDER_TYPES,
        "qwen_deep_research",
    }
)

# DashScope official hosts.  API keys cannot read account balance there; a
# workspace-configured Aliyun AccessKey (secret reference, purpose
# ``aliyun_access_key``) is required for the BSS RPC.
_DASHSCOPE_BALANCE_HOSTS = frozenset(
    {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}
)


def _is_dashscope_host(base_url: str | None) -> bool:
    if not base_url:
        return False
    try:
        host = (urlsplit(base_url.strip()).hostname or "").casefold()
    except ValueError:
        return False
    return host in _DASHSCOPE_BALANCE_HOSTS or host.endswith(".maas.aliyuncs.com")


class ProviderService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str, settings: Settings) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.providers = ProviderRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)

    def list(self) -> list[ProviderConfig]:
        providers = list(self.providers.list())

        def priority(provider: ProviderConfig) -> int:
            raw = (provider.capabilities or {}).get("provider_priority", 0)
            try:
                return max(0, int(raw))
            except (TypeError, ValueError):
                return 0

        return sorted(
            providers,
            key=lambda provider: (-priority(provider), -provider.created_at.timestamp()),
        )

    def catalog(self) -> list[dict[str, object]]:
        return provider_catalog()

    def secret_metadata(self) -> dict[str, dict]:
        records = self.db.scalars(
            select(ProviderSecret).where(
                ProviderSecret.workspace_id == self.workspace_id
            )
        )
        return {
            record.provider_id: {
                "secret_status": (
                    "revoked"
                    if record.revoked_at is not None or not record.ciphertext
                    else "active"
                ),
                "secret_version": record.secret_version,
                "secret_key_provider": record.key_provider,
                "secret_key_version": record.key_version,
            }
            for record in records
        }

    def create(self, payload: ProviderCreateRequest) -> ProviderConfig:
        spec = provider_type_spec(payload.provider_type)
        if spec is None or not spec.create_allowed:
            raise AppError(
                422,
                "unsupported_provider_type",
                "This Provider type cannot be created by the current backend",
                {"provider_type": payload.provider_type},
            )
        existing = self.db.scalar(
            self.providers.query().where(ProviderConfig.display_name == payload.display_name)
        )
        if existing is not None:
            raise AppError(409, "provider_name_conflict", "Provider display name already exists")
        secret = payload.api_key.get_secret_value() if payload.api_key else None
        encrypted = None
        if secret:
            try:
                encrypted = encrypt_provider_secret(self.settings, secret)
            except (SecretStoreUnavailable, ValueError) as exc:
                raise AppError(
                    503,
                    "secret_store_unavailable",
                    "Provider secrets are rejected because the configured secret store is unavailable",
                ) from exc
        masked: str | None = None
        fingerprint: str | None = None
        if secret:
            masked, fingerprint = mask_secret(secret)
        deepseek_defaults: dict[str, object] = {}
        if payload.provider_type == "deepseek_chat" or (
            payload.provider_type == "openai_compatible_chat"
            and (
                (payload.capabilities or {}).get("model_family") == "deepseek"
                or (payload.capabilities or {}).get("brand_id") == "deepseek"
            )
        ):
            deepseek_defaults = {
                "reasoning_efforts": ["low", "medium", "high", "xhigh"],
                "thinking_mapping": {
                    "off": None,
                    "low": "high",
                    "medium": "high",
                    "high": "high",
                    "xhigh": "max",
                },
                "default_thinking_mode": "off",
                "reasoning_parameter": "reasoning_effort",
                "hosted_web_search": False,
                "default_search_route": "auto",
                "capability_source": "official_catalog",
                "supports_agent_tools": True,
                "model_family": "deepseek",
                "brand_id": "deepseek",
            }
        anthropic_defaults: dict[str, object] = {}
        if payload.provider_type == "anthropic_messages":
            anthropic_defaults = {
                "reasoning_efforts": ["low", "medium", "high", "xhigh"],
                "thinking_mapping": {
                    "off": None,
                    "low": "low",
                    "medium": "medium",
                    "high": "high",
                    "xhigh": "high",
                },
                "default_thinking_mode": "off",
                "reasoning_parameter": "reasoning_effort",
                "hosted_web_search": False,
                "default_search_route": "auto",
                "capability_source": "official_catalog",
                "supports_agent_tools": True,
                "model_family": "anthropic",
                "brand_id": "anthropic",
            }
        qwen_defaults: dict[str, object] = {}
        if payload.provider_type == "qwen":
            qwen_defaults = {
                "provider_family": "qwen",
                "brand_id": "qwen",
                "structured_output_mode": "json_object",
                "supports_agent_tools": True,
                "capability_source": "official_catalog",
                # Per-model hosted tools and modalities are filled by the
                # unified defaults interface after discovery.
                "companion_capabilities": [
                    "web_search",
                    "web_fetch",
                    "image_search",
                    "image_understanding",
                    "video_understanding",
                ],
            }
        codex_defaults: dict[str, object] = {}
        if payload.provider_type == "codex_chatgpt":
            codex_defaults = {
                "brand_id": "openai",
                "model_family": "codex",
                "capability_source": "official_catalog",
                "supports_agent_tools": True,
                "discovered_model_ids": list(CODEX_KNOWN_MODELS),
                "discovered_model_count": len(CODEX_KNOWN_MODELS),
                "model_states": {model_id: True for model_id in CODEX_KNOWN_MODELS},
                "default_model": CODEX_DEFAULT_MODEL,
            }
        ollama_defaults: dict[str, object] = {}
        if payload.provider_type == "ollama":
            ollama_defaults = {
                "brand_id": "ollama",
                "model_family": "ollama",
                "provider_family": "ollama",
                "capability_source": "official_catalog",
                "supports_agent_tools": True,
                "structured_output_mode": "json_object",
                # Thinking models (DeepSeek-R1, Qwen3, GPT-OSS, …) can opt in via
                # the capability editor; default keeps fast mode available.
                "reasoning_efforts": ["low", "medium", "high", "xhigh"],
                "thinking_mapping": {
                    "off": False,
                    "low": "low",
                    "medium": "medium",
                    "high": "high",
                    "xhigh": "max",
                },
                "default_thinking_mode": "off",
                "reasoning_parameter": "thinking",
                "hosted_web_search": False,
                "default_search_route": "external",
            }
        if payload.provider_type == "ollama_embedding":
            ollama_defaults = {
                "brand_id": "ollama",
                "model_family": "ollama",
                "provider_family": "ollama",
                "capability_source": "official_catalog",
                "provider_role": "embedding",
            }
        if payload.provider_type == "ollama_cloud":
            ollama_defaults = {
                "brand_id": "ollama",
                "model_family": "ollama",
                "provider_family": "ollama",
                "capability_source": "official_catalog",
                "supports_agent_tools": True,
                # Ollama Cloud currently does not support structured outputs.
                "supports_structured_output": False,
                "reasoning_efforts": ["low", "medium", "high", "xhigh"],
                "thinking_mapping": {
                    "off": False,
                    "low": "low",
                    "medium": "medium",
                    "high": "high",
                    "xhigh": "max",
                },
                "default_thinking_mode": "off",
                "reasoning_parameter": "thinking",
                "hosted_web_search": False,
                "default_search_route": "external",
            }
        copilot_defaults: dict[str, object] = {}
        if payload.provider_type == "github_copilot":
            copilot_defaults = {
                "brand_id": "github",
                "model_family": "github_copilot",
                "provider_family": "github_copilot",
                "capability_source": "official_catalog",
                "supports_agent_tools": True,
                "structured_output_mode": "json_schema",
                "reasoning_efforts": ["low", "medium", "high", "xhigh"],
                "thinking_mapping": {"off": None, "low": "low", "medium": "medium", "high": "high", "xhigh": "high"},
                "default_thinking_mode": "off",
                "reasoning_parameter": "reasoning_effort",
                "hosted_web_search": False,
                "default_search_route": "external",
            }
        qwen_research_defaults: dict[str, object] = {}
        if payload.provider_type == "qwen_deep_research":
            known = list(QwenDeepResearchProvider.KNOWN_MODELS)
            qwen_research_defaults = {
                "brand_id": "qwen",
                "capability_source": "official_catalog",
                "discovered_model_ids": known,
                "discovered_model_count": len(known),
                "model_states": {model_id: True for model_id in known},
                "default_model": QwenDeepResearchProvider.DEFAULT_MODEL,
                "deep_research_model": QwenDeepResearchProvider.DEFAULT_MODEL,
            }
        # Sanitize optional custom headers used by proxy / relay stations.
        incoming_capabilities = dict(payload.capabilities or {})
        if "extra_headers" in incoming_capabilities:
            incoming_capabilities["extra_headers"] = self._sanitize_extra_headers(
                incoming_capabilities.get("extra_headers")
            )
        capabilities = {
            **deepseek_defaults,
            **anthropic_defaults,
            **qwen_defaults,
            **codex_defaults,
            **ollama_defaults,
            **copilot_defaults,
            **qwen_research_defaults,
            **incoming_capabilities,
            "provider_role": spec.role,
            "declaration_status": "unverified_user_input",
            "remote_calls_enabled": False,
        }
        # All currently supported model protocols implement the structured
        # function-call loop used by Agent mode.  Persist the declaration for
        # the client as well; otherwise generic OpenAI-compatible providers
        # depend on an implicit missing-key fallback and can be incorrectly
        # presented as unavailable by consumers of the provider API.
        if payload.provider_type in MODEL_PROVIDER_TYPES:
            capabilities.setdefault("supports_agent_tools", True)
        # Local providers (Ollama) are fully configured with only a base URL.
        configured_without_secret = (
            not secret
            and not spec.requires_secret
            and bool(payload.base_url)
        )
        # 免费且无需 Key / Base URL 的供应商（如 Openverse 匿名文搜图）创建即启用，
        # 用户可随时在 Provider 管理里关闭。
        auto_enable_keyless_free = (
            spec.is_free and not spec.requires_secret and not spec.requires_base_url
        )
        if auto_enable_keyless_free:
            capabilities["remote_calls_enabled"] = True
        provider = self.providers.add(
            ProviderConfig(
                workspace_id=self.workspace_id,
                display_name=payload.display_name,
                provider_type=payload.provider_type,
                base_url=payload.base_url,
                api_key_masked=masked,
                secret_fingerprint=fingerprint,
                enabled=auto_enable_keyless_free,
                remote_capability=auto_enable_keyless_free,
                capabilities=capabilities,
                status=(
                    "enabled_unverified"
                    if auto_enable_keyless_free
                    else (
                        "configured_disabled"
                        if secret or configured_without_secret
                        else "unconfigured"
                    )
                ),
            )
        )
        self.db.flush()
        if secret and encrypted is not None:
            self.db.add(
                ProviderSecret(
                    workspace_id=self.workspace_id,
                    provider_id=provider.id,
                    ciphertext=encrypted.ciphertext,
                    algorithm=encrypted.algorithm,
                    key_provider=encrypted.key_provider,
                    key_version=encrypted.key_version,
                    secret_version=1,
                )
            )
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.create_metadata",
            resource_type="provider",
            resource_id=provider.id,
            details={
                "provider_type": provider.provider_type,
                "secret_received": bool(secret),
                "secret_persisted_encrypted": bool(secret),
            },
        )
        self.db.commit()
        self.db.refresh(provider)
        if provider.provider_type in _MODEL_MANAGEMENT_PROVIDER_TYPES:
            known_model_ids = [
                str(item).strip()
                for item in (provider.capabilities or {}).get("discovered_model_ids")
                or []
                if str(item).strip()
            ]
            if known_model_ids:
                self.sync_model_catalog_defaults(provider.id, known_model_ids)
                provider = self.providers.require(provider.id, "provider")
                self.db.refresh(provider)
        if (
            secret
            and spec.supports_probe
            and provider.provider_type in MODEL_PROVIDER_TYPES
        ):
            self._probe_after_configuration(provider.id)
            provider = self.providers.require(provider.id, "provider")
            self.db.refresh(provider)
        elif (
            not secret
            and spec.supports_probe
            and provider.provider_type == "ollama"
            and provider.base_url
        ):
            # Local Ollama needs no API key; still auto-probe so discovery runs.
            self._probe_after_configuration(provider.id)
            provider = self.providers.require(provider.id, "provider")
            self.db.refresh(provider)
        return provider

    def default_model_capabilities(
        self,
        model_id: str,
        *,
        provider_type: str | None = None,
    ) -> dict:
        """Expose the same defaults interface used by request resolution."""

        return {
            "model_id": model_id,
            "provider_type": provider_type,
            "capabilities": unified_model_defaults(
                model_id,
                provider_type=provider_type,
            ),
        }

    def secret_store_status(self) -> dict:
        store = secret_store_from_settings(self.settings)
        try:
            current = store.status()
            return {
                "provider": current.provider,
                "available": current.available,
                "secure_backend": current.secure_backend,
                "backend_name": current.backend_name,
                "active_key_version": current.active_key_version,
            }
        except SecretStoreUnavailable:
            return {
                "provider": self.settings.secret_provider,
                "available": False,
                "secure_backend": False,
                "backend_name": "unavailable",
                "active_key_version": None,
            }

    def rotate_secret(
        self, provider_id: str, payload: ProviderSecretRotateRequest
    ) -> dict:
        provider = self.providers.require(provider_id, "provider")
        plaintext = payload.api_key.get_secret_value()
        try:
            encrypted = encrypt_provider_secret(self.settings, plaintext)
        except (SecretStoreUnavailable, ValueError) as exc:
            raise AppError(
                503,
                "secret_store_unavailable",
                "The Provider secret cannot be rotated because the configured secret store is unavailable",
            ) from exc
        masked, fingerprint = mask_secret(plaintext)
        record = self._secret_record(provider.id)
        now = utc_now()
        if record is None:
            record = ProviderSecret(
                workspace_id=self.workspace_id,
                provider_id=provider.id,
                ciphertext=encrypted.ciphertext,
                algorithm=encrypted.algorithm,
                key_provider=encrypted.key_provider,
                key_version=encrypted.key_version,
                secret_version=1,
                rotated_at=now,
            )
            self.db.add(record)
        else:
            record.ciphertext = encrypted.ciphertext
            record.algorithm = encrypted.algorithm
            record.key_provider = encrypted.key_provider
            record.key_version = encrypted.key_version
            record.secret_version += 1
            record.rotated_at = now
            record.revoked_at = None
            record.revoked_by = None
        provider.api_key_masked = masked
        provider.secret_fingerprint = fingerprint
        if provider.enabled:
            provider.status = "enabled_unverified"
        else:
            provider.status = "configured_disabled"
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.secret.rotate",
            resource_type="provider",
            resource_id=provider.id,
            details={
                "secret_version": record.secret_version,
                "key_version": record.key_version,
            },
        )
        self.db.commit()
        invalidate_provider_plan_cache(self.workspace_id, provider_id=provider_id)
        self.db.refresh(record)
        spec = provider_type_spec(provider.provider_type)
        if (
            spec is not None
            and spec.supports_probe
            and provider.provider_type in MODEL_PROVIDER_TYPES
        ):
            self._probe_after_configuration(provider.id)
        return self._secret_lifecycle(provider, record)

    def rotate_master_key(self) -> dict:
        store = secret_store_from_settings(self.settings)
        if self.settings.secret_provider != "keyring":
            raise AppError(
                409,
                "master_key_rotation_unsupported",
                "In-application master-key rotation requires the keyring secret provider",
            )
        try:
            previous, current = store.rotate_key()
            records = list(
                self.db.scalars(
                    select(ProviderSecret).where(
                        ProviderSecret.workspace_id == self.workspace_id,
                        ProviderSecret.revoked_at.is_(None),
                    )
                )
            )
            replacements: list[tuple[ProviderSecret, str]] = []
            for record in records:
                plaintext = decrypt_provider_secret(self.settings, record)
                replacements.append(
                    (record, SecretCipher(current.secret).encrypt(plaintext))
                )
        except (SecretStoreUnavailable, ProviderSecretUnavailable, ValueError) as exc:
            self.db.rollback()
            raise AppError(
                503,
                "master_key_rotation_failed",
                "The workspace Provider secrets could not be re-encrypted with a new master key",
            ) from exc
        now = utc_now()
        for record, ciphertext in replacements:
            record.ciphertext = ciphertext
            record.algorithm = PROVIDER_SECRET_ALGORITHM
            record.key_provider = self.settings.secret_provider
            record.key_version = current.version
            record.rotated_at = now
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.master_key.rotate",
            resource_type="secret_store",
            resource_id=self.workspace_id,
            details={
                "previous_key_version": previous.version,
                "active_key_version": current.version,
                "reencrypted_secrets": len(replacements),
            },
        )
        self.db.commit()
        return {
            "provider": self.settings.secret_provider,
            "previous_key_version": previous.version,
            "active_key_version": current.version,
            "reencrypted_secrets": len(replacements),
        }

    def update(self, provider_id: str, payload: ProviderUpdateRequest) -> ProviderConfig:
        provider = self.providers.require(provider_id, "provider")
        was_enabled = provider.enabled
        capabilities = dict(provider.capabilities or {})
        fields_set = payload.model_fields_set
        if payload.provider_type is not None and payload.provider_type != provider.provider_type:
            next_spec = provider_type_spec(payload.provider_type)
            current_spec = provider_type_spec(provider.provider_type)
            if (
                next_spec is None
                or not next_spec.create_allowed
                or current_spec is None
                or next_spec.role != current_spec.role
            ):
                raise AppError(
                    422,
                    "provider_protocol_incompatible",
                    "Provider protocol changes must use a supported type with the same role",
                )
            if payload.provider_type in {"codex_chatgpt", "github_copilot"}:
                raise AppError(
                    409,
                    "provider_protocol_requires_device_login",
                    "Switch to this protocol by creating a Provider through its device-login flow",
                )
            provider.provider_type = payload.provider_type
            capabilities["remote_calls_enabled"] = False
            provider.remote_capability = False
            provider.status = "configured_disabled"
        if "base_url" in fields_set:
            updated_base_url = (
                payload.base_url.strip().rstrip("/")
                if isinstance(payload.base_url, str) and payload.base_url.strip()
                else None
            )
            spec = provider_type_spec(provider.provider_type)
            if spec is not None and spec.requires_base_url and not updated_base_url:
                raise AppError(
                    422,
                    "provider_base_url_required",
                    "This Provider requires a base URL",
                )
            provider.base_url = updated_base_url
        if "extra_headers" in fields_set:
            capabilities["extra_headers"] = self._sanitize_extra_headers(
                payload.extra_headers
            )
        if payload.default_model is not None:
            if (
                provider.provider_type in MODEL_PROVIDER_TYPES
                and unified_model_defaults(
                    payload.default_model, provider_type=provider.provider_type
                ).get("supports_text_output", True)
                is False
            ):
                raise AppError(
                    422,
                    "provider_model_not_text_capable",
                    "This model only outputs images and cannot be used as a text chat model",
                )
            model_states = dict(capabilities.get("model_states") or {})
            if (
                provider.provider_type in MODEL_PROVIDER_TYPES
                and model_states.get(payload.default_model) is False
            ):
                raise AppError(
                    409,
                    "provider_model_disabled",
                    "A disabled model cannot be selected as the default model",
                )
            capabilities["default_model"] = payload.default_model
            if provider.provider_type in DEEP_RESEARCH_PROVIDER_TYPES:
                capabilities["deep_research_model"] = payload.default_model
            if provider.provider_type in EMBEDDING_PROVIDER_TYPES:
                capabilities["default_embedding_model_id"] = payload.default_model
        if payload.default_image_generation_model_id is not None:
            model_states = dict(capabilities.get("model_states") or {})
            if (
                provider.provider_type in IMAGE_GENERATION_PROVIDER_TYPES
                and model_states.get(payload.default_image_generation_model_id)
                is False
            ):
                raise AppError(
                    409,
                    "provider_model_disabled",
                    "A disabled model cannot be selected as the default image generation model",
                )
            capabilities["default_image_generation_model_id"] = (
                payload.default_image_generation_model_id
            )
        if payload.default_transcription_model_id is not None:
            capabilities["default_transcription_model_id"] = (
                payload.default_transcription_model_id
            )
        if payload.default_realtime_transcription_model_id is not None:
            capabilities["default_realtime_transcription_model_id"] = (
                payload.default_realtime_transcription_model_id
            )
        if payload.default_vision_model_id is not None:
            capabilities["default_vision_model_id"] = payload.default_vision_model_id
            # Vision companions also expose default_model so discovery UIs and
            # model_provider-shaped adapters can share the same field.
            capabilities.setdefault("default_model", payload.default_vision_model_id)
        if payload.model_defaults_enabled is not None:
            capabilities["model_defaults_enabled"] = payload.model_defaults_enabled
        if payload.provider_priority is not None:
            capabilities["provider_priority"] = payload.provider_priority
        if "enabled" not in fields_set:
            provider.capabilities = capabilities
            self.audit.record(
                actor_id=self.actor_id,
                action="provider.configuration.update",
                resource_type="provider",
                resource_id=provider.id,
                details={
                    "base_url_changed": "base_url" in fields_set,
                    "provider_type_changed": "provider_type" in fields_set,
                    "extra_headers_changed": "extra_headers" in fields_set,
                    "default_model": capabilities.get("default_model"),
                    "default_image_generation_model_id": capabilities.get(
                        "default_image_generation_model_id"
                    ),
                    "default_transcription_model_id": capabilities.get(
                        "default_transcription_model_id"
                    ),
                    "default_realtime_transcription_model_id": capabilities.get(
                        "default_realtime_transcription_model_id"
                    ),
                    "default_vision_model_id": capabilities.get(
                        "default_vision_model_id"
                    ),
                },
            )
            self.db.commit()
            self.db.refresh(provider)
            return provider
        enabled = payload.enabled
        assert isinstance(enabled, bool)
        if enabled and provider.provider_type in MODEL_PROVIDER_TYPES:
            secret = self._active_secret_record(provider.id)
            requires_secret = provider.provider_type != "ollama"
            if (requires_secret and secret is None) or not provider.base_url:
                raise AppError(
                    409,
                    "provider_not_configured",
                    (
                        "Provider requires a base URL"
                        if provider.provider_type == "ollama"
                        else "Provider requires a base URL and encrypted secret"
                    ),
                )
            if not str(capabilities.get("default_model") or "").strip():
                raise AppError(409, "provider_model_required", "A default model is required before enabling")
            provider.remote_capability = True
            capabilities["remote_calls_enabled"] = True
            provider.status = "enabled_unverified"
        elif (
            enabled
            and provider.provider_type in IMAGE_GENERATION_PROVIDER_TYPES
        ):
            secret = self._active_secret_record(provider.id)
            if secret is None or not provider.base_url:
                raise AppError(
                    409,
                    "provider_not_configured",
                    "Image generation Provider requires a base URL and encrypted secret",
                )
            if not str(
                capabilities.get("default_image_generation_model_id") or ""
            ).strip():
                raise AppError(
                    409,
                    "provider_image_model_required",
                    "A default image generation model is required before enabling",
                )
            for current in self.providers.list():
                if (
                    current.id != provider.id
                    and current.provider_type in IMAGE_GENERATION_PROVIDER_TYPES
                ):
                    current.enabled = False
                    current.remote_capability = False
                    current_capabilities = dict(current.capabilities or {})
                    current_capabilities["remote_calls_enabled"] = False
                    current.capabilities = current_capabilities
                    current.status = "disabled"
            provider.remote_capability = True
            capabilities["remote_calls_enabled"] = True
            capabilities["provider_role"] = "image_generation"
            provider.status = "enabled_unverified"
        elif enabled and provider.provider_type in VISION_PROVIDER_TYPES:
            secret = self._active_secret_record(provider.id)
            if secret is None or not provider.base_url:
                raise AppError(
                    409,
                    "provider_not_configured",
                    "Vision Provider requires a base URL and encrypted secret",
                )
            default_vision = str(
                capabilities.get("default_vision_model_id")
                or capabilities.get("default_model")
                or ""
            ).strip()
            if not default_vision:
                raise AppError(
                    409,
                    "provider_vision_model_required",
                    "A default vision model is required before enabling",
                )
            capabilities["default_vision_model_id"] = default_vision
            capabilities.setdefault("default_model", default_vision)
            for current in self.providers.list():
                if (
                    current.id != provider.id
                    and current.provider_type in VISION_PROVIDER_TYPES
                ):
                    current.enabled = False
                    current.remote_capability = False
                    current_capabilities = dict(current.capabilities or {})
                    current_capabilities["remote_calls_enabled"] = False
                    current.capabilities = current_capabilities
                    current.status = "disabled"
            provider.remote_capability = True
            capabilities["remote_calls_enabled"] = True
            capabilities["provider_role"] = "vision"
            capabilities["supports_image_input"] = True
            provider.status = "enabled_unverified"
        elif enabled and provider.provider_type in {
            *SEARCH_PROVIDER_TYPES,
            *FETCH_PROVIDER_TYPES,
        }:
            if not provider.base_url:
                raise AppError(409, "provider_not_configured", "This provider requires a base URL before enabling")
            if provider.provider_type in SEARCH_PROVIDER_TYPES - {"searxng"}:
                if self._active_secret_record(provider.id) is None:
                    raise AppError(
                        409,
                        "provider_not_configured",
                        "This cloud SearchProvider requires an encrypted secret before enabling",
                    )
            if provider.provider_type == "firecrawl_fetch" and self._active_secret_record(provider.id) is None:
                raise AppError(
                    409,
                    "provider_not_configured",
                    "Firecrawl FetchProvider requires an encrypted secret before enabling",
                )
            provider.remote_capability = True
            capabilities["remote_calls_enabled"] = True
            capabilities.setdefault(
                "provider_role",
                "search" if provider.provider_type in SEARCH_PROVIDER_TYPES else "fetch",
            )
            provider.status = "enabled_unverified"
        elif enabled and provider.provider_type in REST_IMAGE_SEARCH_PROVIDER_TYPES:
            # 轻量 REST 文搜图 lane（Tavily / Openverse / Pexels / Pixabay）：
            # 无需模型；需要密钥的供应商启用前必须已配置加密 Secret，
            # 无需密钥的（Openverse 匿名）可直接启用。
            spec = provider_type_spec(provider.provider_type)
            keyless = spec is not None and not spec.requires_secret
            if not keyless and self._active_secret_record(provider.id) is None:
                raise AppError(
                    409,
                    "provider_not_configured",
                    "该文搜图供应商需要先配置 API Key 才能启用",
                )
            provider.remote_capability = True
            capabilities["remote_calls_enabled"] = True
            capabilities["provider_role"] = "image_search"
            provider.status = "enabled_unverified"
        elif (
            enabled
            and provider.provider_type
            in IMAGE_SEARCH_PROVIDER_TYPES - REST_IMAGE_SEARCH_PROVIDER_TYPES
        ):
            # 文搜图/图搜图专用通道（qwen_image_search）：仅通过 DashScope
            # Responses API 提供服务，启用前必须配置 base URL、加密密钥和
            # 默认模型（模型需声明 hosted_image_search 且走 Responses 协议，
            # 由 image_search_provider_for_workspace 在解析时校验）。
            secret = self._active_secret_record(provider.id)
            if secret is None or not provider.base_url:
                raise AppError(
                    409,
                    "provider_not_configured",
                    "文搜图/图搜图 Provider requires a base URL and encrypted secret before enabling",
                )
            if not str(capabilities.get("default_model") or "").strip():
                raise AppError(
                    409,
                    "provider_image_search_model_required",
                    "A default 文搜图/图搜图 model is required before enabling",
                )
            provider.remote_capability = True
            capabilities["remote_calls_enabled"] = True
            capabilities["provider_role"] = "image_search"
            provider.status = "enabled_unverified"
        elif enabled and provider.provider_type in DEEP_RESEARCH_PROVIDER_TYPES:
            secret = self._active_secret_record(provider.id)
            if secret is None or not provider.base_url:
                raise AppError(
                    409,
                    "provider_not_configured",
                    "Deep research requires a base URL and encrypted secret before enabling",
                )
            provider.remote_capability = True
            capabilities["remote_calls_enabled"] = True
            capabilities.setdefault("provider_role", "deep_research")
            provider.status = "enabled_unverified"
        elif enabled and provider.provider_type in MEMORY_PROVIDER_TYPES:
            secret = self._active_secret_record(provider.id)
            if secret is None or not provider.base_url:
                raise AppError(
                    409,
                    "provider_not_configured",
                    "Mem0 Platform requires a base URL and encrypted secret before enabling",
                )
            for current in self.providers.list():
                if current.id != provider.id and current.provider_type in MEMORY_PROVIDER_TYPES:
                    current.enabled = False
                    current.remote_capability = False
                    current.status = "disabled"
            provider.remote_capability = True
            capabilities["remote_calls_enabled"] = True
            capabilities["provider_role"] = "memory"
            capabilities["api_family"] = "mem0_platform_v3"
            provider.status = "enabled_unverified"
        elif enabled and provider.provider_type in TRANSCRIPTION_PROVIDER_TYPES:
            secret = self._active_secret_record(provider.id)
            if secret is None or not provider.base_url:
                raise AppError(
                    409,
                    "provider_not_configured",
                    "ASR Provider requires a base URL and encrypted secret before enabling",
                )
            stored_model = str(
                capabilities.get("default_transcription_model_id") or ""
            ).strip()
            realtime_model = str(
                capabilities.get("default_realtime_transcription_model_id") or ""
            ).strip()
            if not stored_model and not realtime_model:
                raise AppError(
                    409,
                    "provider_transcription_model_required",
                    "A stored-file or realtime transcription model is required before enabling",
                )
            for current in self.providers.list():
                if (
                    current.id != provider.id
                    and current.provider_type in TRANSCRIPTION_PROVIDER_TYPES
                ):
                    current.enabled = False
                    current.remote_capability = False
                    current.status = "disabled"
            provider.remote_capability = True
            capabilities["remote_calls_enabled"] = True
            capabilities["provider_role"] = "transcription"
            provider.status = "enabled_unverified"
        elif enabled and provider.provider_type in EMBEDDING_PROVIDER_TYPES:
            secret = self._active_secret_record(provider.id)
            requires_secret = provider.provider_type != "ollama_embedding"
            if (requires_secret and secret is None) or not provider.base_url:
                raise AppError(
                    409,
                    "provider_not_configured",
                    (
                        "Embedding Provider requires a base URL"
                        if provider.provider_type == "ollama_embedding"
                        else "Embedding Provider requires a base URL and encrypted secret"
                    ),
                )
            # Multiple embedding providers may stay enabled; the memory
            # enhancement settings pick one explicitly by provider id.
            provider.remote_capability = True
            capabilities["remote_calls_enabled"] = True
            capabilities["provider_role"] = "embedding"
            provider.status = "enabled_unverified"
        elif enabled and provider.provider_type == "local_mock":
            provider.remote_capability = False
            capabilities["remote_calls_enabled"] = False
            provider.status = "healthy_local"
        elif enabled:
            raise AppError(
                422,
                "unsupported_provider_type",
                "This provider type cannot be enabled by the current backend",
                {"provider_type": provider.provider_type},
            )
        else:
            provider.remote_capability = False
            capabilities["remote_calls_enabled"] = False
            provider.status = "disabled"
        provider.enabled = enabled
        provider.capabilities = capabilities
        if provider.provider_type in MEMORY_PROVIDER_TYPES and was_enabled != enabled:
            self._bump_memory_provider_epoch()
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.enable" if enabled else "provider.disable",
            resource_type="provider",
            resource_id=provider.id,
            details={
                "base_url_changed": "base_url" in fields_set,
                "default_model": capabilities.get("default_model"),
                "default_image_generation_model_id": capabilities.get(
                    "default_image_generation_model_id"
                ),
                "default_transcription_model_id": capabilities.get(
                    "default_transcription_model_id"
                ),
                "default_realtime_transcription_model_id": capabilities.get(
                    "default_realtime_transcription_model_id"
                ),
                "default_vision_model_id": capabilities.get(
                    "default_vision_model_id"
                ),
            },
        )
        self.db.commit()
        self.db.refresh(provider)
        return provider

    def update_model_capabilities(
        self,
        provider_id: str,
        model_id: str,
        payload: ProviderModelCapabilityUpdateRequest,
    ) -> dict:
        provider = self.providers.require(provider_id, "provider")
        if provider.provider_type not in _MODEL_MANAGEMENT_PROVIDER_TYPES:
            raise AppError(
                409,
                "provider_has_no_models",
                "Model capabilities can only be configured on a Provider that exposes a model discovery endpoint",
            )
        try:
            raw_payload = payload.model_dump()
            raw_payload.pop("apply_to_all", None)
            validated = validate_model_capability_update(raw_payload)
        except ModelCapabilityError as exc:
            raise AppError(422, "invalid_model_capabilities", str(exc)) from exc
        capabilities = dict(provider.capabilities or {})
        models = dict(capabilities.get("models") or {})
        models[model_id] = validated
        capabilities["models"] = models
        provider.capabilities = capabilities
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.model_capabilities.update",
            resource_type="provider_model",
            resource_id=f"{provider.id}:{model_id}",
            details={
                "provider_id": provider.id,
                "model_id": model_id,
                "reasoning_efforts": validated["reasoning_efforts"],
                "hosted_web_search": validated["hosted_web_search"],
                "supports_image_input": validated["supports_image_input"],
                "image_input_mode": validated.get("image_input_mode", "auto"),
                "capability_source": validated["capability_source"],
            },
        )
        self.db.commit()
        return {
            "provider_id": provider.id,
            "model_id": model_id,
            "capabilities": validated,
        }

    def update_model_group_capabilities(
        self,
        provider_id: str,
        payload: ProviderModelCapabilityUpdateRequest,
    ) -> dict:
        provider = self.providers.require(provider_id, "provider")
        if provider.provider_type not in _MODEL_MANAGEMENT_PROVIDER_TYPES:
            raise AppError(
                409,
                "provider_has_no_models",
                "Model capabilities can only be configured on a Provider that exposes a model discovery endpoint",
            )
        try:
            raw_payload = payload.model_dump()
            apply_to_all = raw_payload.pop("apply_to_all", False)
            validated = validate_model_capability_update(raw_payload)
        except ModelCapabilityError as exc:
            raise AppError(422, "invalid_model_capabilities", str(exc)) from exc
        capabilities = dict(provider.capabilities or {})
        capabilities["model_defaults"] = validated
        # Saving a template turns the global override on unless the workspace
        # has explicitly switched it off.
        capabilities.setdefault("model_defaults_enabled", True)
        if apply_to_all:
            capabilities.pop("models", None)
        provider.capabilities = capabilities
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.model_defaults.update",
            resource_type="provider",
            resource_id=provider.id,
            details={
                "provider_id": provider.id,
                "context_window_tokens": validated["context_window_tokens"],
                "context_limit_tokens": validated["context_limit_tokens"],
                "capability_source": validated["capability_source"],
            },
        )
        self.db.commit()
        return {
            "provider_id": provider.id,
            "model_id": "*",
            "capabilities": validated,
        }

    def sync_model_catalog_defaults(
        self, provider_id: str, model_ids: list[str]
    ) -> dict:
        """Write official catalog defaults as the per-model snapshot of every model."""

        # SQLite allows a single writer; parallel discovery requests or an
        # embedded scheduler sweep can hold the write lock past the busy
        # timeout. The whole snapshot write (capabilities + audit) is
        # idempotent, so re-run it from a fresh transaction when the commit
        # was interrupted by ``database is locked``.
        return retry_sqlite_locked(
            self.db,
            lambda: self._sync_model_catalog_defaults_impl(provider_id, model_ids),
        )

    def _sync_model_catalog_defaults_impl(
        self, provider_id: str, model_ids: list[str]
    ) -> dict:
        provider = self.providers.require(provider_id, "provider")
        if provider.provider_type not in _MODEL_MANAGEMENT_PROVIDER_TYPES:
            raise AppError(
                409,
                "provider_has_no_models",
                "Model capabilities can only be configured on a Provider that exposes a model discovery endpoint",
            )
        capabilities = dict(provider.capabilities or {})
        hidden_model_ids = {
            str(item).strip()
            for item in capabilities.get("hidden_model_ids") or []
            if str(item).strip()
        }
        # Persist the wire-protocol family so runtime capability merging can
        # gate DashScope-private claims even for models without a snapshot.
        capabilities["protocol_family"] = protocol_family_for(
            provider.provider_type, provider.base_url
        )
        models = dict(capabilities.get("models") or {})
        synced: list[dict] = []
        warnings: list[dict[str, object]] = []
        for raw_id in model_ids:
            model_id = raw_id.strip()
            if not model_id:
                continue
            snapshot = catalog_capability_snapshot(
                model_id,
                provider_type=provider.provider_type,
                dashscope_hosted=(
                    capabilities["protocol_family"] == "dashscope"
                ),
            )
            try:
                validated = validate_model_capability_update(snapshot)
            except ModelCapabilityError as exc:
                warnings.append(
                    {
                        "model_id": model_id,
                        "field": "capabilities",
                        "message": f"能力目录无效，已跳过保存：{exc}",
                    }
                )
                continue
            models[model_id] = validated
            if validated.get("context_window_source") == "conservative_default":
                warnings.append(
                    {
                        "model_id": model_id,
                        "field": "context_window_tokens",
                        "message": "供应商未提供有效上下文长度，已采用默认 256K，可在模型设置中调整",
                        "fallback_value": validated["context_window_tokens"],
                        "source": "conservative_default",
                    }
                )
            hidden_model_ids.discard(model_id)
            synced.append(
                {
                    "provider_id": provider.id,
                    "model_id": model_id,
                    "capabilities": validated,
                }
            )
        if not synced:
            raise AppError(
                422, "invalid_model_capabilities", "No valid model ids were provided"
            )
        capabilities["models"] = models
        capabilities["hidden_model_ids"] = sorted(hidden_model_ids)
        discovered = [
            str(item).strip()
            for item in capabilities.get("discovered_model_ids") or []
            if str(item).strip()
        ]
        known_ids = list(
            dict.fromkeys(
                [*discovered, *(model_id for model_id in models if model_id not in hidden_model_ids)]
            )
        )
        capabilities["discovered_model_ids"] = known_ids
        capabilities["discovered_model_count"] = len(known_ids)
        states = {
            str(key): value is not False
            for key, value in dict(capabilities.get("model_states") or {}).items()
            if str(key) in known_ids
        }
        for model_id in known_ids:
            states.setdefault(model_id, True)
        capabilities["model_states"] = states
        provider.capabilities = capabilities
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.model_capabilities.sync_catalog",
            resource_type="provider",
            resource_id=provider.id,
            details={
                "provider_id": provider.id,
                "model_count": len(synced),
            },
        )
        self.db.commit()
        return {"provider_id": provider.id, "models": synced, "warnings": warnings}

    @staticmethod
    def _model_default_field(provider: ProviderConfig) -> str:
        """Return the capability field that holds a Provider's default model.

        Image / vision / transcription / embedding / deep-research Providers
        keep their default in a role-specific field instead of ``default_model``
        (which stays a mirror only for vision and embedding).
        """
        provider_type = provider.provider_type
        if provider_type in IMAGE_GENERATION_PROVIDER_TYPES:
            return "default_image_generation_model_id"
        if provider_type in VISION_PROVIDER_TYPES:
            return "default_vision_model_id"
        if provider_type in TRANSCRIPTION_PROVIDER_TYPES:
            return "default_transcription_model_id"
        if provider_type in EMBEDDING_PROVIDER_TYPES:
            return "default_embedding_model_id"
        if provider_type == "qwen_deep_research":
            return "deep_research_model"
        return "default_model"

    @staticmethod
    def _read_model_default(capabilities: dict, default_field: str) -> str:
        return str(
            capabilities.get(default_field)
            or capabilities.get("default_model")
            or ""
        ).strip()

    @staticmethod
    def _write_model_default(
        capabilities: dict, default_field: str, model_id: str
    ) -> None:
        capabilities[default_field] = model_id
        if default_field in {
            "default_vision_model_id",
            "default_embedding_model_id",
        }:
            capabilities["default_model"] = model_id

    @staticmethod
    def _known_model_ids(capabilities: dict, discovered: list[str]) -> list[str]:
        """Every model id a workspace may toggle or configure.

        The discovery snapshot is authoritative for what the vendor reported,
        but workspaces also pin models manually through a catalog-defaults sync
        (``capabilities.models`` keys) or a typed default model.  Those must
        stay toggleable even though the vendor never listed them.
        """
        snapshot_keys = [
            str(item).strip()
            for item in (dict(capabilities.get("models") or {}))
            if str(item).strip()
        ]
        default_id = str(capabilities.get("default_model") or "").strip()
        return list(dict.fromkeys([*discovered, default_id, *snapshot_keys]))

    def delete_model(self, provider_id: str, model_id: str) -> dict:
        """Remove a workspace-pinned model from the provider's unified model list."""

        provider = self.providers.require(provider_id, "provider")
        if provider.provider_type not in _MODEL_MANAGEMENT_PROVIDER_TYPES:
            raise AppError(
                409,
                "provider_has_no_models",
                "Models can only be deleted on a Provider that exposes a model discovery endpoint",
            )
        normalized_model_id = model_id.strip()
        if not normalized_model_id:
            raise AppError(422, "invalid_model_id", "A model id is required")
        capabilities = dict(provider.capabilities or {})
        discovered = [
            str(item).strip()
            for item in capabilities.get("discovered_model_ids") or []
            if str(item).strip() and str(item).strip() != normalized_model_id
        ]
        models = dict(capabilities.get("models") or {})
        was_known = (
            normalized_model_id in models
            or normalized_model_id
            in {
                str(item).strip()
                for item in capabilities.get("discovered_model_ids") or []
                if str(item).strip()
            }
        )
        if not was_known:
            raise AppError(404, "provider_model_not_found", "The model is not in this Provider's saved model list")
        models.pop(normalized_model_id, None)
        hidden_model_ids = {
            str(item).strip()
            for item in capabilities.get("hidden_model_ids") or []
            if str(item).strip()
        }
        hidden_model_ids.add(normalized_model_id)
        states = {
            str(key): value is not False
            for key, value in dict(capabilities.get("model_states") or {}).items()
            if str(key).strip() != normalized_model_id
        }
        capabilities["models"] = models
        capabilities["hidden_model_ids"] = sorted(hidden_model_ids)
        capabilities["discovered_model_ids"] = discovered
        capabilities["discovered_model_count"] = len(discovered)
        capabilities["model_states"] = states
        default_field = self._model_default_field(provider)
        configured_default = self._read_model_default(capabilities, default_field)
        known_ids = self._known_model_ids(capabilities, discovered)
        if configured_default == normalized_model_id:
            next_default = next(
                (item for item in known_ids if states.get(item, True)), ""
            )
            if next_default:
                self._write_model_default(capabilities, default_field, next_default)
            else:
                capabilities.pop(default_field, None)
                if default_field in {
                    "default_vision_model_id",
                    "default_embedding_model_id",
                }:
                    capabilities.pop("default_model", None)
        provider.capabilities = capabilities
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.model.delete",
            resource_type="provider_model",
            resource_id=f"{provider.id}:{normalized_model_id}",
            details={"provider_id": provider.id, "model_id": normalized_model_id},
        )
        self.db.commit()
        return {
            "provider_id": provider.id,
            "model_id": normalized_model_id,
            "default_model": self._read_model_default(capabilities, default_field)
            or None,
        }

    def update_model_state(
        self, provider_id: str, model_id: str, enabled: bool
    ) -> dict:
        provider = self.providers.require(provider_id, "provider")
        if provider.provider_type not in _MODEL_MANAGEMENT_PROVIDER_TYPES:
            raise AppError(
                409,
                "provider_has_no_models",
                "Model state can only be configured on a Provider that exposes a model discovery endpoint",
            )
        normalized_model_id = model_id.strip()
        capabilities = dict(provider.capabilities or {})
        discovered = [
            str(item).strip()
            for item in capabilities.get("discovered_model_ids") or []
            if str(item).strip()
        ]
        default_field = self._model_default_field(provider)
        configured_default = self._read_model_default(capabilities, default_field)
        if normalized_model_id not in self._known_model_ids(
            capabilities, discovered
        ):
            raise AppError(
                404,
                "provider_model_not_found",
                "The model is not present in the latest Provider discovery "
                "snapshot or a saved per-model snapshot",
            )
        states = {
            str(key): value is not False
            for key, value in dict(capabilities.get("model_states") or {}).items()
        }
        states[normalized_model_id] = enabled
        capabilities["model_states"] = states
        if enabled and not configured_default:
            self._write_model_default(capabilities, default_field, normalized_model_id)
            configured_default = normalized_model_id
        elif not enabled and configured_default == normalized_model_id:
            next_default = next(
                (
                    item
                    for item in self._known_model_ids(capabilities, discovered)
                    if states.get(item, True)
                ),
                "",
            )
            if next_default:
                self._write_model_default(capabilities, default_field, next_default)
            else:
                capabilities.pop(default_field, None)
                if default_field in {
                    "default_vision_model_id",
                    "default_embedding_model_id",
                }:
                    capabilities.pop("default_model", None)
        provider.capabilities = capabilities
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.model.enable" if enabled else "provider.model.disable",
            resource_type="provider_model",
            resource_id=f"{provider.id}:{normalized_model_id}",
            details={
                "provider_id": provider.id,
                "model_id": normalized_model_id,
                "enabled": enabled,
                "default_model": (
                    capabilities.get(default_field)
                    or capabilities.get("default_model")
                    or None
                ),
            },
        )
        self.db.commit()
        return {
            "provider_id": provider.id,
            "model_id": normalized_model_id,
            "enabled": enabled,
            "is_default": (
                capabilities.get(default_field) == normalized_model_id
            ),
        }

    def update_model_states(
        self, provider_id: str, requested_states: dict[str, bool]
    ) -> dict:
        provider = self.providers.require(provider_id, "provider")
        if provider.provider_type not in _MODEL_MANAGEMENT_PROVIDER_TYPES:
            raise AppError(
                409,
                "provider_has_no_models",
                "Model state can only be configured on a Provider that exposes a model discovery endpoint",
            )
        capabilities = dict(provider.capabilities or {})
        discovered = [
            str(item).strip()
            for item in capabilities.get("discovered_model_ids") or []
            if str(item).strip()
        ]
        known_ids = self._known_model_ids(capabilities, discovered)
        unknown = sorted(set(requested_states) - set(known_ids))
        if unknown:
            raise AppError(
                404,
                "provider_model_not_found",
                "One or more models are not present in the latest discovery "
                "snapshot or a saved per-model snapshot",
                {"model_ids": unknown},
            )
        previous_states = {
            str(key): value is not False
            for key, value in dict(capabilities.get("model_states") or {}).items()
        }
        states = {
            model_id: bool(
                requested_states.get(
                    model_id, previous_states.get(model_id, True)
                )
            )
            for model_id in known_ids
        }
        capabilities["model_states"] = states
        default_field = self._model_default_field(provider)
        configured_default = self._read_model_default(capabilities, default_field)
        if not configured_default or not states.get(configured_default, False):
            next_default = next(
                (model_id for model_id in known_ids if states[model_id]), ""
            )
            if next_default:
                self._write_model_default(capabilities, default_field, next_default)
            else:
                capabilities.pop(default_field, None)
                if default_field in {
                    "default_vision_model_id",
                    "default_embedding_model_id",
                }:
                    capabilities.pop("default_model", None)
        provider.capabilities = capabilities
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.model_states.bulk_update",
            resource_type="provider",
            resource_id=provider.id,
            details={
                "provider_id": provider.id,
                "enabled_model_ids": [
                    model_id for model_id, enabled in states.items() if enabled
                ],
                "default_model": (
                    capabilities.get(default_field)
                    or capabilities.get("default_model")
                    or None
                ),
            },
        )
        self.db.commit()
        return {
            "provider_id": provider.id,
            "states": states,
            "default_model": (
                capabilities.get(default_field)
                or capabilities.get("default_model")
                or None
            ),
        }

    def refresh_models(self, provider_id: str) -> dict:
        """Force a fresh discovery snapshot and prune stale model state.

        Stale ``model_states`` entries (models no longer reported) are dropped,
        new models default to enabled, and the role-specific default is moved to
        the first enabled model when the previous default vanished or was
        disabled.  This is the Agent-visible "clear + rediscover" path.
        """
        provider = self.providers.require(provider_id, "provider")
        if provider.provider_type not in _MODEL_MANAGEMENT_PROVIDER_TYPES:
            raise AppError(
                409,
                "provider_has_no_models",
                "This provider does not expose a model discovery endpoint",
            )
        model_ids = self._discover(provider)
        capabilities = dict(provider.capabilities or {})
        previous_states = {
            str(key): value is not False
            for key, value in dict(capabilities.get("model_states") or {}).items()
        }
        known_ids = self._known_model_ids(capabilities, model_ids)
        states: dict[str, bool] = {}
        for model_id in known_ids:
            states[model_id] = previous_states.get(model_id, True)
        capabilities["discovered_model_ids"] = list(model_ids)
        capabilities["discovered_model_count"] = len(model_ids)
        capabilities["model_states"] = states
        default_field = self._model_default_field(provider)
        configured_default = self._read_model_default(capabilities, default_field)
        if (
            not configured_default
            or configured_default not in model_ids
            or states.get(configured_default, True) is False
        ):
            next_default = next(
                (item for item in model_ids if states.get(item, True)), ""
            )
            if next_default:
                self._write_model_default(capabilities, default_field, next_default)
            else:
                capabilities.pop(default_field, None)
                if default_field in {
                    "default_vision_model_id",
                    "default_embedding_model_id",
                }:
                    capabilities.pop("default_model", None)
        provider.capabilities = capabilities
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.models.refresh",
            resource_type="provider",
            resource_id=provider.id,
            details={
                "provider_id": provider.id,
                "discovered_model_count": len(model_ids),
                "removed_stale_models": len(set(previous_states) - set(known_ids)),
                "default_model": (
                    capabilities.get(default_field)
                    or capabilities.get("default_model")
                    or None
                ),
            },
        )
        self.db.commit()
        self.db.refresh(provider)
        return {
            "provider_id": provider.id,
            "status": "refreshed",
            "discovered_model_ids": list(model_ids),
            "discovered_model_count": len(model_ids),
            "enabled_model_ids": [
                model_id for model_id in model_ids if states.get(model_id, True)
            ],
            "default_model": (
                capabilities.get(default_field)
                or capabilities.get("default_model")
                or None
            ),
        }

    def probe_connectivity(self, provider_id: str) -> dict:
        """Non-destructive connectivity check for one Provider.

        Unlike ``probe()`` this never flips ``enabled``/``status`` and never
        disables same-role peers.  Chat / image / vision Providers are verified
        through model discovery (GET /models — zero-cost); search / fetch /
        deep-research / memory Providers use their role-specific health probe.
        """
        provider = self.providers.require(provider_id, "provider")
        role = (
            (provider.capabilities or {}).get("provider_role")
            or provider_type_spec(provider.provider_type).role
        )
        details: dict[str, object]
        if provider.provider_type == "local_mock":
            details = {"capability": "development_demo", "remote": False}
            result: dict[str, object] = {
                "provider_id": provider.id,
                "role": "development",
                "status": "healthy_local",
                "zero_cost": True,
                "details": details,
            }
        elif provider.provider_type in {
            *MODEL_PROVIDER_TYPES,
            *IMAGE_GENERATION_PROVIDER_TYPES,
            *IMAGE_SEARCH_PROVIDER_TYPES,
            *VISION_PROVIDER_TYPES,
        }:
            model_ids = self._discover(provider)
            result = {
                "provider_id": provider.id,
                "role": role,
                "status": "healthy",
                "zero_cost": True,
                "discovered_model_count": len(model_ids),
                "discovered_model_ids": list(model_ids)[:100],
                "details": {
                    "capability": "model_discovery",
                    "discovered_model_count": len(model_ids),
                },
            }
        elif provider.provider_type in SEARCH_PROVIDER_TYPES:
            result = {
                "provider_id": provider.id,
                "role": role,
                "status": "healthy",
                "zero_cost": False,
                "details": self._probe_search(provider),
            }
        elif provider.provider_type in FETCH_PROVIDER_TYPES:
            result = {
                "provider_id": provider.id,
                "role": role,
                "status": "healthy",
                "zero_cost": False,
                "details": self._probe_fetch(provider),
            }
        elif provider.provider_type in DEEP_RESEARCH_PROVIDER_TYPES:
            result = {
                "provider_id": provider.id,
                "role": role,
                "status": "healthy",
                "zero_cost": False,
                "details": self._probe_deep_research(provider),
            }
        elif provider.provider_type in MEMORY_PROVIDER_TYPES:
            health = self._mem0_adapter(provider).health()
            result = {
                "provider_id": provider.id,
                "role": role,
                "status": "healthy",
                "zero_cost": False,
                "details": dict(health.details),
            }
        else:
            raise AppError(
                409,
                "provider_probe_not_supported",
                "The provider requires a task-specific probe and cannot be verified by a connectivity check",
            )
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.connectivity.probe",
            resource_type="provider",
            resource_id=provider.id,
            details={
                "role": role,
                "zero_cost": result.get("zero_cost") is True,
                "status": result.get("status"),
            },
        )
        self.db.commit()
        return result

    _DECLARATION_STATUSES = frozenset(
        {"unverified_user_input", "user_confirmed", "verified_by_probe"}
    )

    def update_declaration_status(self, provider_id: str, status: str) -> dict:
        """Confirm or re-flag the Provider's declared capabilities.

        ``user_confirmed`` means the workspace owner reviewed the declared
        role/capabilities; ``verified_by_probe`` records that a live probe
        matched the declaration.
        """
        provider = self.providers.require(provider_id, "provider")
        if status not in self._DECLARATION_STATUSES:
            raise AppError(
                422,
                "invalid_declaration_status",
                "declaration_status must be one of: unverified_user_input, user_confirmed, verified_by_probe",
            )
        capabilities = dict(provider.capabilities or {})
        capabilities["declaration_status"] = status
        provider.capabilities = capabilities
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.declaration.update",
            resource_type="provider",
            resource_id=provider.id,
            details={"declaration_status": status},
        )
        self.db.commit()
        self.db.refresh(provider)
        return {"provider_id": provider.id, "declaration_status": status}

    def validate_default_models(
        self,
        provider_id: str | None = None,
        *,
        repair: bool = False,
    ) -> dict:
        """Audit every discovery-capable Provider's default model.

        A default is consistent when it names a model present in the latest
        discovery snapshot and that model is enabled.  ``repair=True`` moves a
        broken default to the first enabled model (no other state changes).
        """
        if provider_id is None:
            providers = [
                item
                for item in self.providers.list()
                if item.provider_type in _MODEL_MANAGEMENT_PROVIDER_TYPES
            ]
        else:
            providers = [self.providers.require(provider_id, "provider")]
        entries: list[dict[str, object]] = []
        repaired_count = 0
        for provider in providers:
            capabilities = dict(provider.capabilities or {})
            discovered = [
                str(item).strip()
                for item in capabilities.get("discovered_model_ids") or []
                if str(item).strip()
            ]
            states = {
                str(key): value is not False
                for key, value in dict(capabilities.get("model_states") or {}).items()
            }
            default_field = self._model_default_field(provider)
            role = str(capabilities.get("provider_role") or "").strip()
            if not role:
                spec = provider_type_spec(provider.provider_type)
                role = spec.role if spec is not None else provider.provider_type
            primary = self._read_model_default(capabilities, default_field)
            issues: list[str] = []
            if not primary:
                issues.append("no_default_configured")
            elif primary not in discovered:
                issues.append("default_not_in_discovery")
            elif states.get(primary, True) is False:
                issues.append("default_model_disabled")
            if default_field in {
                "default_vision_model_id",
                "default_embedding_model_id",
            }:
                alias = str(capabilities.get("default_model") or "").strip()
                if primary and alias and alias != primary:
                    issues.append("alias_mismatch")
            repaired = False
            if repair and issues:
                next_default = next(
                    (model_id for model_id in discovered if states.get(model_id, True)),
                    "",
                )
                if next_default:
                    self._write_model_default(
                        capabilities, default_field, next_default
                    )
                    provider.capabilities = capabilities
                    repaired = True
                    repaired_count += 1
            entries.append(
                {
                    "provider_id": provider.id,
                    "display_name": provider.display_name,
                    "role": role,
                    "default_field": default_field,
                    "default_model": primary,
                    "discovered_model_count": len(discovered),
                    "enabled_model_count": sum(
                        1 for model_id in discovered if states.get(model_id, True)
                    ),
                    "issues": issues,
                    "repaired": repaired,
                }
            )
        if repair and repaired_count:
            self.audit.record(
                actor_id=self.actor_id,
                action="provider.default_models.repaired",
                resource_type="provider",
                resource_id=provider_id or self.workspace_id,
                details={"repaired_count": repaired_count},
            )
            self.db.commit()
        return {
            "providers": entries,
            "repair_requested": repair,
            "repaired_count": repaired_count,
        }

    def configure_balance_credential(
        self, provider_id: str, secret_label: str
    ) -> dict:
        """Associate an Aliyun AccessKey secret reference with a Provider.

        The label must resolve (purpose ``aliyun_access_key``) to JSON with
        ``access_key_id`` and ``access_key_secret``.  Only the label is stored
        on the Provider; the plaintext never leaves the secret store.
        """
        from app.services.secret_references import SecretReferenceService

        provider = self.providers.require(provider_id, "provider")
        refs = SecretReferenceService(
            self.db, self.workspace_id, self.actor_id, self.settings
        )
        raw = refs.resolve(secret_label, purpose="aliyun_access_key")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AppError(
                422,
                "invalid_balance_credential",
                "The labelled secret must be JSON containing access_key_id and access_key_secret",
            ) from exc
        if (
            not isinstance(parsed, dict)
            or not parsed.get("access_key_id")
            or not parsed.get("access_key_secret")
        ):
            raise AppError(
                422,
                "invalid_balance_credential",
                "The labelled secret must contain non-empty access_key_id and access_key_secret",
            )
        label = refs.normalize_label(secret_label)
        capabilities = dict(provider.capabilities or {})
        capabilities["balance_access_key_reference"] = label
        provider.capabilities = capabilities
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.balance_credential.configured",
            resource_type="provider",
            resource_id=provider.id,
            details={"reference_label": label},
        )
        self.db.commit()
        self.db.refresh(provider)
        return {
            "provider_id": provider.id,
            "balance_credential_configured": True,
            "reference_label": label,
            "secret_masked": True,
        }

    def model_capabilities(self, provider_id: str, model_id: str) -> dict:
        provider = self.providers.require(provider_id, "provider")
        if provider.provider_type not in _MODEL_MANAGEMENT_PROVIDER_TYPES:
            raise AppError(
                409,
                "provider_has_no_models",
                "Model capabilities can only be read from a Provider that exposes a model discovery endpoint",
            )
        capabilities = dict(provider.capabilities or {})
        models = dict(capabilities.get("models") or {})
        configured = models.get(model_id)
        if not isinstance(configured, dict) and is_deepseek_chat_configuration(
            provider.provider_type,
            provider.base_url,
        ):
            configured = self._deepseek_capabilities(capabilities)
        effective = model_capabilities_for_model(capabilities, model_id)
        if isinstance(configured, dict):
            effective.update(configured)
        return {
            "provider_id": provider.id,
            "model_id": model_id,
            "capabilities": effective,
        }

    # Relay stations get configured under any OpenAI/Anthropic-compatible
    # protocol; all of them may implement the one-api billing convention.
    _GATEWAY_BILLING_PROVIDER_TYPES = frozenset(
        {
            "openai_compatible_chat",
            "openai_responses",
            "qwen",
            "deepseek_chat",
            "anthropic_messages",
            "openai_images",
            "openai_compatible_vision",
            "openai_responses_vision",
            "openai_compatible_transcription",
        }
    )

    def _persist_codex_credentials(self, provider_id: str, secret: str) -> None:
        """Store rotated Codex tokens without bumping the rotation lifecycle.

        A refresh is a background housekeeping event, not an operator key
        rotation: the audit trail and the secret version both stay meaningful
        only if automatic rotation is recorded distinctly.
        """

        record = self._active_secret_record(provider_id)
        if record is None:
            return
        try:
            encrypted = encrypt_provider_secret(self.settings, secret)
        except (SecretStoreUnavailable, ValueError):
            return
        record.ciphertext = encrypted.ciphertext
        record.algorithm = encrypted.algorithm
        record.key_provider = encrypted.key_provider
        record.key_version = encrypted.key_version

    def _codex_usage(self, provider: ProviderConfig) -> dict:
        secret = self._decrypt_secret(provider.id)
        try:
            credentials, changed = ensure_fresh_codex_credentials(
                parse_codex_credentials(secret)
            )
            if changed:
                self._persist_codex_credentials(provider.id, credentials.to_secret())
            usage = fetch_codex_usage(credentials)
        except CodexAuthError as exc:
            raise AppError(
                502,
                "provider_balance_unavailable",
                str(exc),
                {"provider_id": provider.id},
            ) from exc
        balance_infos = []
        if usage.credits_balance and not usage.credits_unlimited:
            balance_infos.append(
                {
                    "currency": "USD",
                    "total_balance": usage.credits_balance,
                    "granted_balance": None,
                    "topped_up_balance": None,
                }
            )
        notices = []
        if usage.plan_type:
            notices.append(f"ChatGPT 计划：{usage.plan_type}")
        if usage.credits_unlimited:
            notices.append("额度类型：不限量")
        notices.append("用量按 ChatGPT 订阅计划结算，不消耗 API 额度。")
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.balance.read",
            resource_type="provider",
            resource_id=provider.id,
            details={
                "provider_type": provider.provider_type,
                "vendor": "codex",
                "is_available": not usage.limit_reached,
            },
        )
        self.db.commit()
        return {
            "provider_id": provider.id,
            "vendor": "codex",
            "vendor_label": "Codex 官方直登",
            "is_available": not usage.limit_reached,
            "balance_infos": balance_infos,
            "usage_windows": [
                {
                    "label": window.label,
                    "used_percent": window.used_percent,
                    "window_minutes": window.window_minutes,
                    "resets_at": window.resets_at,
                }
                for window in usage.windows
            ],
            "notice": "；".join(notices),
            "queried_at": utc_now(),
        }

    def github_copilot_device_login_start(self) -> dict:
        try:
            login = start_copilot_device_login()
        except (CopilotProviderHTTPError, CopilotProviderResponseError, CopilotProviderTimeoutError) as exc:
            raise AppError(502, "github_copilot_login_unavailable", str(exc)) from exc
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.github_copilot.device_login.start",
            resource_type="provider",
            resource_id="github_copilot",
            details={"verification_url": login.verification_url},
        )
        self.db.commit()
        return {"device_auth_id": login.device_auth_id, "user_code": login.user_code, "verification_url": login.verification_url, "interval_seconds": login.interval_seconds}

    def github_copilot_device_login_poll(self, *, device_auth_id: str, user_code: str) -> dict:
        try:
            credentials = poll_copilot_device_login(device_auth_id=device_auth_id, user_code=user_code)
        except (CopilotProviderHTTPError, CopilotProviderResponseError, CopilotProviderTimeoutError) as exc:
            raise AppError(502, "github_copilot_login_unavailable", str(exc)) from exc
        if credentials is None:
            return {"status": "pending", "api_key": None}
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.github_copilot.device_login.complete",
            resource_type="provider",
            resource_id="github_copilot",
            details={},
        )
        self.db.commit()
        return {"status": "authorized", "api_key": credentials.github_token}

    def codex_device_login_start(self) -> dict:
        try:
            login = start_codex_device_login()
        except CodexAuthError as exc:
            raise AppError(502, "codex_login_unavailable", str(exc)) from exc
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.codex.device_login.start",
            resource_type="provider",
            resource_id="codex",
            details={"verification_url": login.verification_url},
        )
        self.db.commit()
        return {
            "device_auth_id": login.device_auth_id,
            "user_code": login.user_code,
            "verification_url": login.verification_url,
            "interval_seconds": login.interval_seconds,
        }

    def codex_device_login_poll(self, *, device_auth_id: str, user_code: str) -> dict:
        try:
            credentials = poll_codex_device_login(
                device_auth_id=device_auth_id,
                user_code=user_code,
            )
        except CodexAuthError as exc:
            raise AppError(502, "codex_login_unavailable", str(exc)) from exc
        if credentials is None:
            return {"status": "pending", "api_key": None, "account_id": None}
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.codex.device_login.complete",
            resource_type="provider",
            resource_id="codex",
            details={"plan_type": credentials.plan_type},
        )
        self.db.commit()
        # The token set is handed back once so the caller can save it as the
        # Provider secret through the normal create/rotate path; it is never
        # persisted by the login endpoints themselves.
        return {
            "status": "authorized",
            "api_key": credentials.to_secret(),
            "account_id": credentials.account_id,
            "plan_type": credentials.plan_type,
        }

    def _dashscope_balance(self, provider: ProviderConfig) -> dict:
        """Query Aliyun account balance via the BSS RPC using a configured AccessKey.

        DashScope API keys cannot read account balance.  The workspace owner
        injects an Aliyun AccessKey as a secret reference (purpose
        ``aliyun_access_key``) through the trusted UI; only its label is stored
        on the Provider, so the plaintext never enters a transcript or the DB.
        """
        from app.services.secret_references import SecretReferenceService

        capabilities = dict(provider.capabilities or {})
        label = capabilities.get("balance_access_key_reference")
        if not isinstance(label, str) or not label.strip():
            raise AppError(
                409,
                "provider_balance_unsupported",
                official_no_balance_notice(provider.base_url)
                or "DashScope 账户余额需配置阿里云 AccessKey 后查询",
            )
        refs = SecretReferenceService(
            self.db, self.workspace_id, self.actor_id, self.settings
        )
        try:
            raw = refs.resolve(label, purpose="aliyun_access_key")
        except AppError:
            raise AppError(
                409,
                "provider_balance_unsupported",
                "配置的阿里云 AccessKey 标签不可用，请重新注入",
            ) from None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AppError(
                409,
                "provider_balance_unsupported",
                "配置的阿里云 AccessKey 标签内容无效",
            ) from exc
        access_key_id = (
            str(parsed.get("access_key_id") or "").strip()
            if isinstance(parsed, dict)
            else ""
        )
        access_key_secret = (
            str(parsed.get("access_key_secret") or "").strip()
            if isinstance(parsed, dict)
            else ""
        )
        if not access_key_id or not access_key_secret:
            raise AppError(
                409,
                "provider_balance_unsupported",
                "配置的阿里云 AccessKey 标签缺少 access_key_id 或 access_key_secret",
            )
        try:
            report = fetch_dashscope_balance(
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
            )
        except ProviderBalanceError as exc:
            raise AppError(
                502,
                "provider_balance_unavailable",
                str(exc),
                {"provider_id": provider.id},
            ) from exc
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.balance.read",
            resource_type="provider",
            resource_id=provider.id,
            details={
                "provider_type": provider.provider_type,
                "vendor": "dashscope",
                "is_available": report.is_available,
            },
        )
        self.db.commit()
        return {
            "provider_id": provider.id,
            "vendor": report.vendor,
            "vendor_label": report.vendor_label,
            "is_available": report.is_available,
            "balance_infos": [
                {
                    "currency": item.currency,
                    "total_balance": item.total_balance,
                    "granted_balance": item.granted_balance,
                    "topped_up_balance": item.topped_up_balance,
                }
                for item in report.infos
            ],
            "notice": report.notice,
            "queried_at": utc_now(),
        }

    def balance(self, provider_id: str) -> dict:
        provider = self.providers.require(provider_id, "provider")
        capabilities = dict(provider.capabilities or {})
        extra_headers = self._sanitize_extra_headers(capabilities.get("extra_headers"))
        default_model = str(capabilities.get("default_model") or "")
        is_deepseek_family = (
            provider.provider_type == "deepseek_chat"
            or capabilities.get("model_family") == "deepseek"
            or capabilities.get("brand_id") == "deepseek"
            or default_model.casefold().startswith("deepseek")
            or "deepseek" in default_model.casefold().split("/")[-1]
            or is_deepseek_chat_configuration(provider.provider_type, provider.base_url)
        )
        if provider.provider_type == "codex_chatgpt":
            return self._codex_usage(provider)
        if not provider.base_url:
            raise AppError(409, "provider_not_configured", "Provider base URL is missing")
        if _is_dashscope_host(provider.base_url):
            return self._dashscope_balance(provider)
        # Balance requests carry the saved key, so each vendor implementation
        # only ever talks to its verified official origin; everything else may
        # at most use the relay-station billing convention against the same
        # host that already receives the key for inference.
        if is_deepseek_family and is_official_deepseek_api_base_url(provider.base_url):
            api_key = self._decrypt_secret(provider.id)
            try:
                is_available, balance_infos = fetch_deepseek_balance(
                    base_url=provider.base_url,
                    api_key=api_key,
                    extra_headers=extra_headers,
                )
            except DeepSeekBalanceError as exc:
                raise AppError(
                    502,
                    "provider_balance_unavailable",
                    "DeepSeek balance could not be retrieved",
                    {"provider_id": provider.id},
                ) from exc
            report = BalanceReport(
                vendor="deepseek",
                vendor_label="DeepSeek",
                is_available=is_available,
                infos=[
                    BalanceInfo(
                        currency=item.currency,
                        total_balance=item.total_balance,
                        granted_balance=item.granted_balance,
                        topped_up_balance=item.topped_up_balance,
                    )
                    for item in balance_infos
                ],
            )
        else:
            vendor = detect_balance_vendor(provider.base_url)
            if vendor is None:
                notice = official_no_balance_notice(provider.base_url)
                if notice:
                    raise AppError(409, "provider_balance_unsupported", notice)
            fetcher = {
                "moonshot": fetch_moonshot_balance,
                "siliconflow": fetch_siliconflow_balance,
                "openrouter": fetch_openrouter_balance,
            }.get(vendor or "")
            if fetcher is None:
                if provider.provider_type in self._GATEWAY_BILLING_PROVIDER_TYPES and (
                    supports_gateway_billing(provider.base_url)
                ):
                    fetcher = fetch_gateway_billing_balance
                else:
                    raise AppError(
                        409,
                        "provider_balance_unsupported",
                        "This Provider does not expose an account-balance endpoint",
                    )
            api_key = self._decrypt_secret(provider.id)
            try:
                report = fetcher(
                    base_url=provider.base_url,
                    api_key=api_key,
                    extra_headers=extra_headers,
                )
            except ProviderBalanceError as exc:
                raise AppError(
                    502,
                    "provider_balance_unavailable",
                    "Account balance could not be retrieved",
                    {"provider_id": provider.id},
                ) from exc
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.balance.read",
            resource_type="provider",
            resource_id=provider.id,
            details={
                "provider_type": provider.provider_type,
                "vendor": report.vendor,
                "is_available": report.is_available,
            },
        )
        self.db.commit()
        return {
            "provider_id": provider.id,
            "vendor": report.vendor,
            "vendor_label": report.vendor_label,
            "is_available": report.is_available,
            "balance_infos": [
                {
                    "currency": item.currency,
                    "total_balance": item.total_balance,
                    "granted_balance": item.granted_balance,
                    "topped_up_balance": item.topped_up_balance,
                }
                for item in report.infos
            ],
            "notice": report.notice,
            "queried_at": utc_now(),
        }

    _BALANCE_QUERY_CAPABILITY_KEY = "balance_query"

    def balance_query_config(self, provider_id: str) -> dict:
        provider = self.providers.require(provider_id, "provider")
        return {
            "provider_id": provider.id,
            "config": self._stored_balance_query_config(provider),
        }

    def update_balance_query_config(
        self,
        provider_id: str,
        config: ProviderBalanceQueryConfig | None,
    ) -> dict:
        provider = self.providers.require(provider_id, "provider")
        capabilities = dict(provider.capabilities or {})
        bucket = dict(capabilities.get(self._BALANCE_QUERY_CAPABILITY_KEY) or {})
        if config is None:
            bucket.pop("config", None)
            bucket.pop("last_result", None)
        else:
            bucket["config"] = config.model_dump()
        if bucket:
            capabilities[self._BALANCE_QUERY_CAPABILITY_KEY] = bucket
        else:
            capabilities.pop(self._BALANCE_QUERY_CAPABILITY_KEY, None)
        provider.capabilities = capabilities
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.balance_query.configure",
            resource_type="provider",
            resource_id=provider.id,
            details={
                "enabled": bool(config.enabled) if config else False,
                "template_id": config.template_id if config else None,
                "auto_query_interval_minutes": (
                    config.auto_query_interval_minutes if config else None
                ),
            },
        )
        self.db.commit()
        self.db.refresh(provider)
        return {
            "provider_id": provider.id,
            "config": self._stored_balance_query_config(provider),
        }

    def execute_balance_query(
        self,
        provider_id: str,
        payload: ProviderBalanceQueryExecuteRequest,
    ) -> dict:
        provider = self.providers.require(provider_id, "provider")
        request = payload.request
        needs_base_url = CUSTOM_BALANCE_BASE_URL_PLACEHOLDER in request.url
        if needs_base_url and not provider.base_url:
            raise AppError(409, "provider_not_configured", "Provider base URL is missing")
        referenced_fields = (
            request.url,
            request.body or "",
            *request.headers.values(),
        )
        needs_api_key = any(
            CUSTOM_BALANCE_API_KEY_PLACEHOLDER in item for item in referenced_fields
        )
        api_key = self._optional_secret(provider.id) if needs_api_key else None
        if needs_api_key and api_key is None:
            raise AppError(
                409,
                "provider_secret_unavailable",
                "Provider encrypted secret is missing or revoked",
            )
        config = self._stored_balance_query_config(provider)
        timeout_seconds = (
            payload.timeout_seconds
            or (config.timeout_seconds if config else None)
            or 10.0
        )
        variables = dict(config.variables) if config else {}
        if payload.variables is not None:
            variables.update(payload.variables)
        variables.pop("baseUrl", None)
        variables.pop("apiKey", None)
        try:
            result = execute_custom_balance_request(
                url=request.url,
                method=request.method,
                headers=request.headers,
                body=request.body,
                base_url=provider.base_url or "",
                api_key=api_key or "",
                variables=variables,
                timeout_seconds=timeout_seconds,
            )
        except CustomBalanceQueryError as exc:
            raise AppError(
                502,
                "provider_balance_unavailable",
                str(exc),
                {"provider_id": provider.id},
            ) from exc
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.balance.read",
            resource_type="provider",
            resource_id=provider.id,
            details={
                "provider_type": provider.provider_type,
                "vendor": "custom",
                "is_available": bool(result.get("ok")),
            },
        )
        self.db.commit()
        return {
            "provider_id": provider.id,
            **result,
            "queried_at": utc_now(),
        }

    def save_balance_query_result(
        self,
        provider_id: str,
        payload: ProviderBalanceQueryResultRequest,
    ) -> dict:
        provider = self.providers.require(provider_id, "provider")
        capabilities = dict(provider.capabilities or {})
        bucket = dict(capabilities.get(self._BALANCE_QUERY_CAPABILITY_KEY) or {})
        queried_at = utc_now()
        bucket["last_result"] = {
            **payload.model_dump(),
            "queried_at": queried_at.isoformat(),
        }
        capabilities[self._BALANCE_QUERY_CAPABILITY_KEY] = bucket
        provider.capabilities = capabilities
        self.db.commit()
        self.db.refresh(provider)
        return {
            "provider_id": provider.id,
            **payload.model_dump(),
            "queried_at": queried_at,
        }

    def _stored_balance_query_config(
        self, provider: ProviderConfig
    ) -> ProviderBalanceQueryConfig | None:
        bucket = (provider.capabilities or {}).get(
            self._BALANCE_QUERY_CAPABILITY_KEY
        )
        raw = bucket.get("config") if isinstance(bucket, dict) else None
        if not isinstance(raw, dict):
            return None
        try:
            return ProviderBalanceQueryConfig.model_validate(raw)
        except ValidationError:
            return None

    def delete(self, provider_id: str) -> dict[str, str]:
        provider = self.providers.require(provider_id, "provider")
        self.db.execute(
            delete(ProviderSecret).where(
                ProviderSecret.workspace_id == self.workspace_id,
                ProviderSecret.provider_id == provider.id,
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.delete",
            resource_type="provider",
            resource_id=provider.id,
            details={"display_name": provider.display_name, "provider_type": provider.provider_type},
        )
        self.db.delete(provider)
        self.db.commit()
        invalidate_provider_plan_cache(self.workspace_id, provider_id=provider_id)
        return {"status": "deleted", "resource_id": provider_id}

    def models(self, provider_id: str) -> dict:
        # The discovery snapshot write (capabilities + catalog defaults +
        # audit) can collide with parallel writers on the single SQLite
        # write lock. Re-run the idempotent snapshot when the commit was
        # interrupted by ``database is locked`` instead of failing the page.
        return retry_sqlite_locked(
            self.db,
            lambda: self._models_impl(provider_id),
        )

    def _models_impl(self, provider_id: str) -> dict:
        provider = self.providers.require(provider_id, "provider")
        if provider.provider_type == "local_mock":
            return {
                "provider_id": provider.id,
                "status": "local_demo",
                "models": [
                    {
                        "id": "deterministic-demo",
                        "roles": ["llm_demo"],
                        "streaming": True,
                        "remote": False,
                    }
                ],
            }
        if provider.provider_type not in {
            *MODEL_PROVIDER_TYPES,
            *IMAGE_GENERATION_PROVIDER_TYPES,
            *IMAGE_SEARCH_PROVIDER_TYPES,
            *VISION_PROVIDER_TYPES,
            *TRANSCRIPTION_PROVIDER_TYPES,
            *EMBEDDING_PROVIDER_TYPES,
            "qwen_deep_research",
        }:
            raise AppError(
                409,
                "provider_has_no_models",
                "This provider does not expose a model discovery endpoint",
            )
        model_ids = self._discover(provider)
        capabilities = dict(provider.capabilities or {})
        hidden_model_ids = {
            str(item).strip()
            for item in capabilities.get("hidden_model_ids") or []
            if str(item).strip()
        }
        model_ids = [model_id for model_id in model_ids if model_id not in hidden_model_ids]
        is_deepseek = is_deepseek_chat_configuration(
            provider.provider_type,
            provider.base_url,
        ) or (
            capabilities.get("model_family") == "deepseek"
            or capabilities.get("brand_id") == "deepseek"
            or provider.provider_type == "deepseek_chat"
        )
        # Persist every discovery snapshot so model pickers—including local
        # Ollama embedding configuration—survive a page reload.
        capabilities["discovered_model_ids"] = list(model_ids)
        capabilities["discovered_model_count"] = len(model_ids)
        # Workspace-pinned manual models (per-model capability snapshots) keep
        # their toggle state across a re-discovery; only models neither
        # discovered nor saved as a snapshot are pruned here.
        known_ids = self._known_model_ids(capabilities, model_ids)
        states = {
            str(key): value is not False
            for key, value in dict(capabilities.get("model_states") or {}).items()
            if str(key) in known_ids
        }
        for model_id in model_ids:
            states.setdefault(model_id, True)
        if provider.provider_type == "codex_chatgpt":
            # Drop ChatGPT-auth rejects from older static catalogues so the
            # model picker no longer offers ids the backend will 400 on.
            for stale_id in list(states):
                if (
                    stale_id not in model_ids
                    and stale_id in CODEX_UNSUPPORTED_CHATGPT_MODELS
                ):
                    states.pop(stale_id, None)
        capabilities["model_states"] = states
        configured_default = str(
            (
                capabilities.get("default_embedding_model_id")
                or capabilities.get("default_model")
                or ""
            )
            if provider.provider_type in EMBEDDING_PROVIDER_TYPES
            else capabilities.get("default_model") or ""
        ).strip()
        if (
            not configured_default
            or configured_default not in known_ids
            or states.get(configured_default, True) is False
        ):
            configured_default = next(
                (model_id for model_id in known_ids if states.get(model_id, True)),
                CODEX_DEFAULT_MODEL if provider.provider_type == "codex_chatgpt" else "",
            )
        if provider.provider_type in EMBEDDING_PROVIDER_TYPES:
            capabilities["default_embedding_model_id"] = configured_default
            capabilities["default_model"] = configured_default
        else:
            capabilities["default_model"] = configured_default
        if provider.provider_type == "qwen_deep_research":
            capabilities.setdefault(
                "deep_research_model",
                capabilities.get("default_model") or QwenDeepResearchProvider.DEFAULT_MODEL,
            )
        provider.capabilities = capabilities
        self.db.commit()
        self.db.refresh(provider)
        # Discovery populates the same persistent catalog snapshots as manual
        # additions, so model selection and supplier configuration stay aligned.
        warnings: list[dict[str, object]] = []
        if model_ids:
            sync_result = self.sync_model_catalog_defaults(provider.id, model_ids)
            warnings = list(sync_result.get("warnings") or [])
            provider = self.providers.require(provider.id, "provider")
            self.db.refresh(provider)
            capabilities = dict(provider.capabilities or {})
        return {
            "provider_id": provider.id,
            "status": "discovered_with_warnings" if warnings else "discovered",
            "warnings": warnings,
            "models": [
                {
                    "id": model_id,
                    "roles": (
                        ["image_generation"]
                        if provider.provider_type in IMAGE_GENERATION_PROVIDER_TYPES
                        else ["vision"]
                        if provider.provider_type in VISION_PROVIDER_TYPES
                        else ["transcription"]
                        if provider.provider_type in TRANSCRIPTION_PROVIDER_TYPES
                        else ["embedding"]
                        if provider.provider_type in EMBEDDING_PROVIDER_TYPES
                        else ["image_search"]
                        if provider.provider_type in IMAGE_SEARCH_PROVIDER_TYPES
                        else ["deep_research"]
                        if provider.provider_type == "qwen_deep_research"
                        else ["llm"]
                    ),
                    "streaming": True,
                    "remote": True,
                    "enabled": dict(
                        (provider.capabilities or {}).get("model_states") or {}
                    ).get(model_id, True)
                    is not False,
                    "capabilities": model_capabilities_for_model(
                        {
                            **capabilities,
                            # Injected DeepSeek defaults are built-in behavior,
                            # not the user template, so they stay active even
                            # when the global-override switch is off.
                            **(
                                {
                                    "model_defaults": self._deepseek_capabilities(
                                        capabilities
                                    ),
                                    "model_defaults_enabled": True,
                                }
                                if (
                                    is_deepseek
                                    or model_id.casefold().startswith("deepseek")
                                    or "deepseek"
                                    in model_id.casefold().split("/")[-1]
                                )
                                and not isinstance(
                                    capabilities.get("model_defaults"), dict
                                )
                                else {
                                    "model_defaults": capabilities.get(
                                        "model_defaults", {}
                                    )
                                }
                            ),
                        },
                        model_id,
                    ),
                }
                for model_id in model_ids
            ],
        }

    @staticmethod
    def _deepseek_capabilities(capabilities: dict) -> dict:
        return {
            "reasoning_efforts": list(
                capabilities.get("reasoning_efforts")
                or ["low", "medium", "high", "xhigh"]
            ),
            "thinking_mapping": dict(
                capabilities.get("thinking_mapping")
                or {
                    "off": None,
                    "low": "high",
                    "medium": "high",
                    "high": "high",
                    "xhigh": "max",
                }
            ),
            "default_thinking_mode": capabilities.get("default_thinking_mode", "off"),
            "reasoning_parameter": capabilities.get(
                "reasoning_parameter", "reasoning_effort"
            ),
            "hosted_web_search": False,
            "supports_image_input": capabilities.get("supports_image_input") is True,
            "default_search_route": capabilities.get("default_search_route", "auto"),
            "capability_source": capabilities.get("capability_source", "official_catalog"),
        }

    def probe(self, provider_id: str) -> ProviderConfig:
        provider = self.providers.require(provider_id, "provider")
        details: dict[str, object]
        discovered_model_ids: list[str] = []
        if provider.provider_type == "local_mock":
            provider.status = "healthy_local"
            details = {"capability": "development_demo", "remote": False}
        elif provider.provider_type in {
            *MODEL_PROVIDER_TYPES,
            *IMAGE_GENERATION_PROVIDER_TYPES,
            *IMAGE_SEARCH_PROVIDER_TYPES,
            *VISION_PROVIDER_TYPES,
        }:
            model_ids = self._discover(provider)
            discovered_model_ids = model_ids
            details = {
                "capability": "model_discovery",
                "discovered_model_count": len(model_ids),
            }
            provider.status = "healthy"
        elif provider.provider_type in SEARCH_PROVIDER_TYPES:
            details = self._probe_search(provider)
            provider.status = "healthy"
        elif provider.provider_type in FETCH_PROVIDER_TYPES:
            details = self._probe_fetch(provider)
            provider.status = "healthy"
        elif provider.provider_type in DEEP_RESEARCH_PROVIDER_TYPES:
            details = self._probe_deep_research(provider)
            provider.status = "healthy"
        elif provider.provider_type in MEMORY_PROVIDER_TYPES:
            health = self._mem0_adapter(provider).health()
            details = dict(health.details)
            details["capability"] = "memory"
            provider.remote_capability = True
            provider.status = "healthy"
        else:
            raise AppError(
                409,
                "provider_probe_not_supported",
                "The provider requires a task-specific probe and cannot be marked healthy by model discovery",
            )
        if provider.provider_type != "local_mock":
            provider.remote_capability = True
        capabilities = dict(provider.capabilities or {})
        # Wire-protocol family gates DashScope-private catalogue claims at
        # runtime; persist it on every probe so it stays authoritative.
        capabilities["protocol_family"] = protocol_family_for(
            provider.provider_type, provider.base_url
        )
        if provider.provider_type in {
            *MODEL_PROVIDER_TYPES,
            *IMAGE_GENERATION_PROVIDER_TYPES,
            *VISION_PROVIDER_TYPES,
        }:
            capabilities["discovered_model_count"] = details["discovered_model_count"]
            capabilities["discovered_model_ids"] = discovered_model_ids
        if provider.provider_type in MODEL_PROVIDER_TYPES:
            states = {
                str(key): value is not False
                for key, value in dict(capabilities.get("model_states") or {}).items()
            }
            for model_id in discovered_model_ids:
                states.setdefault(model_id, True)
            capabilities["model_states"] = states
            configured_default = str(capabilities.get("default_model") or "").strip()
            if not configured_default or states.get(configured_default, True) is False:
                capabilities["default_model"] = next(
                    (
                        model_id
                        for model_id in self._known_model_ids(
                            capabilities, discovered_model_ids
                        )
                        if states.get(model_id, True)
                    ),
                    "",
                )
        capabilities["last_probe_result"] = "healthy"
        capabilities["last_probe_details"] = details
        capabilities["remote_calls_enabled"] = provider.provider_type != "local_mock"
        if provider.provider_type in MEMORY_PROVIDER_TYPES:
            capabilities["api_family"] = "mem0_platform_v3"
        provider.capabilities = capabilities
        was_enabled = provider.enabled
        provider.enabled = True
        if provider.provider_type != "local_mock":
            provider.remote_capability = True
        if provider.provider_type in {
            *IMAGE_GENERATION_PROVIDER_TYPES,
            *VISION_PROVIDER_TYPES,
            *MEMORY_PROVIDER_TYPES,
            *TRANSCRIPTION_PROVIDER_TYPES,
        }:
            peer_types = (
                IMAGE_GENERATION_PROVIDER_TYPES
                if provider.provider_type in IMAGE_GENERATION_PROVIDER_TYPES
                else VISION_PROVIDER_TYPES
                if provider.provider_type in VISION_PROVIDER_TYPES
                else MEMORY_PROVIDER_TYPES
                if provider.provider_type in MEMORY_PROVIDER_TYPES
                else TRANSCRIPTION_PROVIDER_TYPES
            )
            for current in self.providers.list():
                if current.id == provider.id or current.provider_type not in peer_types:
                    continue
                current.enabled = False
                current.remote_capability = False
                current_capabilities = dict(current.capabilities or {})
                current_capabilities["remote_calls_enabled"] = False
                current.capabilities = current_capabilities
                current.status = "disabled"
        if provider.provider_type in MEMORY_PROVIDER_TYPES and not was_enabled:
            self._bump_memory_provider_epoch()
        self.audit.record(
            actor_id=self.actor_id,
            action="provider.probe",
            resource_type="provider",
            resource_id=provider.id,
            details={"status": provider.status, "probe": details},
        )
        self.db.commit()
        self.db.refresh(provider)
        if discovered_model_ids:
            self.sync_model_catalog_defaults(provider.id, discovered_model_ids)
            provider = self.providers.require(provider.id, "provider")
            self.db.refresh(provider)
        return provider

    def _probe_after_configuration(self, provider_id: str) -> None:
        """Probe a newly stored credential and auto-enable only on real health."""

        try:
            self.probe(provider_id)
        except AppError as exc:
            self.db.rollback()
            provider = self.providers.require(provider_id, "provider")
            capabilities = dict(provider.capabilities or {})
            capabilities["last_probe_result"] = "failed"
            capabilities["last_probe_details"] = {
                "error_code": exc.code,
                "status_code": exc.status_code,
            }
            capabilities["remote_calls_enabled"] = False
            provider.capabilities = capabilities
            provider.enabled = False
            provider.remote_capability = False
            provider.status = "probe_failed"
            self.audit.record(
                actor_id=self.actor_id,
                action="provider.probe.failed",
                resource_type="provider",
                resource_id=provider.id,
                details={
                    "error_code": exc.code,
                    "status_code": exc.status_code,
                    "auto_probe": True,
                },
            )
            self.db.commit()

    def _probe_search(self, provider: ProviderConfig) -> dict[str, object]:
        if not provider.base_url:
            raise AppError(409, "provider_not_configured", "Provider base URL is missing")
        try:
            if provider.provider_type == "searxng":
                adapter = SearXNGSearchProvider(
                    provider_id=provider.id,
                    base_url=provider.base_url,
                    api_key=self._optional_secret(provider.id),
                    allow_private_bridge_urls=self.settings.allow_private_bridge_urls,
                )
            elif provider.provider_type == "anysearch":
                adapter = AnySearchSearchProvider(
                    provider_id=provider.id,
                    base_url=provider.base_url,
                    api_key=self._decrypt_secret(provider.id),
                )
            else:
                adapter = CloudSearchProvider(
                    provider_id=provider.id,
                    provider_type=provider.provider_type,
                    base_url=provider.base_url,
                    api_key=self._decrypt_secret(provider.id),
                    allow_private_bridge_urls=self.settings.allow_private_bridge_urls,
                )
            return adapter.probe()
        except ValueError as exc:
            raise AppError(409, "provider_not_configured", str(exc)) from exc
        except FetchProviderError as exc:
            raise AppError(502, "provider_probe_failed", str(exc)) from exc
        except SearchProviderTimeout as exc:
            raise AppError(504, "provider_probe_timeout", "Search Provider probe timed out") from exc
        except SearchProviderResponseError as exc:
            raise AppError(
                502, "provider_probe_invalid_response", "Search Provider probe returned an invalid response"
            ) from exc
        except SearchProviderError as exc:
            raise AppError(502, "provider_probe_failed", str(exc)) from exc

    def _probe_fetch(self, provider: ProviderConfig) -> dict[str, object]:
        if not provider.base_url:
            raise AppError(409, "provider_not_configured", "Provider base URL is missing")
        try:
            if provider.provider_type == "crawl4ai_http":
                adapter = Crawl4AIHTTPFetchProvider(
                    provider_id=provider.id,
                    base_url=provider.base_url,
                    api_key=self._optional_secret(provider.id),
                    allow_private_bridge_urls=self.settings.allow_private_bridge_urls,
                )
            else:
                adapter = FirecrawlFetchProvider(
                    provider_id=provider.id,
                    base_url=provider.base_url,
                    api_key=self._decrypt_secret(provider.id),
                    allow_private_bridge_urls=self.settings.allow_private_bridge_urls,
                )
            return adapter.probe()
        except FetchProviderTimeout as exc:
            raise AppError(504, "provider_probe_timeout", "Fetch Provider probe timed out") from exc
        except FetchProviderError as exc:
            raise AppError(502, "provider_probe_failed", str(exc)) from exc

    def _probe_deep_research(self, provider: ProviderConfig) -> dict[str, object]:
        if not provider.base_url:
            raise AppError(409, "provider_not_configured", "Provider base URL is missing")
        try:
            adapter = HTTPDeepResearchProvider(
                provider_id=provider.id,
                base_url=provider.base_url,
                api_key=self._decrypt_secret(provider.id),
                declared_capabilities=provider.capabilities,
            )
            return adapter.probe()
        except DeepResearchProviderTimeout as exc:
            raise AppError(
                504, "provider_probe_timeout", "Deep Research Provider probe timed out"
            ) from exc
        except DeepResearchProviderError as exc:
            raise AppError(502, "provider_probe_failed", str(exc)) from exc

    def _discover(self, provider: ProviderConfig) -> list[str]:
        # Static catalogues for endpoints without a public model list.
        if provider.provider_type == "codex_chatgpt":
            return list(CODEX_KNOWN_MODELS)
        if provider.provider_type == "qwen_deep_research":
            return list(QwenDeepResearchProvider.KNOWN_MODELS)
        if not provider.base_url:
            raise AppError(409, "provider_not_configured", "Provider base URL is missing")
        extra_headers = self._sanitize_extra_headers(
            (provider.capabilities or {}).get("extra_headers")
        )
        try:
            if provider.provider_type in {"ollama", "ollama_embedding"}:
                from app.providers.remote.ollama import discover_ollama_models

                return discover_ollama_models(
                    base_url=provider.base_url,
                    api_key=self._optional_secret(provider.id),
                    extra_headers=extra_headers,
                )
            if provider.provider_type == "ollama_cloud":
                from app.providers.remote.ollama import discover_ollama_cloud_models

                return discover_ollama_cloud_models(
                    base_url=provider.base_url,
                    api_key=self._optional_secret(provider.id) or "",
                    extra_headers=extra_headers,
                )
            api_key = self._decrypt_secret(provider.id)
            if provider.provider_type == "github_copilot":
                return discover_copilot_models(
                    base_url=provider.base_url,
                    github_token=api_key,
                    extra_headers=extra_headers,
                )
            if provider.provider_type == "anthropic_messages":
                from app.providers.remote.anthropic import discover_anthropic_models

                return discover_anthropic_models(
                    base_url=provider.base_url,
                    api_key=api_key,
                    extra_headers=extra_headers,
                )
            return discover_remote_models(
                base_url=provider.base_url,
                api_key=api_key,
                extra_headers=extra_headers,
            )
        except ProviderInvalidUrlError as exc:
            # User-facing configuration mistakes (missing http(s):// protocol,
            # unusable hosts) must surface as a clear 4xx instead of a 500.
            # Kept first: copilot.py re-exports the same ProviderHTTPError
            # classes, so the Copilot* aliases below would otherwise match.
            raise AppError(422, "provider_base_url_invalid", str(exc)) from exc
        except CopilotProviderTimeoutError as exc:
            raise AppError(504, "provider_timeout", "Provider model discovery timed out") from exc
        except CopilotProviderResponseError as exc:
            raise AppError(502, "provider_invalid_response", str(exc)) from exc
        except CopilotProviderHTTPError as exc:
            raise AppError(502, "provider_http_error", str(exc)) from exc
        except ProviderTimeoutError as exc:
            raise AppError(504, "provider_timeout", "Provider model discovery timed out") from exc
        except ProviderResponseError as exc:
            raise AppError(502, "provider_invalid_response", str(exc)) from exc
        except ProviderHTTPError as exc:
            raise AppError(502, "provider_http_error", str(exc)) from exc

    @staticmethod
    def _sanitize_extra_headers(raw: object) -> dict[str, str]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise AppError(
                422,
                "invalid_extra_headers",
                "extra_headers must be an object of string header names to string values",
            )
        headers: dict[str, str] = {}
        for key, value in raw.items():
            name = str(key).strip()
            text = str(value).strip() if value is not None else ""
            if not name:
                continue
            if len(name) > 128 or len(text) > 2_048:
                raise AppError(
                    422,
                    "invalid_extra_headers",
                    "extra_headers names/values exceed the allowed length",
                )
            if name.casefold() in {
                "authorization",
                "x-api-key",
                "api-key",
                "proxy-authorization",
                "cookie",
                "set-cookie",
                "host",
                "content-length",
            }:
                # Credentials and hop-by-hop headers always come from the Secret
                # Store / transport layer, never from user-supplied custom headers.
                continue
            if not text:
                continue
            headers[name] = text
        if len(headers) > 32:
            raise AppError(
                422,
                "invalid_extra_headers",
                "At most 32 custom request headers are allowed",
            )
        return headers

    def _mem0_adapter(self, provider: ProviderConfig) -> Mem0PlatformAdapter:
        if not provider.base_url:
            raise AppError(
                409,
                "provider_not_configured",
                "Mem0 Platform base URL is missing",
            )
        workspace = self.db.get(Workspace, self.workspace_id)
        if workspace is None:
            raise AppError(404, "workspace_not_found", "Workspace does not exist")
        api_key = self._decrypt_secret(provider.id)
        try:
            identity_key = secret_store_from_settings(self.settings).identity_key(create=True)
        except SecretStoreUnavailable as exc:
            raise AppError(
                503, "secret_store_unavailable", "The secret store identity key is unavailable"
            ) from exc
        return Mem0PlatformAdapter(
            provider_id=provider.id,
            base_url=provider.base_url,
            api_key=api_key,
            workspace_entity=mem0_entity_id(
                tenant_id=workspace.tenant_id,
                user_id=self.actor_id,
                workspace_id=workspace.id,
                secret=identity_key,
            ),
        )

    def _secret_record(self, provider_id: str) -> ProviderSecret | None:
        return self.db.scalar(
            select(ProviderSecret).where(
                ProviderSecret.workspace_id == self.workspace_id,
                ProviderSecret.provider_id == provider_id,
            )
        )

    def _active_secret_record(self, provider_id: str) -> ProviderSecret | None:
        record = self._secret_record(provider_id)
        if record is None or record.revoked_at is not None or not record.ciphertext:
            return None
        return record

    def _decrypt_secret(self, provider_id: str) -> str:
        record = self._active_secret_record(provider_id)
        if record is None:
            raise AppError(
                409,
                "provider_secret_unavailable",
                "Provider encrypted secret is missing or revoked",
            )
        try:
            return decrypt_provider_secret(self.settings, record)
        except ProviderSecretUnavailable as exc:
            raise AppError(
                503,
                "provider_secret_unavailable",
                "Provider encrypted secret cannot be opened by the configured secret store",
            ) from exc

    def _optional_secret(self, provider_id: str) -> str | None:
        return (
            self._decrypt_secret(provider_id)
            if self._active_secret_record(provider_id) is not None
            else None
        )

    @staticmethod
    def _secret_lifecycle(provider: ProviderConfig, record: ProviderSecret) -> dict:
        return {
            "provider_id": provider.id,
            "api_key_masked": provider.api_key_masked,
            "status": provider.status,
            "secret_version": record.secret_version,
            "key_version": record.key_version,
            "rotated_at": record.rotated_at,
            "revoked_at": record.revoked_at,
        }

    def _bump_memory_provider_epoch(self) -> None:
        setting = self.db.scalar(
            select(WorkspaceSetting).where(
                WorkspaceSetting.workspace_id == self.workspace_id,
                WorkspaceSetting.key == "memory.provider_epoch",
            )
        )
        try:
            current = int(setting.value) if setting is not None else 1
        except (TypeError, ValueError):
            current = 1
        if setting is None:
            self.db.add(
                WorkspaceSetting(
                    workspace_id=self.workspace_id,
                    key="memory.provider_epoch",
                    value=current + 1,
                )
            )
        else:
            setting.value = current + 1


class UsageService:
    def __init__(self, db: Session, workspace_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.usage = UsageRepository(db, workspace_id)

    def events(
        self,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
        feature: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[UsageEvent]:
        statement = self._filtered_query(
            provider_id=provider_id,
            model_id=model_id,
            feature=feature,
            start_at=start_at,
            end_at=end_at,
        )
        return list(
            self.db.scalars(statement.order_by(UsageEvent.created_at.desc())).all()
        )

    def summary(
        self,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
        feature: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> UsageSummary:
        filtered = self._filtered_query(
            provider_id=provider_id,
            model_id=model_id,
            feature=feature,
            start_at=start_at,
            end_at=end_at,
        ).subquery()
        values = self.db.execute(
            select(
                func.coalesce(func.sum(filtered.c.input_tokens), 0),
                func.coalesce(func.sum(filtered.c.cached_input_tokens), 0),
                func.coalesce(func.sum(filtered.c.cache_creation_input_tokens), 0),
                func.coalesce(func.sum(filtered.c.output_tokens), 0),
                func.coalesce(func.sum(filtered.c.reasoning_tokens), 0),
                func.coalesce(func.sum(filtered.c.total_tokens), 0),
                func.count(filtered.c.id),
                func.coalesce(func.sum(filtered.c.cost_usd), 0.0),
                func.coalesce(func.sum(filtered.c.cost_cny), 0.0),
            )
        ).one()
        remote_count = self.db.scalar(
            select(func.count()).select_from(filtered).where(
                filtered.c.provider_id != "local_mock",
                filtered.c.cost_status != "non_billable",
            )
        ) or 0
        unpriced_count = self.db.scalar(
            select(func.count()).select_from(filtered).where(
                filtered.c.cost_status.in_(["unpriced", "estimated_usage_missing"])
            )
        ) or 0
        return UsageSummary(
            workspace_id=self.workspace_id,
            input_tokens=int(values[0]),
            cached_input_tokens=int(values[1]),
            cache_creation_input_tokens=int(values[2]),
            output_tokens=int(values[3]),
            reasoning_tokens=int(values[4]),
            total_tokens=int(values[5]),
            attempts=int(values[6]),
            cost_usd=float(values[7]),
            cost_cny=float(values[8]),
            unpriced_events=int(unpriced_count),
            remote_usage_recorded=bool(remote_count),
        )

    def clear_events(self, *, actor_id: str) -> dict[str, int]:
        """Delete every UsageEvent in the current workspace.

        Price versions, exchange rates, and budget policies remain so the
        workspace can keep billing configuration while resetting the ledger.
        """

        count = int(
            self.db.scalar(
                select(func.count())
                .select_from(UsageEvent)
                .where(UsageEvent.workspace_id == self.workspace_id)
            )
            or 0
        )
        self.db.execute(
            delete(UsageEvent).where(UsageEvent.workspace_id == self.workspace_id)
        )
        AuditRepository(self.db, self.workspace_id).record(
            actor_id=actor_id,
            action="usage.events.cleared",
            resource_type="usage_event",
            resource_id=self.workspace_id,
            details={"deleted_count": count},
        )
        self.db.commit()
        return {"deleted_count": count}

    def _filtered_query(
        self,
        *,
        provider_id: str | None,
        model_id: str | None,
        feature: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
    ):
        statement = self.usage.query()
        if provider_id:
            statement = statement.where(UsageEvent.provider_id == provider_id)
        if model_id:
            statement = statement.where(UsageEvent.model_id == model_id)
        if feature:
            statement = statement.where(UsageEvent.feature == feature)
        if start_at:
            statement = statement.where(UsageEvent.created_at >= start_at)
        if end_at:
            statement = statement.where(UsageEvent.created_at < end_at)
        return statement


class PluginService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.plugins = PluginRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)

    def list(self) -> list[PluginRecord]:
        return list(self.plugins.list())

    def toggle(self, plugin_id: str, payload: PluginToggleRequest) -> PluginRecord:
        plugin = self.plugins.require(plugin_id, "plugin")
        next_status = "enabled" if payload.enabled else "disabled"
        if payload.enabled and plugin.plugin_type == "trusted_component":
            # Imported components must retain an authorization matching the exact
            # immutable manifest and permission fingerprint. The local import
            # keeps the generic plugin registry independent of component schemas.
            from app.services.components import ComponentService

            next_status = ComponentService(
                self.db,
                self.workspace_id,
                self.actor_id,
            ).assert_can_enable(plugin)
        plugin.enabled = payload.enabled
        plugin.status = next_status
        self.audit.record(
            actor_id=self.actor_id,
            action="plugin.enable" if payload.enabled else "plugin.disable",
            resource_type="plugin",
            resource_id=plugin.id,
            details={"permissions": plugin.permissions, "status": plugin.status},
        )
        self.db.commit()
        self.db.refresh(plugin)
        return plugin


class MigrationService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.jobs = MigrationRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)

    def list(self) -> list[MigrationJob]:
        return list(self.jobs.list())

    def preflight(self, payload: MigrationPreflightRequest) -> MigrationJob:
        checks = [
            {"key": "source_readable", "status": "passed" if payload.source_kind == "sqlite" else "not_checked"},
            {"key": "target_adapter", "status": "missing" if payload.target_kind != "sqlite" else "passed"},
            {"key": "maintenance_window", "status": "required"},
            {"key": "dual_write", "status": "forbidden"},
        ]
        ready = all(item["status"] == "passed" for item in checks)
        job = self.jobs.add(
            MigrationJob(
                workspace_id=self.workspace_id,
                source_kind=payload.source_kind,
                target_kind=payload.target_kind,
                status="cutover_ready" if ready else "preflight_blocked",
                report={"checks": checks, "ready": ready, "data_copied": False},
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="migration.preflight",
            resource_type="migration_job",
            resource_id=job.id,
            outcome="success" if ready else "blocked",
        )
        self.db.commit()
        self.db.refresh(job)
        return job

    def start(self, job_id: str) -> None:
        job = self.jobs.require(job_id, "migration job")
        if job.status != "cutover_ready":
            raise AppError(409, "migration_not_ready", "Preflight is not ready; no data was copied")
        raise AppError(
            501,
            "migration_executor_not_configured",
            "This MVP exposes truthful preflight state but has no target migration executor",
        )


class AuditService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str | None = None) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id or "system"
        self.audit = AuditRepository(db, workspace_id)

    def list(self, action: str | None = None) -> list[AuditEvent]:
        statement = select(AuditEvent).where(AuditEvent.workspace_id == self.workspace_id)
        if action:
            statement = statement.where(AuditEvent.action == action)
        return list(self.db.scalars(statement.order_by(AuditEvent.created_at.desc()).limit(200)).all())

    def delete(self, event_id: str) -> dict[str, str | None]:
        event = self.db.scalar(
            select(AuditEvent).where(
                AuditEvent.workspace_id == self.workspace_id,
                AuditEvent.id == event_id,
            )
        )
        if event is None:
            raise AppError(404, "not_found", "Audit event not found in this workspace")
        # Snapshot metadata before delete so the purge event stays useful without
        # re-storing the original details payload.
        snapshot = {
            "id": event.id,
            "action": event.action,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "outcome": event.outcome,
            "deleted_created_at": event.created_at.isoformat() if event.created_at else None,
        }
        self.db.delete(event)
        self.db.flush()
        self.audit.record(
            actor_id=self.actor_id,
            action="audit.event_deleted",
            resource_type="audit_event",
            resource_id=event_id,
            details={
                "deleted_action": snapshot["action"],
                "deleted_resource_type": snapshot["resource_type"],
                "deleted_resource_id": snapshot["resource_id"],
                "deleted_outcome": snapshot["outcome"],
                "deleted_created_at": snapshot["deleted_created_at"],
            },
        )
        self.db.commit()
        return snapshot

    def delete_many(self, event_ids: list[str]) -> dict[str, int | list[str]]:
        unique_ids = list(dict.fromkeys(event_id for event_id in event_ids if event_id))
        if not unique_ids:
            raise AppError(422, "invalid_request", "At least one audit event id is required")
        if len(unique_ids) > 100:
            raise AppError(422, "invalid_request", "Cannot delete more than 100 audit events at once")
        events = list(
            self.db.scalars(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == self.workspace_id,
                    AuditEvent.id.in_(unique_ids),
                )
            ).all()
        )
        found_ids = {event.id for event in events}
        missing = [event_id for event_id in unique_ids if event_id not in found_ids]
        if missing:
            raise AppError(
                404,
                "not_found",
                "One or more audit events were not found in this workspace",
                {"missing_ids": missing},
            )
        deleted_summaries = [
            {
                "id": event.id,
                "action": event.action,
                "resource_type": event.resource_type,
                "resource_id": event.resource_id,
                "outcome": event.outcome,
            }
            for event in events
        ]
        for event in events:
            self.db.delete(event)
        self.db.flush()
        self.audit.record(
            actor_id=self.actor_id,
            action="audit.events_deleted",
            resource_type="audit_event",
            resource_id=self.workspace_id,
            details={
                "count": len(deleted_summaries),
                "deleted_ids": [item["id"] for item in deleted_summaries],
                "actions": sorted({item["action"] for item in deleted_summaries}),
            },
        )
        self.db.commit()
        return {"deleted": len(deleted_summaries), "ids": [item["id"] for item in deleted_summaries]}


class SettingsService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = SettingRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)

    # Settings whose values hold credentials (e.g. the SMTP alert password)
    # are managed through dedicated endpoints with masked views and must not
    # leak through the generic listing.
    HIDDEN_SETTING_KEYS = frozenset({"usage.alert_email"})
    SETTING_CATALOG: dict[str, dict] = {
        CHAT_SUGGESTED_PROMPTS_SETTING_KEY: {
            "description": "Generate suggested follow-up prompts after chat replies",
            "default": {"enabled": True},
            "risk": "low",
        },
        CHAT_DICTATION_CLEANUP_SETTING_KEY: {
            "description": "Clean up dictated text with the configured model",
            "default": {"enabled": False},
            "risk": "low",
        },
        CHAT_CONTEXT_USAGE_SETTING_KEY: {
            "description": "Show and use conversation context accounting",
            "default": {"enabled": True},
            "risk": "low",
        },
        CHAT_AUTO_TITLE_MODEL_SETTING_KEY: {
            "description": "Default model for automatic conversation titles",
            "default": {"provider_id": None, "model_id": None},
            "risk": "medium",
        },
        CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY: {
            "description": "Default model for suggested prompts",
            "default": {"provider_id": None, "model_id": None},
            "risk": "medium",
        },
        CHAT_DICTATION_CLEANUP_MODEL_SETTING_KEY: {
            "description": "Default model for dictation cleanup",
            "default": {"provider_id": None, "model_id": None},
            "risk": "medium",
        },
        FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY: {
            "description": "Capability-specific default Provider/model routing",
            "default": {},
            "risk": "medium",
        },
        CHAT_RESPONSE_STYLE_SETTING_KEY: {
            "description": "Workspace response style and presentation preferences",
            "default": {
                "base_style": "default",
                "warmth": 0,
                "enthusiasm": 0,
                "headings_and_lists": 0,
                "emoji": 0,
                "verbosity": 0,
            },
            "risk": "low",
        },
        CHAT_DEFAULT_RESPONSE_MODE_SETTING_KEY: {
            "description": "Default chat response mode for new conversations (fast / thinking / agentic)",
            "default": {"response_mode": "agentic"},
            "risk": "low",
        },
        "web_fetch.policy": {
            "description": "Workspace web-fetch confirmation and domain allowlist policy",
            "default": {"allow_without_confirmation": False, "allowed_domains": []},
            "risk": "high",
        },
        "research.policy": {
            "description": "Workspace source-domain allowlist for search and Deep Research",
            "default": {"allowed_domains": []},
            "risk": "high",
        },
        "access.allowlist": {
            "description": "Unified workspace allowlist (search / web fetch / outbound egress); allow_all disables interception",
            "default": {"allow_all": False, "allowed_domains": []},
            "risk": "high",
        },
        "usage.display_currency": {
            "description": "Display currency for usage views",
            "default": "CNY",
            "risk": "low",
        },
        "ui.preferences": {
            "description": "Workspace user-interface preferences",
            "default": {},
            "risk": "low",
        },
        "memory.shared_policy": {
            "description": "Workspace-wide shared-memory switch",
            "default": {"workspace_enabled": False},
            "risk": "medium",
        },
        "memory.enhancement": {
            "description": "Memory extraction, embedding recall, and summarization switches",
            "default": {
                "extraction": {
                    "enabled": False,
                    "provider_id": "",
                    "model_id": "",
                    "auto_commit": True,
                },
                "embedding": {
                    "enabled": False,
                    "provider_id": "",
                    "model_id": "",
                    "semantic_weight": 0.8,
                },
                "summarization": {
                    "enabled": False,
                    "provider_id": "",
                    "model_id": "",
                },
            },
            "risk": "medium",
        },
    }

    def list(self) -> list[WorkspaceSetting]:
        return [
            setting
            for setting in self.settings.list()
            if setting.key not in self.HIDDEN_SETTING_KEYS
        ]

    def catalog(self) -> list[dict]:
        persisted = {item.key: item for item in self.list()}
        keys = sorted(set(self.SETTING_CATALOG) | set(persisted))
        return [
            {
                "key": key,
                "value": (
                    persisted[key].value
                    if key in persisted
                    else self.SETTING_CATALOG.get(key, {}).get("default")
                ),
                "persisted": key in persisted,
                "description": self.SETTING_CATALOG.get(key, {}).get(
                    "description", "Workspace setting"
                ),
                "risk": self.SETTING_CATALOG.get(key, {}).get("risk", "medium"),
                # Secure-by-default: newly persisted internal settings are not
                # silently exposed to Agent writes until added to this catalog.
                "agent_writable": key in self.SETTING_CATALOG,
            }
            for key in keys
        ]

    def get(self, key: str) -> dict:
        if key in self.HIDDEN_SETTING_KEYS:
            raise AppError(
                403,
                "setting_managed_elsewhere",
                "This setting is managed through its dedicated masked interface",
            )
        setting = self.db.scalar(
            self.settings.query().where(WorkspaceSetting.key == key)
        )
        definition = self.SETTING_CATALOG.get(key, {})
        return {
            "key": key,
            "value": setting.value if setting is not None else definition.get("default"),
            "persisted": setting is not None,
            "description": definition.get("description", "Workspace setting"),
            "risk": definition.get("risk", "medium"),
            "agent_writable": key in self.SETTING_CATALOG,
        }

    def require_agent_writable(self, key: str) -> None:
        if key not in self.SETTING_CATALOG or key in self.HIDDEN_SETTING_KEYS:
            raise AppError(
                403,
                "agent_setting_forbidden",
                "This setting is not approved for Agent control",
                {"key": key},
            )

    def update(self, key: str, payload: SettingUpdateRequest) -> WorkspaceSetting:
        if key in self.HIDDEN_SETTING_KEYS:
            raise AppError(
                403,
                "setting_managed_elsewhere",
                "This setting is managed through its dedicated endpoint",
            )
        value = payload.value
        if key == CHAT_SUGGESTED_PROMPTS_SETTING_KEY:
            try:
                value = ChatSuggestedPromptsSettingValue.model_validate(value).model_dump()
            except ValidationError as exc:
                raise AppError(
                    422,
                    "invalid_setting_value",
                    "chat.suggested_prompts must contain only an enabled boolean",
                    {"key": key, "errors": exc.errors(include_input=False)},
                ) from exc
        elif key == CHAT_DICTATION_CLEANUP_SETTING_KEY:
            try:
                value = ChatDictationCleanupSettingValue.model_validate(value).model_dump()
            except ValidationError as exc:
                raise AppError(
                    422,
                    "invalid_setting_value",
                    "chat.dictation_cleanup must contain only an enabled boolean",
                    {"key": key, "errors": exc.errors(include_input=False)},
                ) from exc
        elif key == CHAT_CONTEXT_USAGE_SETTING_KEY:
            try:
                value = ChatContextUsageSettingValue.model_validate(value).model_dump()
            except ValidationError as exc:
                raise AppError(
                    422,
                    "invalid_setting_value",
                    "chat.context_usage must contain only an enabled boolean",
                    {"key": key, "errors": exc.errors(include_input=False)},
                ) from exc
        elif key in {
            CHAT_AUTO_TITLE_MODEL_SETTING_KEY,
            CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY,
            CHAT_DICTATION_CLEANUP_MODEL_SETTING_KEY,
        }:
            try:
                value = ChatFeatureModelSettingValue.model_validate(value).model_dump()
            except ValidationError as exc:
                raise AppError(
                    422,
                    "invalid_setting_value",
                    (
                        f"{key} must contain provider_id and model_id together, "
                        "or both null to use the chat default model"
                    ),
                    {"key": key, "errors": exc.errors(include_input=False)},
                ) from exc
        elif key == CHAT_RESPONSE_STYLE_SETTING_KEY:
            try:
                value = ChatResponseStyleSettingValue.model_validate(value).model_dump()
            except ValidationError as exc:
                raise AppError(
                    422,
                    "invalid_setting_value",
                    (
                        "chat.response_style must contain base_style and integer "
                        "levels in [-2, 2] for warmth, enthusiasm, "
                        "headings_and_lists, emoji, and verbosity"
                    ),
                    {"key": key, "errors": exc.errors(include_input=False)},
                ) from exc
        elif key == CHAT_DEFAULT_RESPONSE_MODE_SETTING_KEY:
            try:
                value = ChatDefaultResponseModeSettingValue.model_validate(
                    value
                ).model_dump()
            except ValidationError as exc:
                raise AppError(
                    422,
                    "invalid_setting_value",
                    (
                        "chat.default_response_mode must contain response_mode "
                        "as one of: fast, thinking, agentic"
                    ),
                    {"key": key, "errors": exc.errors(include_input=False)},
                ) from exc
        elif key == "web_fetch.policy":
            try:
                value = WebFetchPolicySettingValue.model_validate(value).model_dump()
            except ValidationError as exc:
                raise AppError(
                    422,
                    "invalid_setting_value",
                    (
                        "web_fetch.policy must contain allow_without_confirmation and "
                        "a list of exact DNS allowed_domains"
                    ),
                    {"key": key, "errors": exc.errors(include_input=False)},
                ) from exc
        elif key == "research.policy":
            try:
                value = ResearchPolicySettingValue.model_validate(value).model_dump()
            except ValidationError as exc:
                raise AppError(
                    422,
                    "invalid_setting_value",
                    "research.policy must contain a list of exact DNS allowed_domains",
                    {"key": key, "errors": exc.errors(include_input=False)},
                ) from exc
        elif key == "access.allowlist":
            try:
                value = AccessAllowlistSettingValue.model_validate(value).model_dump()
            except ValidationError as exc:
                raise AppError(
                    422,
                    "invalid_setting_value",
                    "access.allowlist must contain an allow_all boolean and a list "
                    "of exact DNS allowed_domains",
                    {"key": key, "errors": exc.errors(include_input=False)},
                ) from exc
        elif key == FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY:
            try:
                value = FunctionalModelDefaultsSettingValue.model_validate(
                    value
                ).model_dump(exclude_none=True)
            except ValidationError as exc:
                raise AppError(
                    422,
                    "invalid_setting_value",
                    "models.functional_defaults contains an invalid Provider/model target",
                    {"key": key, "errors": exc.errors(include_input=False)},
                ) from exc
        elif key == "memory.shared_policy":
            if (
                not isinstance(value, dict)
                or set(value) != {"workspace_enabled"}
                or not isinstance(value.get("workspace_enabled"), bool)
            ):
                raise AppError(
                    422,
                    "invalid_setting_value",
                    "memory.shared_policy requires a workspace_enabled boolean",
                )
            value = {"workspace_enabled": value["workspace_enabled"]}
        elif key == "memory.enhancement":
            from app.domain.schemas.management import MemoryEnhancementUpdateRequest
            from app.services.memory_enhancement import default_enhancement_config

            try:
                update = MemoryEnhancementUpdateRequest.model_validate(value)
            except ValidationError as exc:
                raise AppError(
                    422,
                    "invalid_setting_value",
                    "memory.enhancement contains invalid model or switch configuration",
                    {"key": key, "errors": exc.errors(include_input=False)},
                ) from exc
            normalized = default_enhancement_config()
            for section, patch in update.model_dump(exclude_none=True).items():
                normalized[section].update(patch)
            value = normalized
        setting = self.db.scalar(self.settings.query().where(WorkspaceSetting.key == key))
        if setting is None:
            setting = self.settings.add(
                WorkspaceSetting(workspace_id=self.workspace_id, key=key, value=value)
            )
        else:
            setting.value = value
        self.audit.record(actor_id=self.actor_id, action="settings.update", resource_type="setting", resource_id=key)
        self.db.commit()
        self.db.refresh(setting)
        if key == "access.allowlist":
            self._refresh_egress_policies()
        return setting

    def _refresh_egress_policies(self) -> None:
        """Re-derive sandbox egress policies after an allowlist change.

        The generic Agent egress policy and the web-fetch egress policy file are
        both derived from the unified ``access.allowlist``; regenerating them
        here makes a whitelist / allow_all change take effect for new sandbox
        sessions without waiting for the proxy reload window.
        """
        try:
            from app.core.config import get_settings
            from app.providers.factory import (
                _web_fetch_policy_domains,
                access_allow_all,
            )
            from app.services.egress_approvals import EgressApprovalService
            from app.services.sandbox import web_fetch_egress_envelope

            settings = get_settings()
            EgressApprovalService(
                self.db, self.workspace_id, settings
            ).ensure_agent_egress_policy()
            web_fetch_egress_envelope(
                settings,
                self.workspace_id,
                _web_fetch_policy_domains(self.db, self.workspace_id),
                allow_all=access_allow_all(self.db, self.workspace_id),
            )
        except Exception:
            logger.exception(
                "egress policy refresh failed after access.allowlist update "
                "for workspace %s",
                self.workspace_id,
            )
