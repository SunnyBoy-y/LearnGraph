from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.secret_store import SecretStoreUnavailable, secret_store_from_settings
from app.domain.models import ProviderConfig, ProviderSecret, Workspace
from app.providers.local.memory import LocalWorkspaceMemoryProvider
from app.providers.local.model import LocalDemoModelProvider, UnavailableModelProvider
from app.providers.catalog import (
    DEEP_RESEARCH_PROVIDER_TYPES,
    FETCH_PROVIDER_TYPES,
    IMAGE_GENERATION_PROVIDER_TYPES,
    MEMORY_PROVIDER_TYPES,
    MODEL_PROVIDER_TYPES,
    SEARCH_PROVIDER_TYPES,
    TRANSCRIPTION_PROVIDER_TYPES,
    VISION_PROVIDER_TYPES,
)
from app.providers.ports.fetch import FetchProviderPort
from app.providers.ports.image_generation import ImageGenerationProviderPort
from app.providers.ports.model import ModelProviderPort
from app.providers.ports.memory import MemoryProviderPort
from app.providers.ports.research import DeepResearchProviderPort
from app.providers.ports.search import SearchProviderPort
from app.providers.ports.transcription import TranscriptionProviderPort
from app.providers.remote.transcription import OpenAICompatibleTranscriptionProvider
from app.providers.remote.anysearch import AnySearchSearchProvider
from app.providers.model_options import (
    ModelCapabilityError,
    model_capabilities_for_model,
    resolve_model_call_options,
)
from app.providers.remote.deepseek import (
    DeepSeekChatProvider,
    is_deepseek_chat_configuration,
)
from app.providers.remote.openai import (
    OpenAICompatibleChatProvider,
    OpenAIResponsesProvider,
    normalize_openai_api_base_url,
)
from app.providers.remote.anthropic import AnthropicMessagesProvider, normalize_anthropic_api_base_url
from app.providers.remote.images import (
    OpenAIImagesProvider,
    UnavailableImageGenerationProvider,
)
from app.providers.remote.research import (
    HTTPDeepResearchProvider,
    UnavailableDeepResearchProvider,
)
from app.providers.remote.fetch import (
    Crawl4AIHTTPFetchProvider,
    FirecrawlFetchProvider,
    UnavailableFetchProvider,
)
from app.providers.remote.search import SearXNGSearchProvider, UnavailableSearchProvider
from app.providers.remote.memory import (
    Mem0PlatformAdapter,
    UnavailableMemoryProvider,
    mem0_entity_id,
)
from app.services.provider_secrets import decrypt_provider_secret


def _secret_for_provider(
    db: Session,
    workspace_id: str,
    provider: ProviderConfig,
    settings: Settings,
) -> str | None:
    secret_record = db.scalar(
        select(ProviderSecret).where(
            ProviderSecret.workspace_id == workspace_id,
            ProviderSecret.provider_id == provider.id,
        )
    )
    if secret_record is None:
        return None
    return decrypt_provider_secret(settings, secret_record)


