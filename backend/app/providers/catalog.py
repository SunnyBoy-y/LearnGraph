from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


ProviderRole = Literal[
    "model",
    "image_generation",
    "vision",
    "search",
    "fetch",
    "deep_research",
    "memory",
    "transcription",
    "development",
]


@dataclass(frozen=True, slots=True)
class ProviderTypeSpec:
    """The supported Provider types and their user-facing control surface.

    This is deliberately separate from a ProviderConfig row.  A row is a
    workspace-owned configuration; a spec is a server-owned declaration of
    what that configuration may do.  The management UI consumes this catalog
    so it does not infer model discovery or probes from a string comparison.
    """

    provider_type: str
    role: ProviderRole
    label: str
    description: str
    requires_base_url: bool
    requires_secret: bool
    supports_model_discovery: bool = False
    supports_probe: bool = True
    create_allowed: bool = True
    default_base_url: str | None = None
    probe_notice: str | None = None
    brand_id: str | None = None
    brand_icon_url: str | None = None
    documentation_url: str | None = None
    key_management_url: str | None = None
    supports_account_balance: bool = False

    def view(self) -> dict[str, object]:
        return asdict(self)


PROVIDER_TYPE_SPECS: tuple[ProviderTypeSpec, ...] = (
    ProviderTypeSpec(
        provider_type="openai_responses",
        role="model",
        label="OpenAI Responses",
        description=(
            "Official OpenAI Responses API with native SSE reasoning summaries, "
            "function calls, and stateless multi-turn continuation."
        ),
        requires_base_url=True,
        requires_secret=True,
        supports_model_discovery=True,
        default_base_url="https://api.openai.com/v1",
        probe_notice=(
            "The probe calls OpenAI GET /v1/models. It does not generate content "
            "or create a Responses API conversation."
        ),
        brand_id="openai",
        brand_icon_url="https://cdn.simpleicons.org/openai",
        documentation_url="https://platform.openai.com/docs/api-reference/responses",
        key_management_url="https://platform.openai.com/api-keys",
    ),
    ProviderTypeSpec(
        provider_type="openai_compatible_chat",
        role="model",
        label="OpenAI-compatible Chat",
        description=(
            "OpenAI Chat Completions-compatible model endpoint. "
            "Use this for DeepSeek, proxy stations, and other compatible gateways; "
            "DeepSeek-specific features activate when the model family is DeepSeek."
        ),
        requires_base_url=True,
        requires_secret=True,
        supports_model_discovery=True,
        brand_id="openai_compatible",
        brand_icon_url="https://cdn.simpleicons.org/openai",
        documentation_url="https://platform.openai.com/docs/api-reference/chat",
    ),
    ProviderTypeSpec(
        provider_type="openai_images",
        role="image_generation",
        label="OpenAI Images",
        description=(
            "Official OpenAI Images API with streamed partial previews and a "
            "final generated image."
        ),
        requires_base_url=True,
        requires_secret=True,
        supports_model_discovery=True,
        default_base_url="https://api.openai.com/v1",
        probe_notice=(
            "The probe calls OpenAI GET /v1/models. It does not generate an image."
        ),
        brand_id="openai",
        brand_icon_url="https://cdn.simpleicons.org/openai",
        documentation_url="https://platform.openai.com/docs/guides/image-generation",
        key_management_url="https://platform.openai.com/api-keys",
    ),
    ProviderTypeSpec(
        provider_type="openai_compatible_vision",
        role="vision",
        label="OpenAI-compatible Vision",
        description=(
            "OpenAI Chat Completions-compatible vision model for image "
            "understanding (gpt-4o, Qwen-VL, GLM-4V, and similar gateways). "
            "Used when the primary chat model has no native image input: the "
            "server describes images first, then the text model answers."
        ),
        requires_base_url=True,
        requires_secret=True,
        supports_model_discovery=True,
        brand_id="openai_compatible",
        brand_icon_url="https://cdn.simpleicons.org/openai",
        documentation_url="https://platform.openai.com/docs/guides/images-vision",
        probe_notice=(
            "The probe calls GET /v1/models. It does not send images or generate content."
        ),
    ),
    ProviderTypeSpec(
        provider_type="openai_responses_vision",
        role="vision",
        label="OpenAI Responses Vision",
        description=(
            "Official OpenAI Responses API used only as a vision companion for "
            "image understanding when the primary chat model is text-only."
        ),
        requires_base_url=True,
        requires_secret=True,
        supports_model_discovery=True,
        default_base_url="https://api.openai.com/v1",
        brand_id="openai",
        brand_icon_url="https://cdn.simpleicons.org/openai",
        documentation_url="https://platform.openai.com/docs/guides/images-vision",
        key_management_url="https://platform.openai.com/api-keys",
        probe_notice=(
            "The probe calls OpenAI GET /v1/models. It does not generate content."
        ),
    ),
    ProviderTypeSpec(
        provider_type="deepseek_chat",
        role="model",
        label="DeepSeek",
        description=(
            "Legacy DeepSeek catalog type kept for existing workspaces. "
            "New configurations should use OpenAI-compatible Chat with the "
            "DeepSeek base URL; balance and thinking features activate when "
            "the provider model family is DeepSeek."
        ),
        requires_base_url=True,
        requires_secret=True,
        supports_model_discovery=True,
        # Prefer openai_compatible_chat + DeepSeek preset in the management UI.
        create_allowed=False,
        default_base_url="https://api.deepseek.com",
        probe_notice=(
            "The probe calls DeepSeek /models. It does not generate content or query account balance."
        ),
        brand_id="deepseek",
        brand_icon_url="https://cdn.simpleicons.org/deepseek/4D6BFE",
        documentation_url="https://api-docs.deepseek.com/",
        key_management_url="https://platform.deepseek.com/api_keys",
        supports_account_balance=True,
    ),
    ProviderTypeSpec(
        provider_type="anthropic_messages",
        role="model",
        label="Anthropic Messages",
        description=(
            "Anthropic Messages API (Claude) with SSE streaming, tool use, "
            "and extended thinking. Compatible proxy stations may require "
            "custom request headers."
        ),
        requires_base_url=True,
        requires_secret=True,
        supports_model_discovery=True,
        default_base_url="https://api.anthropic.com",
        probe_notice=(
            "The probe calls Anthropic GET /v1/models. It does not generate content."
        ),
        brand_id="anthropic",
        brand_icon_url="https://cdn.simpleicons.org/anthropic",
        documentation_url="https://docs.anthropic.com/en/api/messages",
        key_management_url="https://console.anthropic.com/settings/keys",
    ),
    ProviderTypeSpec(
        provider_type="anysearch",
        role="search",
        label="AnySearch",
        description=(
            "AnySearch MCP-compatible real-time web search. "
            "The server sends requests only to its official API endpoint."
        ),
        requires_base_url=True,
        requires_secret=True,
        default_base_url="https://api.anysearch.com/mcp",
        probe_notice=(
            "The probe invokes AnySearch's bounded MCP search tool and may use quota."
        ),
        brand_id="anysearch",
        brand_icon_url="https://www.anysearch.com/favicon.ico",
        documentation_url="https://www.anysearch.com/home",
        key_management_url="https://anysearch.com/console/api-keys",
    ),
    ProviderTypeSpec(
        provider_type="searxng",
        role="search",
        label="SearXNG",
        description="Self-hosted SearXNG JSON search endpoint.",
        requires_base_url=True,
        requires_secret=False,
        probe_notice="The probe sends one bounded JSON search request.",
    ),
    ProviderTypeSpec(
        provider_type="tavily",
        role="search",
        label="Tavily Search",
        description="Tavily cloud web search endpoint.",
        requires_base_url=True,
        requires_secret=True,
        probe_notice="The probe sends one bounded search request and may be billable.",
    ),
    ProviderTypeSpec(
        provider_type="exa",
        role="search",
        label="Exa Search",
        description="Exa cloud web search endpoint.",
        requires_base_url=True,
        requires_secret=True,
        probe_notice="The probe sends one bounded search request and may be billable.",
    ),
    ProviderTypeSpec(
        provider_type="brave_search",
        role="search",
        label="Brave Search",
        description="Brave Search API endpoint.",
        requires_base_url=True,
        requires_secret=True,
        probe_notice="The probe sends one bounded search request and may be billable.",
    ),
    ProviderTypeSpec(
        provider_type="firecrawl_search",
        role="search",
        label="Firecrawl Search",
        description="Firecrawl web search endpoint.",
        requires_base_url=True,
        requires_secret=True,
        probe_notice="The probe sends one bounded search request and may be billable.",
    ),
    ProviderTypeSpec(
        provider_type="crawl4ai_http",
        role="fetch",
        label="Crawl4AI HTTP",
        description="Self-hosted Crawl4AI HTTP bridge for authorized page fetches.",
        requires_base_url=True,
        requires_secret=False,
        probe_notice="The probe fetches https://example.com through the configured bridge.",
    ),
    ProviderTypeSpec(
        provider_type="firecrawl_fetch",
        role="fetch",
        label="Firecrawl Fetch",
        description="Firecrawl scrape endpoint for authorized page fetches.",
        requires_base_url=True,
        requires_secret=True,
        probe_notice="The probe fetches https://example.com and may be billable.",
    ),
    ProviderTypeSpec(
        provider_type="deep_research_http",
        role="deep_research",
        label="Deep Research HTTP",
        description="Provider-neutral asynchronous Deep Research task endpoint.",
        requires_base_url=True,
        requires_secret=True,
        probe_notice="The probe calls the provider-neutral GET /health endpoint without creating a task.",
    ),
    ProviderTypeSpec(
        provider_type="openai_compatible_transcription",
        role="transcription",
        label="OpenAI-compatible ASR",
        description="OpenAI-compatible Audio Transcriptions endpoint for stored audio files.",
        requires_base_url=True,
        requires_secret=True,
        supports_model_discovery=False,
        supports_probe=False,
    ),
    ProviderTypeSpec(
        provider_type="mem0_platform",
        role="memory",
        label="Mem0 Platform",
        description="Mem0 Platform v3 workspace memory endpoint.",
        requires_base_url=True,
        requires_secret=True,
    ),
    ProviderTypeSpec(
        provider_type="local_mock",
        role="development",
        label="Local demonstration provider",
        description="Explicit development-only deterministic provider; never a remote fallback.",
        requires_base_url=False,
        requires_secret=False,
        supports_model_discovery=True,
        create_allowed=False,
    ),
)

