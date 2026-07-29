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
        documentation_url="https://platform.openai.com/docs/api-reference/chat",
    ),
    ProviderTypeSpec(
        provider_type="qwen",
        role="model",
        label="阿里云千问（Qwen）",
        description=(
            "千问 AI 平台 OpenAI-compatible Chat，支持 reasoning_content 流式思考、"
            "按模型映射 enable_thinking / thinking_budget / reasoning_effort，"
            "并标注原生搜索、网页抓取、图片搜索及图像/视频理解能力。"
        ),
        requires_base_url=True,
        requires_secret=True,
        supports_model_discovery=True,
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        probe_notice=(
            "探测仅调用 GET /models，不生成内容，也不会触发搜索、抓取或多模态计费。"
        ),
        brand_id="qwen",
        brand_icon_url="https://models.dev/logos/alibaba.svg",
        documentation_url="https://help.aliyun.com/zh/model-studio/",
        key_management_url="https://bailian.console.aliyun.com/?tab=model#/api-key",
    ),
    ProviderTypeSpec(
        provider_type="codex_chatgpt",
        role="model",
        label="Codex 官方直登（ChatGPT 订阅）",
        description=(
            "使用 ChatGPT 账号直登 Codex 后端（chatgpt.com/backend-api/codex），"
            "按 ChatGPT 订阅计划计费而非 API 额度。支持 Responses 协议流式推理、"
            "工具调用与推理续写，并可查询 5 小时 / 每周滚动用量。"
        ),
        requires_base_url=True,
        requires_secret=True,
        # Codex has no public GET /models; discovery returns the reviewed
        # ChatGPT-auth catalog bundled with the adapter (terra/luna/5.5/…).
        supports_model_discovery=True,
        supports_probe=False,
        default_base_url="https://chatgpt.com/backend-api/codex",
        probe_notice=(
            "该接入使用 Codex CLI 的 OAuth 令牌，属于 OpenAI 未公开的内部端点，"
            "接口形态可能随 Codex 版本变化；请确认你的账号计划允许此用法。"
            "「发现模型」会载入内置的 ChatGPT 直登可用目录（默认 gpt-5.6-terra），"
            "不会向 Codex 后端发探测请求。部分文档中的模型（如 gpt-5.6-sol）"
            "在免费 ChatGPT 账号上会被后端拒绝。"
        ),
        brand_id="openai",
        documentation_url="https://learn.chatgpt.com/docs/auth",
        key_management_url="https://chatgpt.com/codex/settings",
        supports_account_balance=True,
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
        documentation_url="https://platform.claude.com/docs/en/api/overview",
        key_management_url="https://platform.claude.com/settings/keys",
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
        brand_id="searxng",
        brand_icon_url="https://cdn.simpleicons.org/searxng",
        documentation_url="https://docs.searxng.org",
    ),
    ProviderTypeSpec(
        provider_type="tavily",
        role="search",
        label="Tavily Search",
        description="Tavily cloud web search endpoint.",
        requires_base_url=True,
        requires_secret=True,
        default_base_url="https://api.tavily.com",
        probe_notice="The probe sends one bounded search request and may be billable.",
        brand_id="tavily",
        brand_icon_url="https://tavily.com/favicon.ico",
        documentation_url="https://docs.tavily.com",
        key_management_url="https://app.tavily.com",
    ),
    ProviderTypeSpec(
        provider_type="exa",
        role="search",
        label="Exa Search",
        description="Exa cloud web search endpoint.",
        requires_base_url=True,
        requires_secret=True,
        default_base_url="https://api.exa.ai",
        probe_notice="The probe sends one bounded search request and may be billable.",
        brand_id="exa",
        brand_icon_url="https://exa.ai/favicon.ico",
        documentation_url="https://exa.ai/docs",
        key_management_url="https://dashboard.exa.ai/api-keys",
    ),
    ProviderTypeSpec(
        provider_type="brave_search",
        role="search",
        label="Brave Search",
        description="Brave Search API endpoint.",
        requires_base_url=True,
        requires_secret=True,
        # The search client appends /res/v1/web/search; keep the bare API origin here.
        default_base_url="https://api.search.brave.com",
        probe_notice="The probe sends one bounded search request and may be billable.",
        brand_id="brave",
        brand_icon_url="https://cdn.simpleicons.org/brave",
        documentation_url="https://api-dashboard.search.brave.com/app/documentation",
        key_management_url="https://api-dashboard.search.brave.com/app/keys",
    ),
    ProviderTypeSpec(
        provider_type="firecrawl_search",
        role="search",
        label="Firecrawl Search",
        description="Firecrawl web search endpoint.",
        requires_base_url=True,
        requires_secret=True,
        # The search client appends /v1/search; keep the bare API origin here.
        default_base_url="https://api.firecrawl.dev",
        probe_notice="The probe sends one bounded search request and may be billable.",
        brand_id="firecrawl",
        brand_icon_url="https://www.firecrawl.dev/favicon.ico",
        documentation_url="https://docs.firecrawl.dev",
        key_management_url="https://www.firecrawl.dev/app/api-keys",
    ),
    ProviderTypeSpec(
        provider_type="crawl4ai_http",
        role="fetch",
        label="Crawl4AI HTTP",
        description="Self-hosted Crawl4AI HTTP bridge for authorized page fetches.",
        requires_base_url=True,
        requires_secret=False,
        default_base_url="http://localhost:11235",
        probe_notice="The probe fetches https://example.com through the configured bridge.",
        brand_id="crawl4ai",
        brand_icon_url="https://docs.crawl4ai.com/img/favicon.ico",
        documentation_url="https://docs.crawl4ai.com/core/self-hosting/",
    ),
    ProviderTypeSpec(
        provider_type="firecrawl_fetch",
        role="fetch",
        label="Firecrawl Fetch",
        description="Firecrawl scrape endpoint for authorized page fetches.",
        requires_base_url=True,
        requires_secret=True,
        # The fetch client appends /v1/scrape; keep the bare API origin here.
        default_base_url="https://api.firecrawl.dev",
        probe_notice="The probe fetches https://example.com and may be billable.",
        brand_id="firecrawl",
        brand_icon_url="https://www.firecrawl.dev/favicon.ico",
        documentation_url="https://docs.firecrawl.dev",
        key_management_url="https://www.firecrawl.dev/app/api-keys",
    ),
    ProviderTypeSpec(
        provider_type="deep_research_http",
        role="deep_research",
        label="Deep Research HTTP",
        description="Provider-neutral asynchronous Deep Research task endpoint.",
        requires_base_url=True,
        requires_secret=True,
        probe_notice="The probe calls the provider-neutral GET /health endpoint without creating a task.",
        brand_id="deep_research",
    ),
    ProviderTypeSpec(
        provider_type="gemini_deep_research",
        role="deep_research",
        label="Google Gemini Deep Research",
        description=(
            "Gemini Interactions API 的深度研究智能体：后台异步执行，自主检索网页并"
            "产出带 URL 引用的研究报告。使用 AI Studio API Key 即可调用，无需 GCP 项目。"
        ),
        requires_base_url=True,
        requires_secret=True,
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        probe_notice=(
            "探测仅调用 GET /models 校验密钥，不会创建研究任务，因此不产生研究费用。"
        ),
        brand_id="gemini",
        brand_icon_url="https://cdn.simpleicons.org/googlegemini",
        documentation_url="https://ai.google.dev/gemini-api/docs/deep-research",
        key_management_url="https://aistudio.google.com/apikey",
    ),
    ProviderTypeSpec(
        provider_type="openai_deep_research",
        role="deep_research",
        label="OpenAI Deep Research（Responses 后台任务）",
        description=(
            "以 OpenAI Responses 后台任务驱动联网检索并生成带引用的研究报告。"
            "专用 o3/o4-mini-deep-research 模型已于 2026-07-23 下线，此接入改用通用"
            "推理模型搭配 web_search 工具。"
        ),
        requires_base_url=True,
        requires_secret=True,
        default_base_url="https://api.openai.com/v1",
        probe_notice=(
            "探测仅调用 GET /v1/models 校验密钥，不会创建后台任务，因此不产生研究费用。"
        ),
        brand_id="openai",
        documentation_url="https://platform.openai.com/docs/guides/deep-research",
        key_management_url="https://platform.openai.com/api-keys",
    ),
    ProviderTypeSpec(
        provider_type="perplexity_deep_research",
        role="deep_research",
        label="Perplexity Deep Research",
        description=(
            "Perplexity 异步 Sonar 深度研究：提交后由平台后台执行多轮检索，"
            "返回研究报告与来源列表（含标题与摘要）。"
        ),
        requires_base_url=True,
        requires_secret=True,
        default_base_url="https://api.perplexity.ai",
        probe_notice=(
            "探测仅列出既有异步任务以校验密钥，不会创建新研究任务。"
        ),
        brand_id="perplexity",
        brand_icon_url="https://cdn.simpleicons.org/perplexity",
        documentation_url="https://docs.perplexity.ai/docs/sonar/models/sonar-deep-research",
        key_management_url="https://www.perplexity.ai/account/api/keys",
    ),
    ProviderTypeSpec(
        provider_type="tavily_deep_research",
        role="deep_research",
        label="Tavily Deep Research",
        description=(
            "Tavily 研究接口：提交后返回 request_id，由平台后台执行检索与撰写，"
            "产出 Markdown 报告与结构化来源列表，支持域名白名单。"
        ),
        requires_base_url=True,
        requires_secret=True,
        default_base_url="https://api.tavily.com",
        probe_notice="探测仅调用 GET /usage 校验密钥与额度，不会创建研究任务。",
        brand_id="tavily",
        brand_icon_url="https://tavily.com/favicon.ico",
        documentation_url="https://docs.tavily.com/documentation/api-reference/endpoint/research",
        key_management_url="https://app.tavily.com",
    ),
    ProviderTypeSpec(
        provider_type="exa_deep_research",
        role="deep_research",
        label="Exa Deep Research（Agent API）",
        description=(
            "Exa Agent API：异步多步研究任务，由子智能体并行检索不同领域，"
            "返回报告正文与 grounding 引用。深度由 effort 档位控制。"
        ),
        requires_base_url=True,
        requires_secret=True,
        default_base_url="https://api.exa.ai",
        probe_notice="探测仅列出既有 Agent 运行以校验密钥，不会创建研究任务。",
        brand_id="exa",
        brand_icon_url="https://exa.ai/favicon.ico",
        documentation_url="https://exa.ai/docs/reference/agent-api/overview",
        key_management_url="https://dashboard.exa.ai/api-keys",
    ),
    ProviderTypeSpec(
        provider_type="qwen_deep_research",
        role="deep_research",
        label="通义千问 Deep Research",
        description=(
            "阿里云百炼 qwen-deep-research：自主制定研究计划并多轮检索，"
            "输出带参考文献的研究报告。可选主线 qwen-deep-research 或快照 "
            "qwen-deep-research-2025-12-15（支持 MCP 工具）。该接口仅支持流式，"
            "任务在独立执行器中运行。"
        ),
        requires_base_url=True,
        requires_secret=True,
        # Static catalogue: mainline + snapshot; not an OpenAI /models list.
        supports_model_discovery=True,
        default_base_url="https://dashscope.aliyuncs.com",
        probe_notice=(
            "探测仅调用 GET /compatible-mode/v1/models 校验密钥，不会发起研究任务。"
            "「发现模型」会载入官方 qwen-deep-research 主线与快照模型列表。"
        ),
        brand_id="qwen",
        brand_icon_url="https://models.dev/logos/alibaba.svg",
        documentation_url="https://help.aliyun.com/zh/model-studio/qwen-deep-research",
        key_management_url="https://bailian.console.aliyun.com/?tab=model#/api-key",
    ),
    ProviderTypeSpec(
        provider_type="jina_deep_research",
        role="deep_research",
        label="Jina DeepSearch",
        description=(
            "Jina DeepSearch：OpenAI 兼容的搜索—阅读—推理循环，返回带 url_citation "
            "注解的答案。该接口仅支持流式，任务在独立执行器中运行。"
        ),
        requires_base_url=True,
        requires_secret=True,
        default_base_url="https://deepsearch.jina.ai",
        probe_notice="探测仅调用 Jina 模型列表校验密钥，不会消耗研究额度。",
        brand_id="jina",
        brand_icon_url="https://jina.ai/favicon.ico",
        documentation_url="https://jina.ai/deepsearch/",
        key_management_url="https://jina.ai/api-dashboard/key-manager",
    ),
    ProviderTypeSpec(
        provider_type="openai_compatible_transcription",
        role="transcription",
        label="OpenAI-compatible ASR",
        description=(
            "OpenAI-compatible Audio Transcriptions endpoint for stored audio "
            "files. 通义千问 / DashScope 兼容模式可用 qwen3-asr-flash 等模型。"
        ),
        requires_base_url=True,
        requires_secret=True,
        # Discovery lists GET {base_url}/models like every OpenAI-compatible
        # gateway; transcription-only endpoints without /models can still be
        # configured by typing the model ID manually.
        supports_model_discovery=True,
        supports_probe=False,
        # The transcription client appends /audio/transcriptions, so the
        # version segment belongs in the base URL.
        default_base_url="https://api.openai.com/v1",
        brand_id="openai_compatible",
        documentation_url="https://platform.openai.com/docs/api-reference/audio/createTranscription",
        key_management_url="https://platform.openai.com/api-keys",
    ),
    ProviderTypeSpec(
        provider_type="openai_compatible_embedding",
        role="embedding",
        label="OpenAI-compatible Embedding",
        description=(
            "OpenAI-compatible /embeddings endpoint used by the semantic memory "
            "recall plugin. 通义千问推荐 text-embedding-v4（DashScope 兼容模式）。"
        ),
        requires_base_url=True,
        requires_secret=True,
        # Discovery lists GET {base_url}/models like every OpenAI-compatible
        # gateway; embedding-only endpoints without /models can still be
        # configured by typing the model ID manually.
        supports_model_discovery=True,
        supports_probe=False,
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        brand_id="openai_compatible",
        documentation_url="https://platform.openai.com/docs/api-reference/embeddings",
    ),
    ProviderTypeSpec(
        provider_type="mem0_platform",
        role="memory",
        label="Mem0 Platform",
        description="Mem0 Platform v3 workspace memory endpoint.",
        requires_base_url=True,
        requires_secret=True,
        default_base_url="https://api.mem0.ai",
        brand_id="mem0",
        brand_icon_url="https://mem0.ai/favicon.ico",
        documentation_url="https://docs.mem0.ai",
        key_management_url="https://app.mem0.ai/dashboard/api-keys",
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
EMBEDDING_PROVIDER_TYPES = frozenset(
    item.provider_type for item in PROVIDER_TYPE_SPECS if item.role == "embedding"
)


def provider_type_spec(provider_type: str) -> ProviderTypeSpec | None:
    return PROVIDER_TYPE_BY_ID.get(provider_type)


def provider_catalog(*, include_development: bool = False) -> list[dict[str, object]]:
    return [
        item.view()
        for item in PROVIDER_TYPE_SPECS
        if include_development or item.role != "development"
    ]