def model_provider_for_workspace(
    db: Session,
    workspace_id: str,
    settings: Settings,
    model_id: str | None = None,
    thinking_mode: str | None = None,
    search_route: str | None = None,
    provider_id: str | None = None,
) -> ModelProviderPort:
    statement = select(ProviderConfig).where(
        ProviderConfig.workspace_id == workspace_id,
        ProviderConfig.enabled.is_(True),
        ProviderConfig.provider_type.in_(MODEL_PROVIDER_TYPES),
    )
    if provider_id:
        statement = statement.where(ProviderConfig.id == provider_id)
    provider = db.scalar(statement.order_by(ProviderConfig.updated_at.desc()))
    if provider_id and provider is None:
        return UnavailableModelProvider(
            "The selected model provider is not enabled in this workspace",
            provider_id=provider_id,
            model_id=model_id or "unavailable",
        )
    if provider is not None:
        selected_model_id = model_id.strip() if model_id else ""
        capabilities = dict(provider.capabilities or {})
        configured_model_id = str(capabilities.get("default_model") or "").strip()
        resolved_model_id = selected_model_id or configured_model_id
        model_states = capabilities.get("model_states")
        if (
            isinstance(model_states, dict)
            and model_states.get(resolved_model_id) is False
        ):
            return UnavailableModelProvider(
                "The selected model is disabled for this Provider",
                provider_id=provider.id,
                model_id=resolved_model_id or "unavailable",
            )
        is_deepseek = (
            is_deepseek_chat_configuration(
                provider.provider_type,
                provider.base_url,
            )
            or provider.provider_type == "deepseek_chat"
            or _model_family_is_deepseek(resolved_model_id, capabilities)
        )
        if is_deepseek:
            # Provider-level defaults are protocol facts, not a hard-coded
            # model catalogue. Individual discovered models may still override
            # these values through their versioned capability snapshot.
            capabilities = {
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
                "default_search_route": "disabled",
                **capabilities,
            }
        if not provider.remote_capability:
            return UnavailableModelProvider(
                "The enabled remote model provider has not declared remote capability",
                provider_id=provider.id,
                model_id=resolved_model_id or "unavailable",
            )
        if not provider.base_url:
            return UnavailableModelProvider(
                "The enabled model provider has no base URL",
                provider_id=provider.id,
                model_id=resolved_model_id or "unavailable",
            )
        if not resolved_model_id:
            return UnavailableModelProvider(
                "The enabled model provider has no default model",
                provider_id=provider.id,
            )
        try:
            api_key = _secret_for_provider(db, workspace_id, provider, settings)
        except Exception:
            return UnavailableModelProvider(
                "The enabled model provider secret cannot be decrypted",
                provider_id=provider.id,
                model_id=resolved_model_id,
            )
        if not api_key:
            return UnavailableModelProvider(
                "The enabled model provider has no usable encrypted secret",
                provider_id=provider.id,
                model_id=resolved_model_id,
            )
        if provider.provider_type == "openai_responses":
            effective_base_url = normalize_openai_api_base_url(provider.base_url)
        elif provider.provider_type == "anthropic_messages":
            effective_base_url = normalize_anthropic_api_base_url(provider.base_url)
        else:
            effective_base_url = provider.base_url
        extra_headers = _extra_headers_from_capabilities(capabilities)
        effective_model_capabilities = model_capabilities_for_model(
            capabilities,
            resolved_model_id,
        )
        context_window_tokens = int(
            effective_model_capabilities.get("context_window_tokens") or 256_000
        )
        context_limit_tokens = min(
            context_window_tokens,
            int(
                effective_model_capabilities.get("context_limit_tokens")
                or context_window_tokens
            ),
        )
        common = {
            "provider_id": provider.id,
            # Keep the persisted provider type unchanged for legacy workspaces,
            # but make the adapter trace the effective protocol truth.
            "provider_type": provider.provider_type,
            "model_id": resolved_model_id,
            "base_url": effective_base_url,
            "api_key": api_key,
            # The adapter sees the user-selected cap as its usable context
            # window. The physical vendor limit remains in the capability
            # snapshot and is never exceeded by a per-model override.
            "context_window_tokens": context_limit_tokens,
            "max_output_tokens": int(
                effective_model_capabilities.get("max_output_tokens") or 4_096
            ),
            "extra_headers": extra_headers,
        }
        common["supports_image_input"] = (
            effective_model_capabilities.get("supports_image_input") is True
        )
        image_mode = effective_model_capabilities.get("image_input_mode") or "auto"
        if image_mode not in {"native", "external_vision", "auto"}:
            image_mode = "auto"
        common["image_input_mode"] = image_mode
        # Expose a shallow capability snapshot so ChatService can re-resolve
        # image routing without another DB round-trip.
        common["capabilities"] = {
            "supports_image_input": common["supports_image_input"],
            "image_input_mode": image_mode,
            "context_window_tokens": context_window_tokens,
            "context_limit_tokens": context_limit_tokens,
            "max_output_tokens": common["max_output_tokens"],
        }
        try:
            common["call_options"] = resolve_model_call_options(
                capabilities,
                resolved_model_id,
                thinking_mode=thinking_mode,
                search_route=search_route,
            )
        except ModelCapabilityError as exc:
            return UnavailableModelProvider(
                str(exc),
                provider_id=provider.id,
                model_id=resolved_model_id,
            )
        if provider.provider_type == "openai_responses":
            return OpenAIResponsesProvider(**common)
        if provider.provider_type == "anthropic_messages":
            return AnthropicMessagesProvider(**common)
        if provider.provider_type == "deepseek_chat" or (
            provider.provider_type == "openai_compatible_chat" and is_deepseek
        ):
            return DeepSeekChatProvider(**common)
        if provider.provider_type == "openai_compatible_chat":
            structured_output_mode = capabilities.get("structured_output_mode")
            if structured_output_mode not in {None, "json_object", "json_schema"}:
                return UnavailableModelProvider(
                    "The enabled model provider has an unsupported structured output mode",
                    provider_id=provider.id,
                    model_id=resolved_model_id,
                )
            return OpenAICompatibleChatProvider(
                **common,
                structured_output_mode=structured_output_mode,
                supports_structured_chat=True,
            )

    local_provider = db.scalar(
        select(ProviderConfig).where(
            ProviderConfig.workspace_id == workspace_id,
            ProviderConfig.enabled.is_(True),
            ProviderConfig.provider_type == "local_mock",
        ).order_by(ProviderConfig.updated_at.desc())
    )
    if local_provider is not None and settings.enable_local_demo_provider:
        selected_model_id = model_id.strip() if model_id else ""
        if selected_model_id and selected_model_id != LocalDemoModelProvider.model_id:
            return UnavailableModelProvider(
                "The requested model is not available because no remote model provider is enabled",
                model_id=selected_model_id,
            )
        return LocalDemoModelProvider()
    return UnavailableModelProvider(
        "No model provider is enabled for this workspace"
    )