PROVIDER_TYPE_BY_ID: dict[str, ProviderTypeSpec] = {
    item.provider_type: item for item in PROVIDER_TYPE_SPECS
}

MODEL_PROVIDER_TYPES = frozenset(
    item.provider_type for item in PROVIDER_TYPE_SPECS if item.role == "model"
)
IMAGE_GENERATION_PROVIDER_TYPES = frozenset(
    item.provider_type
    for item in PROVIDER_TYPE_SPECS
    if item.role == "image_generation"
)
VISION_PROVIDER_TYPES = frozenset(
    item.provider_type for item in PROVIDER_TYPE_SPECS if item.role == "vision"
)
MEMORY_PROVIDER_TYPES = frozenset(
    item.provider_type for item in PROVIDER_TYPE_SPECS if item.role == "memory"
)
SEARCH_PROVIDER_TYPES = frozenset(
    item.provider_type for item in PROVIDER_TYPE_SPECS if item.role == "search"
)
FETCH_PROVIDER_TYPES = frozenset(
    item.provider_type for item in PROVIDER_TYPE_SPECS if item.role == "fetch"
)
DEEP_RESEARCH_PROVIDER_TYPES = frozenset(
    item.provider_type
    for item in PROVIDER_TYPE_SPECS
    if item.role == "deep_research"
)
TRANSCRIPTION_PROVIDER_TYPES = frozenset(
    item.provider_type for item in PROVIDER_TYPE_SPECS if item.role == "transcription"
)


def provider_type_spec(provider_type: str) -> ProviderTypeSpec | None:
    return PROVIDER_TYPE_BY_ID.get(provider_type)


def provider_catalog(*, include_development: bool = False) -> list[dict[str, object]]:
    return [
        item.view()
        for item in PROVIDER_TYPE_SPECS
        if include_development or item.role != "development"
    ]