def _extra_headers_from_capabilities(capabilities: dict) -> dict[str, str]:
    raw = capabilities.get("extra_headers") or capabilities.get("request_headers")
    if not isinstance(raw, dict):
        return {}
    headers: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        text = str(value).strip() if value is not None else ""
        if not name or not text:
            continue
        if name.casefold() in {
            "authorization",
            "x-api-key",
            "api-key",
            "proxy-authorization",
        }:
            continue
        headers[name] = text
    return headers


def _model_family_is_deepseek(model_id: str | None, capabilities: dict) -> bool:
    """Detect DeepSeek model family so balance/thinking features can activate.

    DeepSeek no longer needs a separate protocol type: the OpenAI-compatible
    Chat adapter is used, and DeepSeek-specific behaviour keys off the model
    family (or an explicit capability flag) rather than a separate catalog row.
    """

    if capabilities.get("model_family") == "deepseek":
        return True
    if capabilities.get("brand_id") == "deepseek":
        return True
    if not model_id:
        return False
    lowered = model_id.strip().casefold()
    return lowered.startswith("deepseek") or "deepseek" in lowered.split("/")[-1]


def image_provider_for_workspace(
    db: Session,
    workspace_id: str,
    settings: Settings,
    model_id: str | None = None,
    provider_id: str | None = None,
) -> ImageGenerationProviderPort:
    statement = select(ProviderConfig).where(
        ProviderConfig.workspace_id == workspace_id,
        ProviderConfig.enabled.is_(True),
        ProviderConfig.provider_type.in_(IMAGE_GENERATION_PROVIDER_TYPES),
    )
    if provider_id:
        statement = statement.where(ProviderConfig.id == provider_id)
    provider = db.scalar(statement.order_by(ProviderConfig.updated_at.desc()))
    selected_model_id = model_id.strip() if model_id else ""
    if provider is None:
        return UnavailableImageGenerationProvider(
            (
                "The selected image generation provider is not enabled in this workspace"
                if provider_id
                else "No image generation provider is enabled for this workspace"
            ),
            provider_id=provider_id or "unavailable",
            model_id=selected_model_id or "unavailable",
        )

    capabilities = dict(provider.capabilities or {})
    configured_model_id = str(
        capabilities.get("default_image_generation_model_id") or ""
    ).strip()
    resolved_model_id = selected_model_id or configured_model_id

    def unavailable(reason: str) -> UnavailableImageGenerationProvider:
        return UnavailableImageGenerationProvider(
            reason,
            provider_id=provider.id,
            model_id=resolved_model_id or "unavailable",
        )

    configured_model_ids = {
        str(item).strip()
        for item in capabilities.get("discovered_model_ids", [])
        if isinstance(item, str) and item.strip()
    }
    if configured_model_id:
        configured_model_ids.add(configured_model_id)
    raw_model_states = capabilities.get("model_states")
    model_states = (
        raw_model_states
        if isinstance(raw_model_states, dict)
        else {}
    )
    enabled_model_ids = {
        item for item in configured_model_ids if model_states.get(item, True) is not False
    }
    if resolved_model_id and resolved_model_id not in enabled_model_ids:
        return unavailable(
            "The selected image generation model is not configured and enabled "
            "for this workspace Provider"
        )
    if not provider.remote_capability:
        return unavailable(
            "The enabled image generation provider has not declared remote capability"
        )
    if not provider.base_url:
        return unavailable("The enabled image generation provider has no base URL")
    if not resolved_model_id:
        return unavailable(
            "The enabled image generation provider has no default image generation model"
        )
    try:
        api_key = _secret_for_provider(db, workspace_id, provider, settings)
    except Exception:
        return unavailable(
            "The enabled image generation provider secret cannot be decrypted"
        )
    if not api_key:
        return unavailable(
            "The enabled image generation provider has no usable encrypted secret"
        )
    output_format = str(capabilities.get("image_output_format") or "png").strip()
    try:
        timeout_seconds = float(
            capabilities.get("image_generation_timeout_seconds") or 180.0
        )
    except (TypeError, ValueError):
        return unavailable(
            "The enabled image generation provider has an invalid timeout configuration"
        )
    if not 1.0 <= timeout_seconds <= 600.0:
        return unavailable(
            "The enabled image generation provider timeout must be between 1 and 600 seconds"
        )
    try:
        return OpenAIImagesProvider(
            provider_id=provider.id,
            model_id=resolved_model_id,
            base_url=provider.base_url,
            api_key=api_key,
            output_format=output_format,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as exc:
        return unavailable(str(exc))


def vision_provider_for_workspace(
    db: Session,
    workspace_id: str,
    settings: Settings,
    model_id: str | None = None,
    provider_id: str | None = None,
) -> ModelProviderPort:
    """Resolve an enabled vision companion for describe-then-answer turns.

    Vision Providers reuse the chat adapters with ``supports_image_input=True``
    forced.  They are never a silent fallback for ordinary text chat — only
    ChatService image orchestration may call this helper.
    """

    statement = select(ProviderConfig).where(
        ProviderConfig.workspace_id == workspace_id,
        ProviderConfig.enabled.is_(True),
        ProviderConfig.provider_type.in_(VISION_PROVIDER_TYPES),
    )
    if provider_id:
        statement = statement.where(ProviderConfig.id == provider_id)
    provider = db.scalar(statement.order_by(ProviderConfig.updated_at.desc()))
    selected_model_id = model_id.strip() if model_id else ""
    if provider is None:
        return UnavailableModelProvider(
            (
                "The selected vision provider is not enabled in this workspace"
                if provider_id
                else "No vision provider is enabled for this workspace"
            ),
            provider_id=provider_id or "unavailable",
            model_id=selected_model_id or "unavailable",
        )

    capabilities = dict(provider.capabilities or {})
    configured_model_id = str(
        capabilities.get("default_vision_model_id")
        or capabilities.get("default_model")
        or ""
    ).strip()
    resolved_model_id = selected_model_id or configured_model_id

    def unavailable(reason: str) -> UnavailableModelProvider:
        return UnavailableModelProvider(
            reason,
            provider_id=provider.id,
            model_id=resolved_model_id or "unavailable",
        )

    if not provider.remote_capability:
        return unavailable(
            "The enabled vision provider has not declared remote capability"
        )
    if not provider.base_url:
        return unavailable("The enabled vision provider has no base URL")
    if not resolved_model_id:
        return unavailable(
            "The enabled vision provider has no default vision model"
        )
    try:
        api_key = _secret_for_provider(db, workspace_id, provider, settings)
    except Exception:
        return unavailable("The enabled vision provider secret cannot be decrypted")
    if not api_key:
        return unavailable(
            "The enabled vision provider has no usable encrypted secret"
        )

    if provider.provider_type == "openai_responses_vision":
        effective_base_url = normalize_openai_api_base_url(provider.base_url)
    else:
        effective_base_url = provider.base_url
    extra_headers = _extra_headers_from_capabilities(capabilities)
    common = {
        "provider_id": provider.id,
        "provider_type": provider.provider_type,
        "model_id": resolved_model_id,
        "base_url": effective_base_url,
        "api_key": api_key,
        "context_window_tokens": int(capabilities.get("context_window_tokens") or 128_000),
        "max_output_tokens": int(capabilities.get("max_output_tokens") or 2_048),
        "extra_headers": extra_headers,
        # Vision companions always accept image parts; the catalog role is the
        # capability source, not a per-model user toggle on the primary LLM.
        "supports_image_input": True,
        "image_input_mode": "native",
        "capabilities": {
            "supports_image_input": True,
            "image_input_mode": "native",
        },
    }
    try:
        common["call_options"] = resolve_model_call_options(
            {
                **capabilities,
                "reasoning_efforts": capabilities.get("reasoning_efforts") or [],
                "thinking_mapping": capabilities.get("thinking_mapping")
                or {"off": None},
                "default_thinking_mode": "off",
                "hosted_web_search": False,
                "default_search_route": "disabled",
            },
            resolved_model_id,
            thinking_mode="off",
            search_route="disabled",
        )
    except ModelCapabilityError as exc:
        return unavailable(str(exc))

    if provider.provider_type == "openai_responses_vision":
        return OpenAIResponsesProvider(**common)
    if provider.provider_type == "openai_compatible_vision":
        structured_output_mode = capabilities.get("structured_output_mode")
        if structured_output_mode not in {None, "json_object", "json_schema"}:
            return unavailable(
                "The enabled vision provider has an unsupported structured output mode"
            )
        return OpenAICompatibleChatProvider(
            **common,
            structured_output_mode=structured_output_mode,
            supports_structured_chat=True,
        )
    return unavailable(
        f"Unsupported vision provider type: {provider.provider_type}"
    )


def search_provider_for_workspace(
    db: Session,
    workspace_id: str,
    settings: Settings,
    route: str | None = None,
) -> SearchProviderPort | None:
    if route in {"disabled", "model_native"}:
        return None
    provider_types = SEARCH_PROVIDER_TYPES
    if route == "local":
        provider_types = {"searxng"}
    elif route == "external":
        provider_types = SEARCH_PROVIDER_TYPES - {"searxng"}
    providers = list(db.scalars(
        select(ProviderConfig)
        .where(
            ProviderConfig.workspace_id == workspace_id,
            ProviderConfig.enabled.is_(True),
            ProviderConfig.remote_capability.is_(True),
            ProviderConfig.provider_type.in_(provider_types),
        )
        .order_by(ProviderConfig.updated_at.desc())
    ).all())
    provider = providers[0] if providers else None
    if route == "auto" and len(providers) > 1:
        explicit = [
            item
            for item in providers
            if (item.capabilities or {}).get("auto_search_enabled") is True
        ]
        if explicit:
            provider = sorted(
                explicit,
                key=lambda item: int((item.capabilities or {}).get("auto_search_order") or 100),
            )[0]
        else:
            boundaries = {
                "local" if item.provider_type == "searxng" else "cloud"
                for item in providers
            }
            if len(boundaries) > 1:
                return UnavailableSearchProvider(
                    "route_unconfigured",
                    "Auto search crosses local/cloud boundaries and requires an explicit authorized chain",
                )
    if provider is None:
        return UnavailableSearchProvider(
            "unconfigured",
            f"No enabled SearchProvider matches route '{route or 'configured'}'",
        )
    if not provider.base_url:
        return UnavailableSearchProvider(provider.id, "Configured SearchProvider has no base URL")
    try:
        api_key = _secret_for_provider(db, workspace_id, provider, settings)
    except Exception:
        return UnavailableSearchProvider(provider.id, "Configured SearchProvider secret cannot be decrypted")
    from app.providers.remote.search import CloudSearchProvider

    if provider.provider_type == "searxng":
        return SearXNGSearchProvider(
            provider_id=provider.id,
            base_url=provider.base_url,
            api_key=api_key,
        )
    if not api_key:
        return UnavailableSearchProvider(provider.id, "Configured cloud SearchProvider has no encrypted secret")
    if provider.provider_type == "anysearch":
        try:
            return AnySearchSearchProvider(
                provider_id=provider.id,
                base_url=provider.base_url,
                api_key=api_key,
            )
        except ValueError as exc:
            return UnavailableSearchProvider(provider.id, str(exc))
    return CloudSearchProvider(
        provider_id=provider.id,
        provider_type=provider.provider_type,
        base_url=provider.base_url,
        api_key=api_key,
    )


def transcription_provider_for_workspace(
    db: Session,
    workspace_id: str,
    settings: Settings,
    *,
    provider_id: str | None = None,
    model_id: str | None = None,
) -> TranscriptionProviderPort | None:
    statement = select(ProviderConfig).where(
        ProviderConfig.workspace_id == workspace_id,
        ProviderConfig.enabled.is_(True),
        ProviderConfig.remote_capability.is_(True),
        ProviderConfig.provider_type.in_(TRANSCRIPTION_PROVIDER_TYPES),
    )
    if provider_id:
        statement = statement.where(ProviderConfig.id == provider_id)
    provider = db.scalar(statement.order_by(ProviderConfig.updated_at.desc()))
    if provider is None or not provider.base_url:
        return None
    capabilities = dict(provider.capabilities or {})
    resolved_model = (
        (model_id or "").strip()
        or str(capabilities.get("default_transcription_model_id") or "").strip()
    )
    if not resolved_model:
        return None
    try:
        api_key = _secret_for_provider(db, workspace_id, provider, settings)
    except Exception:
        return None
    if not api_key:
        return None
    return OpenAICompatibleTranscriptionProvider(
        provider_id=provider.id,
        model_id=resolved_model,
        base_url=provider.base_url,
        api_key=api_key,
        timeout_seconds=float(capabilities.get("transcription_timeout_seconds") or 180),
    )


def deep_research_provider_for_workspace(
    db: Session,
    workspace_id: str,
    settings: Settings,
) -> DeepResearchProviderPort | None:
    provider = db.scalar(
        select(ProviderConfig)
        .where(
            ProviderConfig.workspace_id == workspace_id,
            ProviderConfig.enabled.is_(True),
            ProviderConfig.remote_capability.is_(True),
            ProviderConfig.provider_type.in_(DEEP_RESEARCH_PROVIDER_TYPES),
        )
        .order_by(ProviderConfig.updated_at.desc())
    )
    if provider is None:
        return None
    if not provider.base_url:
        return UnavailableDeepResearchProvider(provider.id, "Configured research provider has no base URL")
    try:
        api_key = _secret_for_provider(db, workspace_id, provider, settings)
    except Exception:
        return UnavailableDeepResearchProvider(provider.id, "Configured research secret cannot be decrypted")
    if not api_key:
        return UnavailableDeepResearchProvider(provider.id, "Configured research provider has no encrypted secret")
    return HTTPDeepResearchProvider(
        provider_id=provider.id,
        base_url=provider.base_url,
        api_key=api_key,
        declared_capabilities=provider.capabilities,
    )


def fetch_provider_for_workspace(
    db: Session,
    workspace_id: str,
    settings: Settings,
) -> FetchProviderPort | None:
    provider = db.scalar(
        select(ProviderConfig)
        .where(
            ProviderConfig.workspace_id == workspace_id,
            ProviderConfig.enabled.is_(True),
            ProviderConfig.remote_capability.is_(True),
            ProviderConfig.provider_type.in_(FETCH_PROVIDER_TYPES),
        )
        .order_by(ProviderConfig.updated_at.desc())
    )
    if provider is None:
        return None
    if not provider.base_url:
        return UnavailableFetchProvider(provider.id, "Configured Crawl4AI provider has no base URL")
    try:
        api_key = _secret_for_provider(db, workspace_id, provider, settings)
    except Exception:
        return UnavailableFetchProvider(provider.id, "Configured Crawl4AI secret cannot be decrypted")
    if provider.provider_type == "firecrawl_fetch":
        if not api_key:
            return UnavailableFetchProvider(provider.id, "Configured Firecrawl provider has no encrypted secret")
        return FirecrawlFetchProvider(
            provider_id=provider.id,
            base_url=provider.base_url,
            api_key=api_key,
        )
    return Crawl4AIHTTPFetchProvider(
        provider_id=provider.id,
        base_url=provider.base_url,
        api_key=api_key,
    )


def memory_provider_for_workspace(
    db: Session,
    workspace: Workspace,
    user_id: str,
    settings: Settings,
) -> MemoryProviderPort:
    provider = db.scalar(
        select(ProviderConfig)
        .where(
            ProviderConfig.workspace_id == workspace.id,
            ProviderConfig.enabled.is_(True),
            ProviderConfig.provider_type.in_(MEMORY_PROVIDER_TYPES),
        )
        .order_by(ProviderConfig.updated_at.desc())
    )
    if provider is None:
        return LocalWorkspaceMemoryProvider(settings.memory_root, workspace.id)
    if not provider.remote_capability:
        return UnavailableMemoryProvider(
            provider.id,
            "The enabled Mem0 Platform provider has not declared remote capability",
        )
    if not provider.base_url:
        return UnavailableMemoryProvider(
            provider.id,
            "The enabled Mem0 Platform provider has no base URL",
        )
    try:
        api_key = _secret_for_provider(db, workspace.id, provider, settings)
    except Exception:
        return UnavailableMemoryProvider(
            provider.id,
            "The enabled Mem0 Platform secret cannot be decrypted",
        )
    if not api_key:
        return UnavailableMemoryProvider(
            provider.id,
            "The enabled Mem0 Platform provider has no encrypted secret",
        )
    try:
        identity_key = secret_store_from_settings(settings).identity_key(create=True)
    except SecretStoreUnavailable:
        return UnavailableMemoryProvider(
            provider.id,
            "The Mem0 Platform secret store identity key is unavailable",
        )
    return Mem0PlatformAdapter(
        provider_id=provider.id,
        base_url=provider.base_url,
        api_key=api_key,
        workspace_entity=mem0_entity_id(
            tenant_id=workspace.tenant_id,
            user_id=user_id,
            workspace_id=workspace.id,
            secret=identity_key,
        ),
    )
