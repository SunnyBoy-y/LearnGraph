from __future__ import annotations

"""Bounded tool runtime for a persisted LearnGraph Chat Agent.

The model is a planner only.  Every tool below is dispatched through a
workspace-scoped service, has a durable audit trail, and returns data instead
of a host capability.  This module deliberately contains no model-provider
fallbacks or arbitrary HTTP/shell execution.
"""

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import BytesIO
import json
import math
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

# memory_tools is duck-typed (MemoryService) to avoid circular imports.

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.domain.models import (
    ChatSession,
    FileRecord,
    FileReference,
    ImageGenerationTask,
    Message,
    MessagePartRecord,
)
from app.providers.ports.fetch import FetchProviderPort
from app.providers.ports.image_generation import (
    ImageGenerationProviderPort,
    ImageGenerationRequest,
    ImageSourceInput,
)
from app.providers.ports.search import SearchProviderPort
from app.providers.remote.search import SearchProviderError, SearchProviderTimeout
from app.providers.remote.fetch import (
    FetchProviderError,
    FetchProviderTimeout,
    UnsafeFetchURL,
    require_public_http_url,
)
from app.providers.storage_factory import object_storage_provider
from app.repositories.audit import AuditRepository
from app.services.chat_attachment_policy import is_image_attachment
from app.services.image_generations import ImageGenerationService
from app.services.mcp_skills import MCPAndSkillService
from app.services.sandbox import SandboxAgentWorkspaceService
from app.services.session_retrieval import SessionRetrievalService
from app.services.session_workspace import SessionWorkspaceService


AGENT_TOOL_RESULT_MAX_BYTES = 128 * 1024
MAX_PARALLEL_RESEARCH_CHILDREN = 4
DEFAULT_CLOCK_TIMEZONE = "UTC"
MAX_AGENT_IMAGE_PROMPT_CHARS = 2_000
# Session file tools: durable chat attachments and generated images.
SESSION_FILE_LIST_MAX = 50
SESSION_FILE_TEXT_MAX_BYTES = 1 * 1024 * 1024
SESSION_FILE_TEXT_MAX_CHARS = 40_000
MAX_IMAGE_EDIT_SOURCES = 4
# Mirrors the multimodal chat attachment limits in ChatService so an image the
# user could attach directly is also readable/editable through Agent tools.
AGENT_IMAGE_INPUT_MAX_BYTES = 10 * 1024 * 1024
AGENT_IMAGE_INPUT_MAX_PIXELS = 40_000_000
AGENT_IMAGE_FORMAT_MIME_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


class AgentToolRuntime:
    """Dispatch user-authorized Agent tools without exposing host internals."""

    def __init__(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        search_provider: SearchProviderPort | None,
        extensions: MCPAndSkillService,
        sandbox: SandboxAgentWorkspaceService | None,
        sandbox_authorized: bool,
        memory_tools: Any | None = None,
        session_retrieval: SessionRetrievalService | None = None,
        image_provider: ImageGenerationProviderPort | None = None,
        image_provider_resolver: (
            Callable[[str | None, str | None], ImageGenerationProviderPort] | None
        ) = None,
        settings: Settings | None = None,
        can_manage_providers: bool = False,
        fetch_provider: FetchProviderPort | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.search_provider = search_provider
        self.extensions = extensions
        self.sandbox = sandbox
        self.sandbox_authorized = sandbox_authorized
        self.memory_tools = memory_tools
        self.session_retrieval = session_retrieval
        self.image_provider = image_provider
        self.image_provider_resolver = image_provider_resolver
        self.settings = settings
        self.can_manage_providers = bool(can_manage_providers)
        self.fetch_provider = fetch_provider
        self.audit = AuditRepository(extensions.db, workspace_id)

    def definitions(
        self,
        *,
        agent_mode_enabled: bool,
        web_search_enabled: bool,
        memory_enabled: bool = True,
    ) -> list[dict[str, Any]]:
        if not agent_mode_enabled:
            return []

        # Clock is a host-local, side-effect-free fact. Always expose it in Agent
        # mode so the model does not guess "today" from training data.
        definitions = list(self._clock_tool_definitions())
        definitions.extend(self._canvas_tool_definitions())
        definitions.extend(self._component_admin_tool_definitions())
        definitions.extend(self._provider_tool_definitions())
        definitions.extend(self._management_tool_definitions())
        definitions.extend(self._model_invocation_tool_definitions())
        definitions.extend(self._chart_tool_definitions())
        definitions.extend(self._learning_orchestration_tool_definitions())
        # Durable session files (attachments + generated images) are always
        # addressable in Agent mode; without these tools the model cannot see
        # or reference an image from an earlier turn.
        definitions.extend(self._session_file_tool_definitions())
        definitions.extend(self.extensions.agent_tool_definitions())
        # search_web / parallel_web_research / search_images follow the explicit
        # "联网" search toggle — they need an authorized SearchProvider and the
        # user's web_search flag. fetch_web_page is decoupled: a Firecrawl-style
        # FetchProvider (or a Qwen companion with .fetch) makes it available even
        # when "联网" is off, since fetching a single authorized URL is not a
        # blanket web-search action and the URL is always SSRF/allow-list gated.
        if web_search_enabled and self._search_available:
            definitions.extend(self._web_tool_definitions())
        if self._fetch_available or callable(
            getattr(self.search_provider, "fetch", None)
        ):
            definitions.extend(self._fetch_tool_definitions())
        if self._image_available:
            definitions.extend(self._image_tool_definitions())
        if self.sandbox is not None and self.sandbox_authorized:
            definitions.extend(self.sandbox.agent_tool_definitions())
        # A session with memory disabled must behave like an isolated chat:
        # cross-session retrieval and memory tools vanish together with the
        # passive prompt injection, matching the policy the user toggled.
        if self.session_retrieval is not None and memory_enabled:
            definitions.extend(self._session_retrieval_tool_definitions())
        if self.memory_tools is not None and memory_enabled:
            definitions.extend(self._memory_tool_definitions())
        return definitions

    @staticmethod
    def _learning_orchestration_tool_definitions() -> list[dict[str, Any]]:
        """First-party planning tools whose writes always remain reviewable.

        Roadmap and schedule tools are supplied by ``MCPAndSkillService``.  A
        graph proposal needs the current durable Message IDs, so it lives in
        this session-aware runtime instead of the context-free extension
        dispatcher.
        """

        node_schema = {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 80,
                    "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
                },
                "change": {"type": "string", "enum": ["add", "update"]},
                "node_id": {"type": "string", "minLength": 1, "maxLength": 36},
                "label": {"type": "string", "minLength": 1, "maxLength": 200},
                "description": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2_000,
                },
                "node_type": {
                    "type": "string",
                    "enum": ["root", "concept", "practice", "assessment"],
                },
                "rationale": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1_000,
                },
            },
            "required": [
                "ref",
                "change",
                "label",
                "description",
                "node_type",
                "rationale",
            ],
            "additionalProperties": False,
        }
        edge_schema = {
            "type": "object",
            "properties": {
                "source_ref": {"type": "string", "minLength": 1, "maxLength": 80},
                "target_ref": {"type": "string", "minLength": 1, "maxLength": 80},
                "relation": {
                    "type": "string",
                    "enum": [
                        "contains",
                        "prerequisite",
                        "related",
                        "contrast",
                        "application",
                    ],
                },
                "rationale": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1_000,
                },
            },
            "required": ["source_ref", "target_ref", "relation", "rationale"],
            "additionalProperties": False,
        }
        return [
            {
                "type": "function",
                "function": {
                    "name": "lg_graph_propose_change",
                    "description": (
                        "Create a reviewable target-graph proposal for the Goal "
                        "bound to this session, or update the Graph bound to this "
                        "session. This tool never publishes or mutates the formal "
                        "graph. For a new graph provide at least two added nodes "
                        "and exactly one root; for an update use existing node IDs "
                        "when change=update and do not add another root."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "graph_title": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 80,
                            },
                            "summary": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 2_000,
                            },
                            "nodes": {
                                "type": "array",
                                "items": node_schema,
                                "minItems": 1,
                                "maxItems": 16,
                            },
                            "edges": {
                                "type": "array",
                                "items": edge_schema,
                                "maxItems": 32,
                            },
                        },
                        "required": ["graph_title", "summary", "nodes", "edges"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

    @property
    def _image_available(self) -> bool:
        provider = self.image_provider
        return (
            provider is not None
            and self.settings is not None
            and bool(getattr(provider, "available", True))
            and bool(getattr(provider, "remote_capability", False))
        )

    @property
    def _search_available(self) -> bool:
        return self.search_provider is not None and bool(
            getattr(self.search_provider, "available", True)
        )

    @property
    def _fetch_available(self) -> bool:
        return self.fetch_provider is not None and bool(
            getattr(self.fetch_provider, "available", True)
        )

    @staticmethod
    def _memory_tool_definitions() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_conversation_history",
                    "description": (
                        "Search prior completed messages by topic/keywords within the "
                        "authorized workspace. Use when the user refers to past "
                        "discussions, decisions, or when current memory conflicts. "
                        "Do not call every turn."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "goal_id": {"type": "string"},
                            "session_id": {"type": "string"},
                            "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_conversation_segment",
                    "description": (
                        "Read a contiguous segment of messages from one session "
                        "(optionally centered on a message_id)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "session_id": {"type": "string"},
                            "around_message_id": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 40},
                        },
                        "required": ["session_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_memory_evidence",
                    "description": (
                        "Load source evidence (message snippets / source refs) for one "
                        "LearnGraph memory_id to verify or explain a recalled fact."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"memory_id": {"type": "string"}},
                        "required": ["memory_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "propose_memory_draft",
                    "description": (
                        "Propose a MemoryDraft (CREATE/UPDATE/…); does not write active "
                        "memory unless auto_commit is allowed by policy. Prefer drafts "
                        "over direct long-term writes."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": [
                                    "CREATE",
                                    "UPDATE",
                                    "MERGE",
                                    "SUPERSEDE",
                                    "RETRACT",
                                    "PROMOTE",
                                    "DEMOTE",
                                    "ARCHIVE",
                                ],
                            },
                            "memory_type": {"type": "string"},
                            "title": {"type": "string"},
                            "content": {"type": "string"},
                            "goal_id": {"type": "string"},
                            "node_id": {"type": "string"},
                            "session_id": {"type": "string"},
                            "target_memory_id": {"type": "string"},
                            "confidence": {"type": "number"},
                            "auto_commit": {"type": "boolean"},
                        },
                        "required": ["content"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    @staticmethod
    def _session_retrieval_tool_definitions() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_session_fragments",
                    "description": (
                        "Retrieve authorized prior conversation fragments by exact "
                        "session IDs, sparse keywords, or both. Use this when the "
                        "user refers to a previous plan, decision, explanation, "
                        "assessment, or learning evidence. Generate concise keywords "
                        "and phrases; never invent session IDs or SQL."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "maxLength": 500},
                            "session_ids": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1, "maxLength": 36},
                                "maxItems": 20,
                            },
                            "scope": {
                                "type": "string",
                                "enum": ["linked", "workspace", "all_authorized"],
                            },
                            "reason": {
                                "type": "string",
                                "enum": [
                                    "resolve_reference",
                                    "continue_task",
                                    "recover_decision",
                                    "verify_memory",
                                    "find_learning_evidence",
                                ],
                            },
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1, "maxLength": 160},
                                "maxItems": 20,
                            },
                            "phrases": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1, "maxLength": 240},
                                "maxItems": 10,
                            },
                            "entities": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {"type": "string", "minLength": 1, "maxLength": 64},
                                        "value": {"type": "string", "minLength": 1, "maxLength": 160},
                                    },
                                    "required": ["type", "value"],
                                    "additionalProperties": False,
                                },
                                "maxItems": 20,
                            },
                            "graph_node_ids": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1, "maxLength": 36},
                                "maxItems": 20,
                            },
                            "time_range": {
                                "type": "object",
                                "properties": {
                                    "from": {"type": "string", "format": "date-time"},
                                    "to": {"type": "string", "format": "date-time"},
                                },
                                "additionalProperties": False,
                            },
                            "status": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": [
                                        "current",
                                        "confirmed",
                                        "possibly_current",
                                        "superseded",
                                    ],
                                },
                                "maxItems": 4,
                            },
                            "prefer_recent": {"type": "boolean"},
                            "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                        },
                        "additionalProperties": False,
                        "anyOf": [
                            {"required": ["query"]},
                            {"required": ["session_ids"]},
                            {"required": ["keywords"]},
                            {"required": ["phrases"]},
                            {"required": ["entities"]},
                            {"required": ["graph_node_ids"]},
                        ],
                    },
                },
            }
        ]

    @staticmethod
    def _canvas_tool_definitions() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "canvas_get_render_contract",
                    "description": (
                        "Read the LearnGraph conversation canvas render contract: "
                        "available pixel width, height limits, theme, locale, and "
                        "whether declarative or React-sandbox cards are available. "
                        "Call this before emitting a card."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "available_width": {
                                "type": "integer",
                                "minimum": 280,
                                "maximum": 1600,
                            },
                            "theme": {"type": "string", "enum": ["light", "dark"]},
                            "locale": {"type": "string"},
                            "reduced_motion": {"type": "boolean"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "canvas_emit_trusted_component",
                    "description": (
                        "Publish a channel-A declarative trusted component into the "
                        "assistant message stream (weather_card, metric_card, "
                        "option_group, single_choice, multiple_choice, fill_blank, "
                        "short_answer_table, image_frame). Prefer this over free-form "
                        "HTML/React for forms, metrics, and weather. "
                        "CRITICAL: never pass JSON null for optional fields — omit them. "
                        "Option cards need non-empty options[{id,label}]; "
                        "single/multiple choice need title or prompt; "
                        "fill_blank needs title/prompt (blank_ids default to [answer]); "
                        "weather_card requires location, condition, temperature_c."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "component_type": {
                                "type": "string",
                                "enum": [
                                    "weather_card",
                                    "metric_card",
                                    "option_group",
                                    "single_choice",
                                    "multiple_choice",
                                    "fill_blank",
                                    "short_answer_table",
                                    "image_frame",
                                ],
                            },
                            "props": {
                                "type": "object",
                                "description": (
                                    "Component data. Examples: "
                                    'single_choice → {title, options:[{id,label}]}; '
                                    'fill_blank → {title, prompt, blank_ids:["answer"]}; '
                                    'weather_card → {location, condition, temperature_c}. '
                                    "Do not include null values."
                                ),
                            },
                            "component_id": {"type": "string"},
                            "allowed_events": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 10,
                            },
                            "schema_version": {"type": "string"},
                        },
                        "required": ["component_type", "props"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "canvas_emit_magic_card",
                    "description": (
                        "Publish a channel-B magic_card Message Part. Until the "
                        "isolated browser sandbox is configured, this records a safe "
                        "fallback card with optional dynamic preview_html (scripts run "
                        "inside a sandboxed iframe, not host DOM). Prefer full HTML "
                        "documents or fragments with <script> for animations/canvas. "
                        "Do not use this for ordinary forms."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "fallback_text": {"type": "string"},
                            "card_id": {"type": "string"},
                            "version": {"type": "integer", "minimum": 1, "maximum": 10_000},
                            "preferred_height": {
                                "type": "integer",
                                "minimum": 120,
                                "maximum": 900,
                            },
                            "preview_html": {"type": "string"},
                            "goal_id": {"type": "string"},
                            "node_id": {"type": "string"},
                        },
                        "required": ["title"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def _component_admin_tool_definitions(self) -> list[dict[str, Any]]:
        """Trusted-component Manifest admin tools. Gated by workspace.manage
        (the same flag that gates Provider write tools), so they only register
        when the Agent is acting for a workspace manager. All writes delegate
        to ComponentService, which performs Schema guards, hashing, audit and
        re-authorization — the Agent never touches the catalog directly.
        """
        if not self.can_manage_providers:
            return []
        return [
            {
                "type": "function",
                "function": {
                    "name": "component_register_manifest",
                    "description": (
                        "Register a third-party trusted component Manifest in "
                        "this workspace. The server runs static Schema guards, "
                        "records signature/package-hash status and a health check, "
                        "then returns the plugin_id + manifest_version_id. The "
                        "component_id must not collide with the 8 built-in "
                        "component identities. After registration, call "
                        "component_authorize to authorize this version before it "
                        "can be published. Requires workspace.manage. Third-party "
                        "components render in the isolated browser sandbox; "
                        "until that renderer is configured, published artifacts "
                        "are delivered as a safe sandbox_artifact downgrade."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "manifest": {
                                "type": "object",
                                "description": (
                                    "Full ComponentManifestImportRequest body: "
                                    "component_id, version, display_name, "
                                    "renderer (sandbox for third-party), source, "
                                    "package_hash, data_schema, event_schema, "
                                    "permissions, size_limits, example_data."
                                ),
                            }
                        },
                        "required": ["manifest"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "component_authorize",
                    "description": (
                        "Authorize the current Manifest version of a registered "
                        "component for this workspace, and enable the plugin so it "
                        "can be published. Requires workspace.manage. Must be "
                        "called after component_register_manifest (or after a "
                        "version/permission change that supersedes the old grant). "
                        "Built-in components are authorized by the system and "
                        "cannot be authorized through this tool. Set enable=false "
                        "to authorize without enabling."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "plugin_id": {"type": "string"},
                            "manifest_version_id": {"type": "string"},
                            "scope": {
                                "type": "string",
                                "enum": ["current_workspace"],
                                "default": "current_workspace",
                            },
                            "enable": {
                                "type": "boolean",
                                "default": True,
                                "description": "Also enable the plugin after authorizing.",
                            },
                        },
                        "required": ["plugin_id", "manifest_version_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "component_list",
                    "description": (
                        "List trusted-component plugins registered in this "
                        "workspace, with their current manifest and authorization "
                        "status. Read-only; use it to decide whether a component "
                        "needs authorization or is ready to publish. Pass a "
                        "plugin_id to restrict to one plugin."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "plugin_id": {
                                "type": "string",
                                "description": "Optional; omit to list all components.",
                            }
                        },
                        "additionalProperties": False,
                    },
                },
            },
        ]

    @staticmethod
    def _clock_tool_definitions() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_current_time",
                    "description": (
                        "Return the authoritative current date and time from the "
                        "LearnGraph host clock. Use this whenever the user asks about "
                        "today, now, weekdays, deadlines, or any time-sensitive fact "
                        "instead of guessing from training data. Optional IANA timezone "
                        f"(default {DEFAULT_CLOCK_TIMEZONE})."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "timezone": {
                                "type": "string",
                                "description": (
                                    "IANA timezone name such as Asia/Shanghai or UTC"
                                ),
                            }
                        },
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def _provider_tool_definitions(self) -> list[dict[str, Any]]:
        """D-084: discover model/provider capabilities; manage when authorized."""

        definitions: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": "list_providers",
                    "description": (
                        "List Providers configured in this workspace (model, search, "
                        "fetch, transcription, image, etc.). Never returns API keys — "
                        "only masked metadata, health, and declared capabilities."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "role": {
                                "type": "string",
                                "description": (
                                    "Optional filter: model | image_generation | vision | "
                                    "search | fetch | deep_research | memory | transcription"
                                ),
                            }
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_provider_models",
                    "description": (
                        "Discover or list models for one Provider. Non-model roles "
                        "return a clear error. Use before choosing thinking/search/vision."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "provider_id": {"type": "string"},
                        },
                        "required": ["provider_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_model_capabilities",
                    "description": (
                        "Read the saved capability snapshot for a model "
                        "(reasoning efforts, search route, image input, etc.)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "provider_id": {"type": "string"},
                            "model_id": {"type": "string"},
                        },
                        "required": ["provider_id", "model_id"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_secret_store_status",
                    "description": (
                        "Read Secret Store availability and active key version. "
                        "Never returns secrets."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            },
        ]
        if not self.can_manage_providers:
            return definitions
        definitions.extend(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "create_provider",
                        "description": (
                            "Create a Provider configuration in this workspace. "
                            "Requires workspace.manage. Credentials may only be supplied "
                            "through a trusted-UI secret label; values are never readable."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "display_name": {"type": "string"},
                                "provider_type": {"type": "string"},
                                "base_url": {"type": "string"},
                                "secret_label": {
                                    "type": "string",
                                    "description": "Opaque secret://workspace/... label injected by the trusted UI.",
                                },
                                "capabilities": {"type": "object"},
                            },
                            "required": ["display_name", "provider_type"],
                            "additionalProperties": False,
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "update_provider",
                        "description": (
                            "Update enablement, base_url, default models, or headers. "
                            "Requires workspace.manage."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "provider_id": {"type": "string"},
                                "enabled": {"type": "boolean"},
                                "base_url": {"type": "string"},
                                "default_model": {"type": "string"},
                                "default_image_generation_model_id": {"type": "string"},
                                "default_transcription_model_id": {"type": "string"},
                                "default_vision_model_id": {"type": "string"},
                            },
                            "required": ["provider_id"],
                            "additionalProperties": False,
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "rotate_provider_secret",
                        "description": (
                            "Replace a Provider credential from a trusted-UI secret label. "
                            "Requires workspace.manage. The key is never returned."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "provider_id": {"type": "string"},
                                "secret_label": {
                                    "type": "string",
                                    "description": "Opaque secret://workspace/... label injected by the trusted UI.",
                                },
                            },
                            "required": ["provider_id", "secret_label"],
                            "additionalProperties": False,
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "delete_provider",
                        "description": (
                            "Permanently delete a Provider and its encrypted secret. "
                            "Requires workspace.manage. Confirm with the user first."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "provider_id": {"type": "string"},
                            },
                            "required": ["provider_id"],
                            "additionalProperties": False,
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "put_model_capabilities",
                        "description": (
                            "Create or replace a model capability snapshot "
                            "(thinking, search, image input). Requires workspace.manage."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "provider_id": {"type": "string"},
                                "model_id": {"type": "string"},
                                "reasoning_efforts": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "enum": ["low", "medium", "high", "xhigh"],
                                    },
                                },
                                "thinking_mapping": {
                                    "type": "object",
                                    "properties": {
                                        level: {
                                            "anyOf": [
                                                {"type": "string"},
                                                {"type": "integer", "minimum": 1},
                                                {"type": "boolean"},
                                                {"type": "null"},
                                            ]
                                        }
                                        for level in (
                                            "off",
                                            "low",
                                            "medium",
                                            "high",
                                            "xhigh",
                                        )
                                    },
                                    "additionalProperties": False,
                                },
                                "default_thinking_mode": {
                                    "type": "string",
                                    "enum": ["off", "low", "medium", "high", "xhigh"],
                                },
                                "thinking_required": {"type": "boolean"},
                                "reasoning_parameter": {
                                    "type": "string",
                                    "enum": [
                                        "reasoning_effort",
                                        "reasoning.effort",
                                        "enable_thinking",
                                        "thinking_budget",
                                        "thinking",
                                    ],
                                },
                                "hosted_web_search": {"type": "boolean"},
                                "hosted_web_fetch": {"type": "boolean"},
                                "hosted_image_search": {"type": "boolean"},
                                "supports_image_input": {"type": "boolean"},
                                "supports_video_input": {"type": "boolean"},
                                "supports_structured_output": {"type": "boolean"},
                                "supports_agent_tools": {"type": "boolean"},
                                "context_window_tokens": {
                                    "type": "integer",
                                    "minimum": 8000,
                                    "maximum": 10000000,
                                },
                                "context_limit_tokens": {
                                    "type": "integer",
                                    "minimum": 8000,
                                    "maximum": 10000000,
                                },
                                "max_output_tokens": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 1000000,
                                },
                                "image_input_mode": {
                                    "type": "string",
                                    "enum": ["native", "external_vision", "auto"],
                                },
                                "default_search_route": {
                                    "type": "string",
                                    "enum": [
                                        "disabled",
                                        "model_native",
                                        "external",
                                        "local",
                                        "auto",
                                    ],
                                },
                            },
                            "required": ["provider_id", "model_id"],
                            "additionalProperties": False,
                        },
                    },
                },
            ]
        )
        return definitions

    def _management_tool_definitions(self) -> list[dict[str, Any]]:
        """Workspace control-plane tools; destructive account/storage actions omitted."""

        definitions: list[dict[str, Any]] = [
            self._function_definition(
                "list_settings",
                "List every Agent-visible workspace setting with defaults, risk, and current value.",
                {},
            ),
            self._function_definition(
                "get_setting",
                "Read one Agent-visible workspace setting and its schema metadata.",
                {"key": {"type": "string", "minLength": 1, "maxLength": 120}},
                required=["key"],
            ),
            self._function_definition(
                "rename_conversation",
                "Rename the current conversation or another authorized conversation.",
                {
                    "title": {"type": "string", "minLength": 1, "maxLength": 240},
                    "session_id": {"type": "string", "minLength": 1, "maxLength": 36},
                },
                required=["title"],
            ),
            self._function_definition(
                "get_provider_balance",
                "Query configured balance/quota metadata for one Provider. Never returns a key.",
                {"provider_id": {"type": "string", "minLength": 1, "maxLength": 36}},
                required=["provider_id"],
            ),
            self._function_definition(
                "get_provider_balance_query_config",
                "Read a Provider's custom balance-query configuration. Secret variable values are omitted.",
                {"provider_id": {"type": "string", "minLength": 1, "maxLength": 36}},
                required=["provider_id"],
            ),
            self._function_definition(
                "get_alert_email_config",
                "Read masked email-alert configuration. SMTP password is never returned.",
                {},
            ),
            self._function_definition(
                "get_functional_model_defaults",
                "Read default Provider/model routing for chat, vision, ASR, image generation, search, fetch, and deep research.",
                {},
            ),
            self._function_definition(
                "list_secret_labels",
                "List safe secret labels that can be injected into Provider updates. Never returns secret values or fingerprints.",
                {},
            ),
        ]
        if not self.can_manage_providers:
            return definitions
        definitions.extend(
            [
                self._function_definition(
                    "update_setting",
                    "Update an Agent-visible workspace setting. Account deletion, storage migration, audit deletion, and security-confirmation settings are forbidden.",
                    {
                        "key": {"type": "string", "minLength": 1, "maxLength": 120},
                        "value": {},
                    },
                    required=["key", "value"],
                ),
                self._function_definition(
                    "set_model_enabled",
                    "Enable or disable one configured Provider model.",
                    {
                        "provider_id": {"type": "string"},
                        "model_id": {"type": "string"},
                        "enabled": {"type": "boolean"},
                    },
                    required=["provider_id", "model_id", "enabled"],
                ),
                self._function_definition(
                    "update_provider_balance_query_config",
                    "Set or clear a Provider's custom balance-query script and schedule. Existing trusted-UI variables are preserved and never exposed.",
                    {
                        "provider_id": {"type": "string", "minLength": 1, "maxLength": 36},
                        "clear": {"type": "boolean"},
                        "enabled": {"type": "boolean"},
                        "template_id": {"type": ["string", "null"], "maxLength": 40},
                        "script": {"type": "string", "minLength": 1, "maxLength": 20000},
                        "timeout_seconds": {
                            "type": "number",
                            "minimum": 1,
                            "maximum": 60,
                        },
                        "auto_query_interval_minutes": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 1440,
                        },
                    },
                    required=["provider_id"],
                ),
                self._function_definition(
                    "update_alert_email_config",
                    "Update email-alert routing. Passwords cannot be supplied here; configure SMTP credentials in the trusted UI.",
                    {
                        "enabled": {"type": "boolean"},
                        "smtp_host": {"type": "string", "maxLength": 255},
                        "smtp_port": {"type": "integer", "minimum": 1, "maximum": 65535},
                        "smtp_security": {
                            "type": "string",
                            "enum": ["ssl", "starttls", "none"],
                        },
                        "smtp_username": {"type": "string", "maxLength": 255},
                        "from_address": {"type": "string", "maxLength": 255},
                        "to_addresses": {
                            "type": "array",
                            "maxItems": 20,
                            "items": {"type": "string", "maxLength": 255},
                        },
                    },
                    required=[
                        "enabled",
                        "smtp_host",
                        "smtp_port",
                        "smtp_security",
                        "smtp_username",
                        "from_address",
                        "to_addresses",
                    ],
                ),
                self._function_definition(
                    "set_functional_model_default",
                    "Set or clear the default Provider/model for one functional capability.",
                    {
                        "capability": {
                            "type": "string",
                            "enum": [
                                "chat",
                                "vision",
                                "transcription",
                                "image_generation",
                                "search",
                                "fetch",
                                "deep_research",
                            ],
                        },
                        "provider_id": {"type": ["string", "null"]},
                        "model_id": {"type": ["string", "null"]},
                    },
                    required=["capability", "provider_id", "model_id"],
                ),
            ]
        )
        return definitions

    def _model_invocation_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            self._function_definition(
                "transcribe_audio",
                "Call the configured ASR functional model for an authorized workspace audio file.",
                {
                    "file_id": {"type": "string", "minLength": 1, "maxLength": 36},
                    "language": {"type": "string", "maxLength": 32},
                    "provider_id": {"type": "string", "maxLength": 36},
                    "model_id": {"type": "string", "maxLength": 160},
                },
                required=["file_id"],
            ),
            self._function_definition(
                "analyze_image",
                "Call the configured vision functional model to inspect an authorized workspace image.",
                {
                    "file_id": {"type": "string", "minLength": 1, "maxLength": 36},
                    "prompt": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "provider_id": {"type": "string", "maxLength": 36},
                    "model_id": {"type": "string", "maxLength": 160},
                },
                required=["file_id", "prompt"],
            ),
            self._function_definition(
                "start_deep_research",
                "Create a Deep Research job. Cost-bearing remote jobs remain awaiting explicit user approval. When the result contains user_approval_required=true, STOP and do not call get_deep_research again until the user approves the budget in chat; the host injects a confirmation card the user must click.",
                {
                    "question": {"type": "string", "minLength": 3, "maxLength": 4000},
                    "budget_cny": {"type": "number", "minimum": 0, "maximum": 10000},
                    "allowed_domains": {
                        "type": "array",
                        "maxItems": 50,
                        "items": {"type": "string", "maxLength": 255},
                    },
                },
                required=["question"],
            ),
            self._function_definition(
                "get_deep_research",
                "Read the status and evidence pack of a previously created Deep Research job.",
                {
                    "research_job_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 36,
                    }
                },
                required=["research_job_id"],
            ),
        ]

    def _chart_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            self._function_definition(
                "create_chart",
                "Create a readable, durable pie, line, or bar chart with per-series colors and structured source data.",
                {
                    "type": {"type": "string", "enum": ["pie", "line", "bar"]},
                    "title": {"type": "string", "minLength": 1, "maxLength": 240},
                    "labels": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 100,
                        "items": {"type": "string", "maxLength": 160},
                    },
                    "series": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 120,
                                },
                                "values": {
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": 100,
                                    "items": {"type": "number"},
                                },
                                "color": {
                                    "type": "string",
                                    "pattern": "^#[0-9A-Fa-f]{6}$",
                                },
                            },
                            "required": ["name", "values"],
                            "additionalProperties": False,
                        },
                    },
                    "show_legend": {"type": "boolean"},
                    "show_values": {"type": "boolean"},
                },
                required=["type", "title", "labels", "series"],
            ),
            self._function_definition(
                "read_chart",
                "Read the exact structured data and summary of a chart previously created in this workspace.",
                {
                    "chart_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 36,
                    }
                },
                required=["chart_id"],
            ),
        ]

    @staticmethod
    def _function_definition(
        name: str,
        description: str,
        properties: dict[str, Any],
        *,
        required: list[str] | None = None,
    ) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            parameters["required"] = required
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }

    @staticmethod
    def _session_file_tool_definitions() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_session_files",
                    "description": (
                        "List the durable files of a chat session: user-uploaded "
                        "attachments and previously generated images, each with a "
                        "stable file_id. Call this FIRST whenever the user refers "
                        "to an earlier image or file (e.g. '修改上面的图', 'edit "
                        "that picture', 'the file I uploaded') so you can resolve "
                        "the exact file_id instead of guessing from text. Defaults "
                        "to the current session. Pass session_id ONLY when the "
                        "user explicitly asks to use files from another "
                        "conversation."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "session_id": {
                                "type": "string",
                                "description": (
                                    "Optional other chat session ID in this "
                                    "workspace. Use only on the user's explicit "
                                    "cross-session request; omit for the current "
                                    "session."
                                ),
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_session_file",
                    "description": (
                        "Read a durable session file by file_id (from "
                        "list_session_files, an attachment note, or a "
                        "generate_image result). Images: with target='context' "
                        "the host attaches the actual picture to the conversation "
                        "when the active chat model supports image input, so you "
                        "can see and describe it; before modifying an existing "
                        "image, read it first, then pass its file_id in "
                        "generate_image.source_file_ids so the original pixels "
                        "are preserved. UTF-8 text files return their decoded "
                        "content. Binary or oversized files (and target="
                        "'workspace') are materialized into the session workspace "
                        "inputs/ tree for sandbox tools."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_id": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Durable file ID within this workspace.",
                            },
                            "target": {
                                "type": "string",
                                "enum": ["context", "workspace"],
                                "description": (
                                    "context (default): return content to the "
                                    "conversation (images become visible model "
                                    "input). workspace: materialize the raw file "
                                    "into the session workspace inputs/ for "
                                    "sandbox_list_files / sandbox_exec processing."
                                ),
                            },
                        },
                        "required": ["file_id"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def _image_tool_definitions(self) -> list[dict[str, Any]]:
        default_provider_id = (
            getattr(self.image_provider, "provider_id", None) or "workspace default"
        )
        default_model_id = (
            getattr(self.image_provider, "model_id", None) or "workspace default"
        )
        return [
            {
                "type": "function",
                "function": {
                    "name": "generate_image",
                    "description": (
                        "Generate an image from a natural-language prompt using the "
                        "workspace image generation Provider. Use only when the user "
                        "explicitly asks for a picture, diagram, illustration, or "
                        "visual. To MODIFY an existing session image (e.g. '修改上面"
                        "的图'), you MUST pass its file_id in source_file_ids — "
                        "resolve it via list_session_files / read_session_file — so "
                        "the provider edits the original pixels; never re-describe "
                        "an existing image from memory, which produces a completely "
                        "different picture. By default OMIT provider_id and "
                        f"model_id: the tool will use the configured default image "
                        f"model {default_provider_id}/{default_model_id}. Specify "
                        "them only when deliberately choosing another configured, "
                        "enabled image model after inspecting Provider models. "
                        "Returns a durable file_id the chat UI can render."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": MAX_AGENT_IMAGE_PROMPT_CHARS,
                                "description": (
                                    "Detailed image description in the user's language."
                                ),
                            },
                            "title": {
                                "type": "string",
                                "maxLength": 120,
                                "description": "Optional short display title for the image.",
                            },
                            "provider_id": {
                                "type": "string",
                                "description": (
                                    "Optional configured image Provider ID. Omit to use "
                                    "the workspace default image Provider."
                                ),
                            },
                            "model_id": {
                                "type": "string",
                                "description": (
                                    "Optional configured and enabled image-generation "
                                    "model ID. Omit to use the default image model."
                                ),
                            },
                            "source_file_ids": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                                "maxItems": MAX_IMAGE_EDIT_SOURCES,
                                "description": (
                                    "Existing session image file_ids to edit or use "
                                    "as the visual reference. REQUIRED when the user "
                                    "asks to modify a previously generated or "
                                    "uploaded image: the original pixels are sent to "
                                    "the image provider so composition and content "
                                    "are preserved. Obtain file_ids from "
                                    "list_session_files or earlier generate_image "
                                    "results."
                                ),
                            },
                        },
                        "required": ["prompt"],
                        "additionalProperties": False,
                    },
                },
            },
        ]

    def _fetch_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "fetch_web_page",
                    "description": (
                        "Read the full content of a user-authorized public URL. "
                        "Uses the configured Firecrawl-style FetchProvider when "
                        "available, automatically falling back to the Qwen web "
                        "extractor companion if the primary fetcher fails. The "
                        "URL's host must be inside the explicitly authorized "
                        "domain set for this session."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string", "format": "uri"},
                        },
                        "required": ["url"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

    def _web_tool_definitions(self) -> list[dict[str, Any]]:
        definitions = [
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": (
                        "Search the web through the user-authorized LearnGraph "
                        "SearchProvider. Use this only when current information or "
                        "external sources are needed."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "A concise web search query.",
                            }
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "parallel_web_research",
                    "description": (
                        "Run 2 to 4 independent web-research child tasks in parallel "
                        "through the authorized SearchProvider, then return their "
                        "separate source sets to the parent Agent. Use for genuinely "
                        "independent research angles; do not use it for one query."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tasks": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1, "maxLength": 500},
                                "minItems": 2,
                                "maxItems": MAX_PARALLEL_RESEARCH_CHILDREN,
                                "description": "Independent concise search queries.",
                            }
                        },
                        "required": ["tasks"],
                        "additionalProperties": False,
                    },
                },
            },
        ]
        if callable(getattr(self.search_provider, "image_search", None)):
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": "search_images",
                        "description": (
                            "Search the public web for images through the configured "
                            "Qwen Responses image-search companion."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 500,
                                },
                                "image_url": {
                                    "type": "string",
                                    "format": "uri",
                                    "description": (
                                        "Optional public image URL for reverse image search."
                                    ),
                                },
                            },
                            "required": ["query"],
                            "additionalProperties": False,
                        },
                    },
                }
            )
        return definitions

    def execute(
        self,
        tool_call: dict[str, Any],
        *,
        allowed_domains: list[str],
        chat_session_id: str,
        assistant_message_id: str | None = None,
        assistant_version_id: str | None = None,
        source_message_id: str | None = None,
        model_supports_image_input: bool = False,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return self._failure("invalid_tool_call", "Tool call is malformed")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            return self._failure("invalid_tool_call", "Tool call has no function name")
        try:
            arguments = self._parse_arguments(function.get("arguments"))
            if name == "search_session_fragments":
                if self.session_retrieval is None:
                    return self._failure(
                        "session_search_unavailable",
                        "Session retrieval is unavailable",
                    )
                from app.domain.schemas.chat import SessionFragmentSearchRequest

                try:
                    payload = SessionFragmentSearchRequest.model_validate(arguments)
                except Exception as exc:
                    raise AppError(
                        422,
                        "invalid_tool_arguments",
                        "search_session_fragments arguments are invalid",
                        {"validation_error": str(exc)},
                    ) from exc
                response = self.session_retrieval.search(
                    current_session_id=chat_session_id,
                    payload=payload,
                )
                result = response.model_dump(mode="json", by_alias=True)
                return self._success(
                    result,
                    {
                        "hit_count": len(response.hits),
                        "retrieval_strategy": response.retrieval_strategy,
                    },
                    [],
                )
            if name in {
                "search_conversation_history",
                "read_conversation_segment",
                "get_memory_evidence",
                "propose_memory_draft",
            }:
                return self._execute_memory_tool(name, arguments, chat_session_id=chat_session_id)
            if name == "get_current_time":
                result = self._get_current_time(arguments)
                return self._success(
                    result,
                    {
                        "timezone": result["timezone"],
                        "utc_offset": result["utc_offset"],
                    },
                    [],
                )
            if name in {
                "canvas_get_render_contract",
                "canvas_emit_trusted_component",
                "canvas_emit_magic_card",
            }:
                return self._execute_canvas_tool(name, arguments, chat_session_id=chat_session_id)
            if name in {
                "component_register_manifest",
                "component_authorize",
                "component_list",
            }:
                return self._execute_component_admin_tool(name, arguments)
            if name == "lg_graph_propose_change":
                return self._execute_graph_proposal_tool(
                    arguments,
                    chat_session_id=chat_session_id,
                    assistant_message_id=assistant_message_id,
                    source_message_id=source_message_id,
                )
            if name in {
                "list_providers",
                "list_provider_models",
                "get_model_capabilities",
                "get_secret_store_status",
                "create_provider",
                "update_provider",
                "rotate_provider_secret",
                "delete_provider",
                "put_model_capabilities",
            }:
                return self._execute_provider_tool(name, arguments)
            if name in {
                "list_settings",
                "get_setting",
                "update_setting",
                "rename_conversation",
                "get_provider_balance",
                "get_provider_balance_query_config",
                "update_provider_balance_query_config",
                "set_model_enabled",
                "get_alert_email_config",
                "update_alert_email_config",
                "get_functional_model_defaults",
                "set_functional_model_default",
                "list_secret_labels",
            }:
                return self._execute_management_tool(
                    name,
                    arguments,
                    chat_session_id=chat_session_id,
                )
            if name in {
                "transcribe_audio",
                "analyze_image",
                "start_deep_research",
                "get_deep_research",
            }:
                return self._execute_model_invocation_tool(
                    name,
                    arguments,
                    chat_session_id=chat_session_id,
                )
            if name in {"create_chart", "read_chart"}:
                return self._execute_chart_tool(name, arguments)
            if name == "search_web":
                result, sources = self._search(arguments, allowed_domains)
                return self._success(
                    result,
                    {"query": result["query"], "result_count": len(sources)},
                    sources,
                )
            if name == "fetch_web_page":
                return self._fetch_web_page(arguments, allowed_domains)
            if name == "search_images":
                return self._search_images(arguments, allowed_domains)
            if name == "list_session_files":
                return self._list_session_files(
                    arguments, chat_session_id=chat_session_id
                )
            if name == "read_session_file":
                return self._read_session_file(
                    arguments,
                    chat_session_id=chat_session_id,
                    model_supports_image_input=model_supports_image_input,
                )
            if name == "generate_image":
                return self._execute_generate_image(
                    arguments,
                    chat_session_id=chat_session_id,
                    assistant_message_id=assistant_message_id,
                    assistant_version_id=assistant_version_id,
                    source_message_id=source_message_id,
                )
            if name == "parallel_web_research":
                result, sources = self._parallel_web_research(arguments, allowed_domains)
                return self._success(result, {"child_runs": result["child_runs"]}, sources)
            if name.startswith("sandbox_"):
                if self.sandbox is None:
                    return self._failure("sandbox_agent_unavailable", "Sandbox Agent tools are unavailable")
                result = self.sandbox.execute_agent_tool(
                    name,
                    arguments,
                    chat_session_id=chat_session_id,
                    agent_authorized=self.sandbox_authorized,
                )
                status = str(result.get("status") or "completed")
                if status not in {"completed", "ready"} and name == "sandbox_exec":
                    return self._failure(
                        str(result.get("error_class") or "sandbox_execution_failed"),
                        str(result.get("stderr") or result.get("error_class") or "Sandbox command failed"),
                        data=result,
                    )
                meta: dict[str, Any] = {
                    "sandbox": True,
                    "sandbox_session_id": result.get("sandbox_session_id"),
                }
                if isinstance(result.get("artifact"), dict):
                    meta["artifact"] = result["artifact"]
                if isinstance(result.get("part"), dict):
                    meta["artifact"] = result["part"]
                if isinstance(result.get("summary"), dict):
                    meta["summary"] = result["summary"]
                if result.get("file_id"):
                    meta["file_id"] = result.get("file_id")
                return self._success(result, meta, [])
            result = self.extensions.invoke_agent_function(name, arguments)
            extension_meta: dict[str, Any] = {
                "extension_invocation_id": result.get("invocation_id"),
                "target_type": result.get("target_type"),
            }
            if isinstance(result.get("skill_trigger"), dict):
                extension_meta["skill_trigger"] = result["skill_trigger"]
            extension_result = result.get("result")
            if (
                isinstance(extension_result, dict)
                and extension_result.get("confirmation_required") is True
                and extension_result.get("confirmation_id")
            ):
                extension_meta["artifact"] = {
                    "type": "user_confirmation",
                    "status": "pending",
                    "data": {
                        "action": "skill.delete",
                        "confirmation_id": extension_result["confirmation_id"],
                        "skill_id": extension_result.get("skill_id"),
                        "skill_key": extension_result.get("skill_key"),
                        "skill_name": extension_result.get("skill_name"),
                        "expires_at": extension_result.get("expires_at"),
                        "message": (
                            "永久删除 Skill 需要用户本人进行二次确认并重新输入当前密码。"
                            "智能体不能代替用户完成此确认。"
                        ),
                    },
                }
            return self._success(result, extension_meta, [])
        except AppError as exc:
            return self._failure(exc.code, exc.message, data=exc.details or {})
        except Exception:
            # Do not leak transport, provider, SQL, or sandbox implementation
            # details into an Agent transcript.  The authoritative traceback
            # remains in server logs; the persisted audit record identifies the
            # target tool and safe failure class.
            self.audit.record(
                actor_id=self.actor_id,
                action="agent.tool.unexpected_failure",
                resource_type="agent_tool",
                resource_id=name,
                outcome="failure",
            )
            self.extensions.db.commit()
            return self._failure("agent_tool_failed", "The authorized tool failed")

    def _execute_graph_proposal_tool(
        self,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
        assistant_message_id: str | None,
        source_message_id: str | None,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Persist an inert graph change set from the current Agent turn."""

        if not assistant_message_id or not source_message_id:
            raise AppError(
                409,
                "graph_proposal_message_context_missing",
                "A graph proposal requires the current persisted user and assistant messages",
            )

        from sqlalchemy import select

        from app.domain.models import ChatSession, Goal, Graph, Message
        from app.domain.schemas.graphs import (
            GraphChangeSetView,
            ModelConversationGraphProposal,
        )
        from app.services.graph_changes import GraphChangeSetService

        db = self.extensions.db
        session = db.scalar(
            select(ChatSession).where(
                ChatSession.workspace_id == self.workspace_id,
                ChatSession.id == chat_session_id,
            )
        )
        if session is None:
            raise AppError(404, "session_not_found", "Session was not found")
        if session.goal_id is None:
            raise AppError(
                409,
                "goal_required_for_graph",
                "Bind a confirmed Goal to this session before proposing a graph",
            )
        goal = db.scalar(
            select(Goal).where(
                Goal.workspace_id == self.workspace_id,
                Goal.id == session.goal_id,
            )
        )
        if goal is None:
            raise AppError(404, "goal_not_found", "The session Goal was not found")
        if goal.status not in {"confirmed", "candidate_ready", "approved"}:
            raise AppError(
                409,
                "goal_not_confirmed_for_graph",
                "Confirm the Goal before proposing a graph",
            )

        graph = None
        mode = "create"
        base_revision = 0
        if session.graph_id:
            graph = db.scalar(
                select(Graph).where(
                    Graph.workspace_id == self.workspace_id,
                    Graph.id == session.graph_id,
                    Graph.goal_id == goal.id,
                )
            )
            if graph is None:
                raise AppError(
                    404,
                    "graph_not_found",
                    "The Graph bound to this session was not found",
                )
            mode = "update"
            base_revision = graph.revision

        source_user_message = db.scalar(
            select(Message).where(
                Message.workspace_id == self.workspace_id,
                Message.id == source_message_id,
                Message.session_id == session.id,
                Message.role == "user",
            )
        )
        source_assistant_message = db.scalar(
            select(Message).where(
                Message.workspace_id == self.workspace_id,
                Message.id == assistant_message_id,
                Message.session_id == session.id,
                Message.role == "assistant",
            )
        )
        if source_user_message is None or source_assistant_message is None:
            raise AppError(
                409,
                "graph_proposal_message_context_invalid",
                "Graph proposal messages do not belong to the current session",
            )

        try:
            proposal = ModelConversationGraphProposal.model_validate(arguments)
        except Exception as exc:
            raise AppError(
                422,
                "invalid_tool_arguments",
                "Graph proposal arguments are invalid",
                {"validation_error": str(exc)},
            ) from exc

        service = GraphChangeSetService(db, self.workspace_id, self.actor_id)
        service.ensure_can_propose(session.id)
        item = service.create_proposal(
            session=session,
            goal=goal,
            graph=graph,
            source_user_message=source_user_message,
            source_assistant_message=source_assistant_message,
            mode=mode,
            base_revision=base_revision,
            proposal=proposal,
            provider_trace={
                "origin": "agent_tool",
                "tool_name": "lg_graph_propose_change",
                "source_assistant_message_id": assistant_message_id,
            },
        )
        db.flush()
        view = GraphChangeSetView.model_validate(item).model_dump(mode="json")
        component = service.component_data(item)
        return self._success(
            {
                "proposal_id": item.id,
                "mode": mode,
                "goal_id": goal.id,
                "graph_id": graph.id if graph else None,
                "base_revision": base_revision,
                "status": item.status,
                "review_required": True,
                "proposal": view["proposal"],
            },
            {
                "graph_change_set_id": item.id,
                "review_required": True,
                "artifact": {
                    "type": "component",
                    "status": "completed",
                    "data": component,
                },
            },
            [],
        )

    def _provider_service(self):
        from app.core.config import get_settings
        from app.services.management import ProviderService

        settings = self.settings or get_settings()
        return ProviderService(
            self.extensions.db,
            self.workspace_id,
            self.actor_id,
            settings,
        )

    @staticmethod
    def _provider_public_view(provider: Any, secret_meta: dict[str, Any] | None = None) -> dict[str, Any]:
        meta = secret_meta or {}
        capabilities = dict(getattr(provider, "capabilities", None) or {})
        # Never surface secret material or fingerprints to the model.
        return {
            "id": provider.id,
            "display_name": provider.display_name,
            "provider_type": provider.provider_type,
            "base_url": provider.base_url,
            "api_key_configured": bool(provider.api_key_masked),
            "enabled": bool(provider.enabled),
            "remote_capability": bool(provider.remote_capability),
            "status": provider.status,
            "capabilities": {
                key: value
                for key, value in capabilities.items()
                if key
                not in {
                    "secret",
                    "api_key",
                    "authorization",
                    "secret_fingerprint",
                }
            },
            "secret_status": meta.get("secret_status"),
            "secret_version": meta.get("secret_version"),
            "secret_key_provider": meta.get("secret_key_provider"),
            "secret_key_version": meta.get("secret_key_version"),
        }

    def _require_provider_manage(self) -> None:
        if not self.can_manage_providers:
            raise AppError(
                403,
                "permission_denied",
                "Permission 'workspace.manage' is required for Provider write tools",
            )

    def _require_component_manage(self) -> None:
        if not self.can_manage_providers:
            raise AppError(
                403,
                "permission_denied",
                "Permission 'workspace.manage' is required for trusted-component admin tools",
            )

    def _component_service(self):
        from app.services.components import ComponentService

        return ComponentService(
            self.extensions.db,
            self.workspace_id,
            self.actor_id,
        )

    def _execute_component_admin_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        from app.domain.models import PluginRecord
        from app.domain.schemas.components import (
            ComponentAuthorizationRequest,
            ComponentManifestImportRequest,
        )
        from app.repositories.domain import PluginRepository

        self._require_component_manage()
        service = self._component_service()

        if name == "component_register_manifest":
            manifest_payload = arguments.get("manifest")
            if not isinstance(manifest_payload, dict):
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "manifest must be a JSON object",
                )
            try:
                payload = ComponentManifestImportRequest.model_validate(
                    manifest_payload
                )
            except Exception as exc:  # pydantic validation details
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    f"manifest failed validation: {exc}",
                ) from exc
            plugin, manifest, reauth_required, reasons, checks = service.register(payload)
            return self._success(
                {
                    "plugin_id": plugin.id,
                    "manifest_version_id": manifest.id,
                    "component_id": manifest.component_id,
                    "version": manifest.version,
                    "display_name": manifest.display_name,
                    "renderer": manifest.renderer,
                    "reauthorization_required": reauth_required,
                    "reauthorization_reasons": reasons,
                    "checks": [
                        {
                            "check_type": check.check_type,
                            "status": check.status,
                            "executor": check.executor,
                        }
                        for check in checks
                    ],
                    "next_step": (
                        "Call component_authorize with this plugin_id and "
                        "manifest_version_id to authorize it in this workspace."
                    ),
                },
                {"tool": name, "component_id": manifest.component_id},
                [],
            )

        if name == "component_authorize":
            from app.domain.schemas.management import PluginToggleRequest
            from app.services.management import PluginService

            plugin_id = str(arguments.get("plugin_id") or "").strip()
            manifest_version_id = str(
                arguments.get("manifest_version_id") or ""
            ).strip()
            if not plugin_id or not manifest_version_id:
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "plugin_id and manifest_version_id are required",
                )
            scope = arguments.get("scope")
            auth_request = ComponentAuthorizationRequest(
                manifest_version_id=manifest_version_id,
                scope=scope if isinstance(scope, str) else "current_workspace",
            )
            authorization = service.authorize(plugin_id, auth_request)
            # Authorizing also enables the plugin so the Agent can publish it
            # without a separate enable step; assert_can_enable is enforced
            # inside PluginService.toggle for trusted_component plugins.
            enable_flag = arguments.get("enable")
            should_enable = (
                enable_flag if isinstance(enable_flag, bool) else True
            )
            enable_status: dict[str, Any] = {"plugin_enabled": None, "plugin_status": None}
            if should_enable:
                plugin_service = PluginService(
                    self.extensions.db,
                    self.workspace_id,
                    self.actor_id,
                )
                toggled = plugin_service.toggle(
                    plugin_id, PluginToggleRequest(enabled=True)
                )
                enable_status = {
                    "plugin_enabled": bool(toggled.enabled),
                    "plugin_status": toggled.status,
                }
            return self._success(
                {
                    "authorization_id": authorization.id,
                    "status": authorization.status,
                    "manifest_version_id": authorization.manifest_version_id,
                    "scope": authorization.scope,
                    **enable_status,
                    "next_step": (
                        "The component is authorized and enabled. Emit it via "
                        "canvas_emit_trusted_component using its component_id as "
                        "component_type. Third-party artifacts render as a safe "
                        "sandbox_artifact downgrade until the isolated browser "
                        "renderer is configured."
                    ),
                },
                {"tool": name, "plugin_id": plugin_id},
                [],
            )

        if name == "component_list":
            plugin_id = arguments.get("plugin_id")
            plugins_repo = PluginRepository(
                self.extensions.db, self.workspace_id
            )
            query = plugins_repo.query().where(
                PluginRecord.plugin_type == "trusted_component"
            )
            if isinstance(plugin_id, str) and plugin_id.strip():
                query = query.where(PluginRecord.id == plugin_id.strip())
            plugins = list(self.extensions.db.scalars(query).all())
            items: list[dict[str, Any]] = []
            for plugin in plugins:
                manifest = service.manifests.current(plugin)
                authorization = service.authorizations.active_for_plugin(plugin.id)
                health = (
                    service.checks.latest(plugin.id, manifest.id, "health")
                    if manifest is not None
                    else None
                )
                items.append(
                    {
                        "plugin_id": plugin.id,
                        "component_id": plugin.plugin_key,
                        "name": plugin.name,
                        "version": plugin.version,
                        "enabled": bool(plugin.enabled),
                        "status": plugin.status,
                        "manifest_version_id": manifest.id if manifest else None,
                        "renderer": manifest.renderer if manifest else None,
                        "source": manifest.source if manifest else None,
                        "authorized": authorization is not None,
                        "authorization_status": (
                            authorization.status if authorization else None
                        ),
                        "health_status": health.status if health else None,
                        "ready_to_publish": (
                            authorization is not None
                            and manifest is not None
                            and health is not None
                            and health.status == "passed"
                        ),
                    }
                )
            return self._success(
                {"components": items, "count": len(items)},
                {"tool": name, "count": len(items)},
                [],
            )

        raise AppError(404, "unknown_tool", f"Unknown component admin tool: {name}")

    def _execute_provider_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        from app.domain.schemas.management import (
            ProviderCreateRequest,
            ProviderModelCapabilityUpdateRequest,
            ProviderSecretRotateRequest,
            ProviderUpdateRequest,
        )
        from app.providers.catalog import provider_type_spec
        from app.services.secret_references import SecretReferenceService
        from pydantic import SecretStr

        service = self._provider_service()
        secret_refs = SecretReferenceService(
            self.extensions.db,
            self.workspace_id,
            self.actor_id,
            self.settings or get_settings(),
        )
        if name == "list_providers":
            role_filter = arguments.get("role")
            secret_meta = service.secret_metadata()
            items = []
            for provider in service.list():
                capabilities = dict(provider.capabilities or {})
                role = capabilities.get("provider_role")
                if not role:
                    spec = provider_type_spec(provider.provider_type)
                    role = spec.role if spec is not None else None
                if isinstance(role_filter, str) and role_filter.strip():
                    if role != role_filter.strip():
                        continue
                items.append(
                    self._provider_public_view(
                        provider,
                        secret_meta.get(provider.id),
                    )
                    | {"role": role}
                )
            return self._success(
                {"providers": items, "count": len(items)},
                {"tool": name, "count": len(items)},
                [],
            )
        if name == "list_provider_models":
            provider_id = str(arguments.get("provider_id") or "").strip()
            if not provider_id:
                raise AppError(422, "invalid_tool_arguments", "provider_id is required")
            result = service.models(provider_id)
            return self._success(result, {"tool": name, "provider_id": provider_id}, [])
        if name == "get_model_capabilities":
            provider_id = str(arguments.get("provider_id") or "").strip()
            model_id = str(arguments.get("model_id") or "").strip()
            if not provider_id or not model_id:
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "provider_id and model_id are required",
                )
            result = service.model_capabilities(provider_id, model_id)
            return self._success(
                result,
                {"tool": name, "provider_id": provider_id, "model_id": model_id},
                [],
            )
        if name == "get_secret_store_status":
            result = service.secret_store_status()
            return self._success(result, {"tool": name}, [])
        if name == "create_provider":
            self._require_provider_manage()
            payload = ProviderCreateRequest(
                display_name=str(arguments.get("display_name") or "").strip(),
                provider_type=str(arguments.get("provider_type") or "").strip(),
                base_url=(
                    str(arguments["base_url"]).strip()
                    if isinstance(arguments.get("base_url"), str)
                    else None
                ),
                api_key=(
                    SecretStr(
                        secret_refs.resolve(
                            str(arguments["secret_label"]),
                            purpose="provider_api_key",
                        )
                    )
                    if arguments.get("secret_label")
                    else None
                ),
                capabilities=(
                    arguments.get("capabilities")
                    if isinstance(arguments.get("capabilities"), dict)
                    else {}
                ),
            )
            provider = service.create(payload)
            secret_meta = service.secret_metadata().get(provider.id)
            return self._success(
                self._provider_public_view(provider, secret_meta),
                {"tool": name, "provider_id": provider.id, "mutated": True},
                [],
            )
        if name == "update_provider":
            self._require_provider_manage()
            provider_id = str(arguments.get("provider_id") or "").strip()
            if not provider_id:
                raise AppError(422, "invalid_tool_arguments", "provider_id is required")
            update_fields = {
                key: arguments[key]
                for key in (
                    "enabled",
                    "base_url",
                    "default_model",
                    "default_image_generation_model_id",
                    "default_transcription_model_id",
                    "default_vision_model_id",
                )
                if key in arguments and arguments[key] is not None
            }
            payload = ProviderUpdateRequest(**update_fields)
            provider = service.update(provider_id, payload)
            secret_meta = service.secret_metadata().get(provider.id)
            return self._success(
                self._provider_public_view(provider, secret_meta),
                {"tool": name, "provider_id": provider.id, "mutated": True},
                [],
            )
        if name == "rotate_provider_secret":
            self._require_provider_manage()
            provider_id = str(arguments.get("provider_id") or "").strip()
            secret_label = arguments.get("secret_label")
            if (
                not provider_id
                or not isinstance(secret_label, str)
                or not secret_label.strip()
            ):
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "provider_id and secret_label are required",
                )
            api_key = secret_refs.resolve(
                secret_label,
                purpose="provider_api_key",
            )
            result = service.rotate_secret(
                provider_id,
                ProviderSecretRotateRequest(api_key=SecretStr(api_key)),
            )
            # rotate_secret returns lifecycle metadata without plaintext.
            safe = {
                key: result.get(key)
                for key in (
                    "provider_id",
                    "status",
                    "secret_version",
                    "key_version",
                    "rotated_at",
                    "revoked_at",
                )
            }
            return self._success(
                safe,
                {"tool": name, "provider_id": provider_id, "mutated": True},
                [],
            )
        if name == "delete_provider":
            self._require_provider_manage()
            provider_id = str(arguments.get("provider_id") or "").strip()
            if not provider_id:
                raise AppError(422, "invalid_tool_arguments", "provider_id is required")
            result = service.delete(provider_id)
            return self._success(
                result,
                {"tool": name, "provider_id": provider_id, "mutated": True},
                [],
            )
        if name == "put_model_capabilities":
            self._require_provider_manage()
            provider_id = str(arguments.get("provider_id") or "").strip()
            model_id = str(arguments.get("model_id") or "").strip()
            if not provider_id or not model_id:
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "provider_id and model_id are required",
                )
            capability_fields = {
                key: arguments[key]
                for key in (
                    "reasoning_efforts",
                    "thinking_mapping",
                    "default_thinking_mode",
                    "thinking_required",
                    "reasoning_parameter",
                    "hosted_web_search",
                    "hosted_web_fetch",
                    "hosted_image_search",
                    "supports_image_input",
                    "supports_video_input",
                    "supports_structured_output",
                    "supports_agent_tools",
                    "context_window_tokens",
                    "context_limit_tokens",
                    "max_output_tokens",
                    "image_input_mode",
                    "default_search_route",
                )
                if key in arguments and arguments[key] is not None
            }
            payload = ProviderModelCapabilityUpdateRequest(**capability_fields)
            result = service.update_model_capabilities(provider_id, model_id, payload)
            return self._success(
                result,
                {
                    "tool": name,
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "mutated": True,
                },
                [],
            )
        raise AppError(404, "unknown_provider_tool", f"Unknown provider tool: {name}")

    def _execute_management_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        from app.domain.schemas.chat import SessionUpdateRequest
        from app.domain.schemas.management import (
            ProviderModelStateUpdateRequest,
            SettingUpdateRequest,
        )
        from app.domain.settings import FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY
        from app.providers.catalog import provider_type_spec
        from app.services import alert_email
        from app.services.management import SettingsService
        from app.services.secret_references import SecretReferenceService
        from app.services.workflow import WorkflowService

        db = self.extensions.db
        settings_service = SettingsService(db, self.workspace_id, self.actor_id)
        if name == "list_settings":
            items = settings_service.catalog()
            return self._success(
                {"settings": items, "count": len(items)},
                {"tool": name, "count": len(items)},
                [],
            )
        if name == "get_setting":
            key = str(arguments.get("key") or "").strip()
            if not key:
                raise AppError(422, "invalid_tool_arguments", "key is required")
            return self._success(
                settings_service.get(key),
                {"tool": name, "setting_key": key},
                [],
            )
        if name == "rename_conversation":
            title = str(arguments.get("title") or "").strip()
            session_id = str(arguments.get("session_id") or chat_session_id).strip()
            if not title:
                raise AppError(422, "invalid_tool_arguments", "title is required")
            if self.extensions.workspace is None or self.extensions.principal is None:
                raise AppError(
                    403,
                    "agent_runtime_context_missing",
                    "Conversation management requires an authorized user context",
                )
            session = WorkflowService(
                db,
                self.extensions.workspace,
                self.extensions.principal,
            ).update_session(session_id, SessionUpdateRequest(title=title))
            return self._success(
                {"session_id": session.id, "title": session.title},
                {"tool": name, "session_id": session.id, "mutated": True},
                [],
            )
        if name == "get_provider_balance":
            self._require_provider_manage()
            provider_id = str(arguments.get("provider_id") or "").strip()
            if not provider_id:
                raise AppError(
                    422, "invalid_tool_arguments", "provider_id is required"
                )
            result = self._provider_service().balance(provider_id)
            return self._success(
                result,
                {"tool": name, "provider_id": provider_id},
                [],
            )
        if name == "get_provider_balance_query_config":
            self._require_provider_manage()
            provider_id = str(arguments.get("provider_id") or "").strip()
            if not provider_id:
                raise AppError(
                    422, "invalid_tool_arguments", "provider_id is required"
                )
            result = self._provider_service().balance_query_config(provider_id)
            config = result.get("config")
            if config is not None:
                dumped = config.model_dump(mode="json")
                dumped["variable_names"] = sorted(dumped.pop("variables", {}).keys())
                result = {"provider_id": provider_id, "config": dumped}
            return self._success(
                result,
                {
                    "tool": name,
                    "provider_id": provider_id,
                    "secret_redacted": True,
                },
                [],
            )
        if name == "get_alert_email_config":
            self._require_provider_manage()
            return self._success(
                alert_email.load_config(db, self.workspace_id).view(),
                {"tool": name, "secret_redacted": True},
                [],
            )
        if name == "get_functional_model_defaults":
            return self._success(
                settings_service.get(FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY),
                {"tool": name},
                [],
            )
        if name == "list_secret_labels":
            self._require_provider_manage()
            raw_items = SecretReferenceService(
                db,
                self.workspace_id,
                self.actor_id,
                self.settings or get_settings(),
            ).list()
            items = [
                {
                    "reference": item["reference"],
                    "purpose": item["purpose"],
                    "version": item["version"],
                    "updated_at": item["updated_at"],
                }
                for item in raw_items
            ]
            return self._success(
                {"labels": items, "count": len(items)},
                {"tool": name, "secret_redacted": True, "count": len(items)},
                [],
            )

        self._require_provider_manage()
        if name == "update_setting":
            key = str(arguments.get("key") or "").strip()
            if not key:
                raise AppError(422, "invalid_tool_arguments", "key is required")
            settings_service.require_agent_writable(key)
            if key == FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY:
                raise AppError(
                    409,
                    "dedicated_setting_tool_required",
                    "Use set_functional_model_default so the Provider and model are validated",
                )
            item = settings_service.update(
                key,
                SettingUpdateRequest(value=arguments.get("value")),
            )
            return self._success(
                {"key": item.key, "value": item.value},
                {"tool": name, "setting_key": key, "mutated": True},
                [],
            )
        if name == "update_provider_balance_query_config":
            from app.domain.schemas.management import ProviderBalanceQueryConfig

            provider_id = str(arguments.get("provider_id") or "").strip()
            if not provider_id:
                raise AppError(
                    422, "invalid_tool_arguments", "provider_id is required"
                )
            provider_service = self._provider_service()
            if bool(arguments.get("clear")):
                result = provider_service.update_balance_query_config(
                    provider_id, None
                )
            else:
                existing = provider_service.balance_query_config(provider_id).get(
                    "config"
                )
                if not isinstance(arguments.get("script"), str):
                    raise AppError(
                        422,
                        "invalid_tool_arguments",
                        "script is required unless clear is true",
                    )
                config = ProviderBalanceQueryConfig(
                    enabled=bool(arguments.get("enabled", True)),
                    template_id=arguments.get("template_id"),
                    script=str(arguments["script"]),
                    timeout_seconds=float(arguments.get("timeout_seconds", 10)),
                    auto_query_interval_minutes=int(
                        arguments.get("auto_query_interval_minutes", 0)
                    ),
                    # These may contain trusted UI values and are deliberately
                    # neither shown to nor editable by the Agent.
                    variables=(
                        dict(existing.variables)
                        if existing is not None
                        else {}
                    ),
                )
                result = provider_service.update_balance_query_config(
                    provider_id, config
                )
            config = result.get("config")
            if config is not None:
                dumped = config.model_dump(mode="json")
                dumped["variable_names"] = sorted(dumped.pop("variables", {}).keys())
                result = {"provider_id": provider_id, "config": dumped}
            return self._success(
                result,
                {
                    "tool": name,
                    "provider_id": provider_id,
                    "mutated": True,
                    "secret_redacted": True,
                },
                [],
            )
        if name == "set_model_enabled":
            provider_id = str(arguments.get("provider_id") or "").strip()
            model_id = str(arguments.get("model_id") or "").strip()
            enabled = arguments.get("enabled")
            if not provider_id or not model_id or not isinstance(enabled, bool):
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "provider_id, model_id and enabled are required",
                )
            result = self._provider_service().update_model_state(
                provider_id,
                model_id,
                ProviderModelStateUpdateRequest(enabled=enabled),
            )
            return self._success(
                result,
                {
                    "tool": name,
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "mutated": True,
                },
                [],
            )
        if name == "update_alert_email_config":
            result = alert_email.save_config(
                db,
                self.workspace_id,
                self.actor_id,
                enabled=bool(arguments.get("enabled")),
                smtp_host=str(arguments.get("smtp_host") or ""),
                smtp_port=int(arguments.get("smtp_port") or 465),
                smtp_security=str(arguments.get("smtp_security") or "ssl"),
                smtp_username=str(arguments.get("smtp_username") or ""),
                # Agent tools never accept credential material. Existing trusted-
                # UI password configuration is preserved.
                smtp_password=None,
                from_address=str(arguments.get("from_address") or ""),
                to_addresses=[
                    str(item)
                    for item in (arguments.get("to_addresses") or [])
                    if isinstance(item, str)
                ],
            ).view()
            return self._success(
                result,
                {"tool": name, "mutated": True, "secret_redacted": True},
                [],
            )
        if name == "set_functional_model_default":
            capability = str(arguments.get("capability") or "").strip()
            provider_id = arguments.get("provider_id")
            model_id = arguments.get("model_id")
            if (provider_id is None) != (model_id is None):
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "provider_id and model_id must both be set or both be null",
                )
            current = settings_service.get(
                FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY
            ).get("value")
            defaults = dict(current) if isinstance(current, dict) else {}
            if provider_id is None:
                defaults.pop(capability, None)
            else:
                provider = next(
                    (
                        item
                        for item in self._provider_service().list()
                        if item.id == str(provider_id)
                    ),
                    None,
                )
                if provider is None or not provider.enabled:
                    raise AppError(
                        409,
                        "functional_model_provider_unavailable",
                        "The selected Provider is not configured and enabled",
                    )
                spec = provider_type_spec(provider.provider_type)
                expected_role = "model" if capability == "chat" else capability
                declared_role = (
                    (provider.capabilities or {}).get("provider_role")
                    or (spec.role if spec is not None else None)
                )
                if declared_role != expected_role:
                    raise AppError(
                        409,
                        "functional_model_capability_mismatch",
                        "The selected Provider does not match the functional capability",
                        {
                            "expected_role": expected_role,
                            "provider_role": declared_role,
                        },
                    )
                states = (provider.capabilities or {}).get("model_states")
                if isinstance(states, dict) and states.get(str(model_id)) is False:
                    raise AppError(
                        409,
                        "provider_model_disabled",
                        "A disabled model cannot be selected as a functional default",
                    )
                defaults[capability] = {
                    "provider_id": str(provider_id),
                    "model_id": str(model_id),
                }
            item = settings_service.update(
                FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY,
                SettingUpdateRequest(value=defaults),
            )
            return self._success(
                {"key": item.key, "defaults": item.value},
                {"tool": name, "capability": capability, "mutated": True},
                [],
            )
        raise AppError(404, "unknown_management_tool", f"Unknown management tool: {name}")

    def _execute_model_invocation_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        from app.domain.schemas.research import ResearchJobView, ResearchRequest
        from app.providers.factory import (
            deep_research_provider_for_workspace,
            search_provider_for_workspace,
            transcription_provider_for_workspace,
            vision_provider_for_workspace,
        )
        from app.providers.ports.model import ProviderChatMessage
        from app.services.research import ResearchService

        db = self.extensions.db
        settings = self.settings or get_settings()
        if name == "transcribe_audio":
            file = self._require_workspace_file(str(arguments.get("file_id") or ""))
            if not file.mime_type.casefold().split(";", 1)[0].startswith("audio/"):
                raise AppError(
                    415,
                    "audio_required",
                    "transcribe_audio requires an audio file",
                )
            provider = transcription_provider_for_workspace(
                db,
                self.workspace_id,
                settings,
                provider_id=(
                    str(arguments["provider_id"])
                    if arguments.get("provider_id")
                    else None
                ),
                model_id=(
                    str(arguments["model_id"])
                    if arguments.get("model_id")
                    else None
                ),
            )
            if provider is None or not getattr(provider, "available", True):
                raise AppError(
                    503,
                    "transcription_provider_unavailable",
                    "No enabled ASR functional model is available",
                )
            content = self._read_stored_file_bytes(
                file,
                limit_bytes=50 * 1024 * 1024,
            )
            result = provider.transcribe(
                filename=file.original_name,
                mime_type=file.mime_type,
                content=content,
                language=(
                    str(arguments["language"]).strip()
                    if arguments.get("language")
                    else None
                ),
            )
            payload = {
                "file_id": file.id,
                "text": result.text,
                "language": result.language,
                "duration_seconds": result.duration_seconds,
                "provider_id": provider.provider_id,
                "model_id": provider.model_id,
                "usage": result.usage,
            }
            self.audit.record(
                actor_id=self.actor_id,
                action="agent.model.invoke.transcription",
                resource_type="file",
                resource_id=file.id,
                details={
                    "provider_id": provider.provider_id,
                    "model_id": provider.model_id,
                    "chat_session_id": chat_session_id,
                },
            )
            db.commit()
            return self._success(
                payload,
                {
                    "tool": name,
                    "provider_id": provider.provider_id,
                    "model_id": provider.model_id,
                },
                [],
            )
        if name == "analyze_image":
            file = self._require_workspace_file(str(arguments.get("file_id") or ""))
            content = self._read_stored_file_bytes(
                file,
                limit_bytes=AGENT_IMAGE_INPUT_MAX_BYTES,
            )
            mime_type, width, height = self._validated_image_bytes(
                content,
                file_id=file.id,
            )
            provider = vision_provider_for_workspace(
                db,
                self.workspace_id,
                settings,
                provider_id=(
                    str(arguments["provider_id"])
                    if arguments.get("provider_id")
                    else None
                ),
                model_id=(
                    str(arguments["model_id"])
                    if arguments.get("model_id")
                    else None
                ),
            )
            if not getattr(provider, "available", True) or not getattr(
                provider, "supports_image_input", False
            ):
                raise AppError(
                    503,
                    "vision_provider_unavailable",
                    "No enabled vision functional model is available",
                )
            encoded = base64.b64encode(content).decode("ascii")
            text_parts: list[str] = []
            for event in provider.stream_chat(
                [
                    ProviderChatMessage(
                        role="user",
                        content=str(arguments.get("prompt") or "").strip(),
                        content_parts=[
                            {
                                "type": "input_image",
                                "image_url": f"data:{mime_type};base64,{encoded}",
                                "detail": "auto",
                            }
                        ],
                    )
                ]
            ):
                if event.content:
                    text_parts.append(event.content)
            answer = "".join(text_parts).strip()
            if not answer:
                raise AppError(
                    502,
                    "vision_provider_empty_response",
                    "The vision model returned no description",
                )
            payload = {
                "file_id": file.id,
                "width": width,
                "height": height,
                "analysis": answer,
                "provider_id": provider.provider_id,
                "model_id": provider.model_id,
            }
            self.audit.record(
                actor_id=self.actor_id,
                action="agent.model.invoke.vision",
                resource_type="file",
                resource_id=file.id,
                details={
                    "provider_id": provider.provider_id,
                    "model_id": provider.model_id,
                    "chat_session_id": chat_session_id,
                },
            )
            db.commit()
            return self._success(
                payload,
                {
                    "tool": name,
                    "provider_id": provider.provider_id,
                    "model_id": provider.model_id,
                },
                [],
            )

        search_provider = search_provider_for_workspace(
            db,
            self.workspace_id,
            settings,
            route="auto",
        )
        if search_provider is None:
            raise AppError(
                503,
                "search_provider_unavailable",
                "No SearchProvider is configured for research",
            )
        research = ResearchService(
            db,
            self.workspace_id,
            self.actor_id,
            search_provider,
            deep_research_provider_for_workspace(
                db,
                self.workspace_id,
                settings,
            ),
            settings,
        )
        if name == "start_deep_research":
            job = research.create_research(
                ResearchRequest(
                    question=str(arguments.get("question") or "").strip(),
                    budget_cny=float(arguments.get("budget_cny") or 0),
                    allowed_domains=[
                        str(item)
                        for item in (arguments.get("allowed_domains") or [])
                        if isinstance(item, str)
                    ],
                )
            )
            payload = ResearchJobView.model_validate(job).model_dump(mode="json")
            payload["user_approval_required"] = job.status == "awaiting_approval"
            return self._success(
                payload,
                {
                    "tool": name,
                    "research_job_id": job.id,
                    "status": job.status,
                    "user_approval_required": payload["user_approval_required"],
                    "estimated_cost_cny": payload.get("estimated_cost_cny", 0.0),
                },
                [],
            )
        if name == "get_deep_research":
            job_id = str(arguments.get("research_job_id") or "").strip()
            job = research.get_research(job_id)
            return self._success(
                ResearchJobView.model_validate(job).model_dump(mode="json"),
                {"tool": name, "research_job_id": job.id, "status": job.status},
                [],
            )
        raise AppError(
            404,
            "unknown_model_invocation_tool",
            f"Unknown model invocation tool: {name}",
        )

    def _execute_chart_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        if name == "read_chart":
            chart_id = str(arguments.get("chart_id") or "").strip()
            part = next(
                (
                    item
                    for item in self.extensions.db.scalars(
                        select(MessagePartRecord).where(
                            MessagePartRecord.workspace_id == self.workspace_id,
                            MessagePartRecord.part_type == "chart",
                        )
                    ).all()
                    if isinstance(item.data, dict)
                    and item.data.get("chart_id") == chart_id
                ),
                None,
            )
            if part is None:
                raise AppError(404, "chart_not_found", "Chart was not found")
            return self._success(
                dict(part.data or {}),
                {"tool": name, "chart_id": chart_id},
                [],
            )

        chart_type = str(arguments.get("type") or "").strip()
        title = str(arguments.get("title") or "").strip()
        labels = arguments.get("labels")
        raw_series = arguments.get("series")
        if (
            chart_type not in {"pie", "line", "bar"}
            or not title
            or not isinstance(labels, list)
            or not labels
            or len(labels) > 100
            or not isinstance(raw_series, list)
            or not raw_series
            or len(raw_series) > 8
        ):
            raise AppError(
                422,
                "invalid_chart_data",
                "Chart type, title, labels, and series are required",
            )
        clean_labels = [str(item)[:160] for item in labels]
        palette = [
            "#4F46E5",
            "#0EA5E9",
            "#10B981",
            "#F59E0B",
            "#EF4444",
            "#8B5CF6",
            "#EC4899",
            "#64748B",
        ]
        clean_series: list[dict[str, Any]] = []
        for index, item in enumerate(raw_series):
            if not isinstance(item, dict):
                raise AppError(422, "invalid_chart_data", "Each series must be an object")
            values = item.get("values")
            if not isinstance(values, list) or len(values) != len(clean_labels):
                raise AppError(
                    422,
                    "invalid_chart_data",
                    "Every series must have exactly one numeric value per label",
                )
            if any(
                not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(float(value))
                for value in values
            ):
                raise AppError(
                    422,
                    "invalid_chart_data",
                    "Chart values must be finite numbers",
                )
            color = str(item.get("color") or palette[index % len(palette)])
            if (
                len(color) != 7
                or not color.startswith("#")
                or any(character not in "0123456789abcdefABCDEF" for character in color[1:])
            ):
                raise AppError(
                    422,
                    "invalid_chart_color",
                    "Chart colors must use #RRGGBB format",
                )
            clean_series.append(
                {
                    "name": str(item.get("name") or f"Series {index + 1}")[:120],
                    "values": [float(value) for value in values],
                    "color": color.upper(),
                }
            )
        if chart_type == "pie" and len(clean_series) != 1:
            raise AppError(
                422,
                "invalid_chart_data",
                "Pie charts require exactly one series",
            )
        extrema = [
            value
            for series in clean_series
            for value in series["values"]
        ]
        summary = (
            f"{title}: {len(clean_labels)} categories, {len(clean_series)} series; "
            f"minimum {min(extrema):g}, maximum {max(extrema):g}."
        )
        data = {
            "chart_id": str(uuid4()),
            "chart_type": chart_type,
            "title": title[:240],
            "labels": clean_labels,
            "series": clean_series,
            "show_legend": bool(arguments.get("show_legend", True)),
            "show_values": bool(arguments.get("show_values", False)),
            "summary": summary,
            "source": "agent_structured_data",
        }
        self.audit.record(
            actor_id=self.actor_id,
            action="agent.chart.create",
            resource_type="chart",
            resource_id=data["chart_id"],
            details={
                "chart_type": chart_type,
                "category_count": len(clean_labels),
                "series_count": len(clean_series),
            },
        )
        self.extensions.db.commit()
        return self._success(
            data,
            {
                "tool": name,
                "chart_id": data["chart_id"],
                "artifact": {
                    "type": "chart",
                    "status": "completed",
                    "data": data,
                },
            },
            [],
        )

    def _emit_custom_component_part(
        self,
        *,
        component_type: str,
        props: dict[str, Any],
        component_id: str | None,
        allowed_events: list[str] | None,
        schema_version: str,
    ) -> dict[str, Any]:
        """Publish an authorized custom (third-party) trusted component.

        Resolves the registered plugin by ``component_id`` (passed as
        ``component_type``), requires a current, authorized manifest, and
        validates ``props`` against the manifest's ``data_schema`` via
        ``ComponentService.create_artifact``. The isolated browser renderer
        is not configured, so the result is delivered as a safe
        ``sandbox_artifact`` downgrade (runtime_status=unavailable); no
        untrusted code enters the host DOM.
        """
        from app.domain.models import PluginRecord
        from app.domain.schemas.components import ComponentArtifactRequest
        from app.repositories.domain import PluginRepository

        service = self._component_service()
        plugins = PluginRepository(self.extensions.db, self.workspace_id)
        plugin = self.extensions.db.scalar(
            plugins.query().where(PluginRecord.plugin_key == component_type)
        )
        if plugin is None or plugin.plugin_type != "trusted_component":
            raise AppError(
                422,
                "canvas_component_type_unsupported",
                f"Component type '{component_type}' is not a registered "
                "trusted component in this workspace; register and authorize "
                "its Manifest first.",
            )
        manifest = service.manifests.current(plugin)
        if manifest is None:
            raise AppError(
                409,
                "component_manifest_required",
                "The component has no current manifest",
            )
        # create_artifact re-checks enablement, authorization, schema and
        # records the audit entry. It raises on stale authorization.
        artifact = service.create_artifact(
            plugin.id,
            ComponentArtifactRequest(
                manifest_version_id=manifest.id,
                data=props,
            ),
        )
        events = allowed_events
        if events is None:
            events = ["submit"]
        cleaned_events: list[str] = []
        for event in events:
            if isinstance(event, str) and event and event not in cleaned_events:
                cleaned_events.append(event[:80])
        data = {
            "component_type": component_type,
            "component_id": component_id or f"{component_type}_{manifest.version}",
            "schema_version": (schema_version or manifest.version)[:32],
            "props": props,
            "allowed_events": cleaned_events[:10],
            "delivery_mode": artifact.delivery_mode,
            "runtime_status": artifact.runtime_status,
            "manifest_version_id": artifact.manifest_version_id,
            "sandbox_executed": artifact.sandbox_executed,
        }
        if artifact.sandbox_artifact is not None:
            data["sandbox_artifact"] = artifact.sandbox_artifact
        title = props.get("title") or props.get("name") or component_type
        return {
            "type": "component",
            "status": "completed",
            "content": str(title)[:500],
            "data": data,
        }

    def _execute_canvas_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        from app.services.canvas_cards import (
            build_magic_card_part,
            build_trusted_component_part,
            get_render_contract,
        )

        if name == "canvas_get_render_contract":
            contract = get_render_contract(arguments if isinstance(arguments, dict) else None)
            return self._success(
                contract,
                {
                    "canvas": True,
                    "tool": name,
                    "slot": contract.get("slot"),
                    "available_width": contract.get("available_width"),
                },
                [],
            )
        if name == "canvas_emit_trusted_component":
            from app.services.canvas_cards import CHANNEL_A_TYPES
            from app.services.components import BUILTIN_COMPONENT_IDS

            component_type = arguments.get("component_type")
            props = arguments.get("props")
            if not isinstance(component_type, str) or not component_type:
                raise AppError(422, "invalid_tool_arguments", "component_type is required")
            if not isinstance(props, dict):
                raise AppError(422, "invalid_tool_arguments", "props must be an object")
            allowed_events = arguments.get("allowed_events")
            if allowed_events is not None and not isinstance(allowed_events, list):
                raise AppError(422, "invalid_tool_arguments", "allowed_events must be an array")

            is_builtin = (
                component_type in CHANNEL_A_TYPES
                and component_type in BUILTIN_COMPONENT_IDS
            )
            if is_builtin:
                part = build_trusted_component_part(
                    component_type=component_type,
                    props=props,
                    component_id=arguments.get("component_id")
                    if isinstance(arguments.get("component_id"), str)
                    else None,
                    allowed_events=[str(item) for item in allowed_events]
                    if isinstance(allowed_events, list)
                    else None,
                    schema_version=arguments.get("schema_version")
                    if isinstance(arguments.get("schema_version"), str)
                    else "1.0",
                )
            else:
                # Custom third-party component: resolve the authorized manifest
                # in this workspace and validate props against its data_schema.
                # The isolated browser renderer is not configured, so the
                # artifact is delivered as a safe sandbox_artifact downgrade.
                part = self._emit_custom_component_part(
                    component_type=component_type,
                    props=props,
                    component_id=arguments.get("component_id")
                    if isinstance(arguments.get("component_id"), str)
                    else None,
                    allowed_events=[str(item) for item in allowed_events]
                    if isinstance(allowed_events, list)
                    else None,
                    schema_version=arguments.get("schema_version")
                    if isinstance(arguments.get("schema_version"), str)
                    else "1.0",
                )
            self.audit.record(
                actor_id=self.actor_id,
                action="canvas.emit_trusted_component",
                resource_type="chat_session",
                resource_id=chat_session_id,
                details={
                    "component_type": component_type,
                    "component_id": (part.get("data") or {}).get("component_id"),
                    "custom_component": not is_builtin,
                },
            )
            self.extensions.db.commit()
            return self._success(
                {
                    "published": True,
                    "channel": "declarative" if is_builtin else "sandbox_artifact",
                    "component_type": component_type,
                    "part_type": "component",
                    "component_id": (part.get("data") or {}).get("component_id"),
                    "runtime_status": (
                        "builtin_registry_validated"
                        if is_builtin
                        else (part.get("data") or {}).get("runtime_status")
                    ),
                },
                {
                    "canvas": True,
                    "tool": name,
                    "artifact": part,
                },
                [],
            )
        if name == "canvas_emit_magic_card":
            title = arguments.get("title")
            if not isinstance(title, str) or not title.strip():
                raise AppError(422, "invalid_tool_arguments", "title is required")
            scope: dict[str, Any] = {}
            if isinstance(arguments.get("goal_id"), str):
                scope["goal_id"] = arguments["goal_id"]
            if isinstance(arguments.get("node_id"), str):
                scope["node_id"] = arguments["node_id"]
            part = build_magic_card_part(
                title=title.strip(),
                fallback_text=arguments.get("fallback_text")
                if isinstance(arguments.get("fallback_text"), str)
                else None,
                card_id=arguments.get("card_id")
                if isinstance(arguments.get("card_id"), str)
                else None,
                version=int(arguments.get("version") or 1),
                preferred_height=int(arguments["preferred_height"])
                if isinstance(arguments.get("preferred_height"), int)
                else None,
                preview_html=arguments.get("preview_html")
                if isinstance(arguments.get("preview_html"), str)
                else None,
                scope=scope or None,
            )
            self.audit.record(
                actor_id=self.actor_id,
                action="canvas.emit_magic_card",
                resource_type="chat_session",
                resource_id=chat_session_id,
                details={
                    "card_id": (part.get("data") or {}).get("card_id"),
                    "card_instance_id": (part.get("data") or {}).get("card_instance_id"),
                    "status": (part.get("data") or {}).get("status"),
                },
            )
            self.extensions.db.commit()
            return self._success(
                {
                    "published": True,
                    "channel": "react_sandbox",
                    "runtime_available": bool((part.get("data") or {}).get("origin_verified")),
                    "part_type": "magic_card",
                    "card_instance_id": (part.get("data") or {}).get("card_instance_id"),
                    "status": (part.get("data") or {}).get("status"),
                    "reason": (part.get("data") or {}).get("reason"),
                },
                {
                    "canvas": True,
                    "tool": name,
                    "artifact": part,
                },
                [],
            )
        return self._failure("unknown_canvas_tool", f"Unknown canvas tool {name}")

    def _execute_memory_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        if self.memory_tools is None:
            return self._failure("memory_tools_unavailable", "Memory tools are unavailable")
        if name == "search_conversation_history":
            query = arguments.get("query")
            if not isinstance(query, str) or not query.strip():
                raise AppError(422, "invalid_tool_arguments", "query is required")
            top_k = arguments.get("top_k", 8)
            if not isinstance(top_k, int):
                top_k = 8
            result = self.memory_tools.search_conversation_history(
                query=query.strip(),
                goal_id=arguments.get("goal_id") if isinstance(arguments.get("goal_id"), str) else None,
                session_id=(
                    arguments.get("session_id")
                    if isinstance(arguments.get("session_id"), str)
                    else chat_session_id
                ),
                top_k=min(20, max(1, top_k)),
            )
            return self._success(result, {"hit_count": len(result.get("hits") or [])}, [])
        if name == "read_conversation_segment":
            session_id = arguments.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise AppError(422, "invalid_tool_arguments", "session_id is required")
            limit = arguments.get("limit", 12)
            if not isinstance(limit, int):
                limit = 12
            result = self.memory_tools.read_conversation_segment(
                session_id=session_id,
                around_message_id=(
                    arguments.get("around_message_id")
                    if isinstance(arguments.get("around_message_id"), str)
                    else None
                ),
                limit=min(40, max(1, limit)),
            )
            return self._success(result, {"message_count": len(result.get("messages") or [])}, [])
        if name == "get_memory_evidence":
            memory_id = arguments.get("memory_id")
            if not isinstance(memory_id, str) or not memory_id:
                raise AppError(422, "invalid_tool_arguments", "memory_id is required")
            result = self.memory_tools.get_memory_evidence(memory_id)
            return self._success(result, {"memory_id": memory_id}, [])
        if name == "propose_memory_draft":
            from app.domain.schemas.management import MemoryDraftCreateRequest

            content = arguments.get("content")
            if not isinstance(content, str) or not content.strip():
                raise AppError(422, "invalid_tool_arguments", "content is required")
            payload = MemoryDraftCreateRequest(
                operation=arguments.get("operation") or "CREATE",
                memory_type=arguments.get("memory_type") or "ai_observation",
                title=arguments.get("title") or "",
                content=content.strip(),
                goal_id=arguments.get("goal_id") if isinstance(arguments.get("goal_id"), str) else None,
                node_id=arguments.get("node_id") if isinstance(arguments.get("node_id"), str) else None,
                session_id=arguments.get("session_id")
                if isinstance(arguments.get("session_id"), str)
                else chat_session_id,
                target_memory_id=arguments.get("target_memory_id")
                if isinstance(arguments.get("target_memory_id"), str)
                else None,
                confidence=float(arguments.get("confidence") or 0.55),
                auto_commit=bool(arguments.get("auto_commit") or False),
                created_by="learning_agent",
            )
            draft = self.memory_tools.create_draft(payload)
            data = draft.model_dump(mode="json") if hasattr(draft, "model_dump") else dict(draft)
            return self._success(data, {"draft_id": data.get("id"), "status": data.get("status")}, [])
        return self._failure("unknown_memory_tool", f"Unknown memory tool {name}")

    @staticmethod
    def _parse_arguments(raw_arguments: Any) -> dict[str, Any]:
        if not isinstance(raw_arguments, str):
            raise AppError(422, "invalid_tool_arguments", "Tool arguments must be a JSON object")
        try:
            value = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise AppError(422, "invalid_tool_arguments", "Tool arguments are not valid JSON") from exc
        if not isinstance(value, dict):
            raise AppError(422, "invalid_tool_arguments", "Tool arguments must be a JSON object")
        return value

    @staticmethod
    def _get_current_time(arguments: dict[str, Any]) -> dict[str, Any]:
        """Return a host-clock snapshot for the requested IANA timezone."""

        unknown = set(arguments) - {"timezone"}
        if unknown:
            raise AppError(
                422,
                "invalid_tool_arguments",
                "get_current_time only accepts an optional timezone",
            )
        raw_timezone = arguments.get("timezone", DEFAULT_CLOCK_TIMEZONE)
        if raw_timezone is None or raw_timezone == "":
            raw_timezone = DEFAULT_CLOCK_TIMEZONE
        if not isinstance(raw_timezone, str) or not (1 <= len(raw_timezone.strip()) <= 80):
            raise AppError(
                422,
                "invalid_tool_arguments",
                "timezone must be an IANA name such as Asia/Shanghai or UTC",
            )
        timezone_name = raw_timezone.strip()
        if timezone_name.casefold() in {"utc", "z", "gmt"}:
            timezone_name = "UTC"
            zone = timezone.utc
        else:
            try:
                zone = ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError as exc:
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    f"Unknown IANA timezone '{timezone_name}'",
                ) from exc
        now_local = datetime.now(zone)
        now_utc = now_local.astimezone(timezone.utc)
        offset = now_local.utcoffset()
        if offset is None:
            utc_offset = "+00:00"
        else:
            total_minutes = int(offset.total_seconds() // 60)
            sign = "+" if total_minutes >= 0 else "-"
            absolute = abs(total_minutes)
            utc_offset = f"{sign}{absolute // 60:02d}:{absolute % 60:02d}"
        return {
            "timezone": timezone_name,
            "utc_offset": utc_offset,
            "iso_local": now_local.isoformat(timespec="seconds"),
            "iso_utc": now_utc.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "unix_timestamp": int(now_local.timestamp()),
            "date": now_local.date().isoformat(),
            "time": now_local.time().replace(microsecond=0).isoformat(),
            "weekday": now_local.strftime("%A"),
            "weekday_zh": (
                "星期一",
                "星期二",
                "星期三",
                "星期四",
                "星期五",
                "星期六",
                "星期日",
            )[now_local.weekday()],
            "source": "learngraph_host_clock",
        }

    def _search(
        self,
        arguments: dict[str, Any],
        allowed_domains: list[str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        query = arguments.get("query")
        if set(arguments) != {"query"} or not isinstance(query, str) or not (1 <= len(query.strip()) <= 500):
            raise AppError(422, "invalid_tool_arguments", "search_web requires one query from 1 to 500 characters")
        if not self._search_available:
            raise AppError(503, "search_provider_unavailable", "SearchProvider is unavailable")
        domains = {item.strip().casefold() for item in allowed_domains if item.strip()}
        try:
            results = self.search_provider.search(query.strip(), 5, allowed_domains=domains or None)
        except SearchProviderTimeout as exc:
            raise AppError(504, "search_provider_timeout", "SearchProvider timed out") from exc
        except SearchProviderError as exc:
            raise AppError(502, "search_provider_failed", "SearchProvider failed") from exc
        sources = [item.model_dump(mode="json") for item in results]
        return (
            {
                "query": query.strip(),
                "results": [
                    {"title": item["title"], "url": item["url"], "snippet": item["snippet"]}
                    for item in sources
                ],
            },
            sources,
        )

    def _fetch_web_page(
        self,
        arguments: dict[str, Any],
        allowed_domains: list[str],
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        url = arguments.get("url")
        search_has_fetch = callable(getattr(self.search_provider, "fetch", None))
        fetch_available = self._fetch_available
        if (
            set(arguments) != {"url"}
            or not isinstance(url, str)
            or not url.strip()
            or (not search_has_fetch and not fetch_available)
        ):
            raise AppError(
                422,
                "invalid_tool_arguments",
                "fetch_web_page requires one URL and a configured web fetcher",
            )
        domains = {
            item.strip().casefold()
            for item in allowed_domains
            if isinstance(item, str) and item.strip()
        }
        if not domains:
            raise AppError(
                403,
                "fetch_domain_not_authorized",
                "Full-page extraction requires an explicitly authorized domain",
            )
        target_url = url.strip()
        # The requested URL's host must sit inside the authorized domain set
        # for *every* fetcher we might route to; this is a hard SSRF/authorization
        # gate and is never bypassed by the Firecrawl→Qwen fallback below.
        try:
            require_public_http_url(target_url, domains)
        except UnsafeFetchURL as exc:
            raise AppError(
                422,
                "fetch_url_blocked",
                "The requested URL is outside the authorized public domains",
            ) from exc

        document: Any | None = None
        fetch_provider_id: str | None = None
        # Firecrawl-style FetchProvider is preferred. A soft transport/content
        # failure degrades to the Qwen companion when one is available; an
        # UnsafeFetchURL on the final URL remains a hard gate (no fallback).
        if fetch_available:
            try:
                document = self.fetch_provider.fetch(target_url)
                require_public_http_url(document.final_url, domains)
                fetch_provider_id = self.fetch_provider.provider_id
            except UnsafeFetchURL as exc:
                raise AppError(
                    422,
                    "fetch_url_blocked",
                    "The fetched page is outside the authorized public domains",
                ) from exc
            except FetchProviderTimeout:
                document = None
            except FetchProviderError:
                document = None
        if document is None and search_has_fetch:
            try:
                document = self.search_provider.fetch(target_url)
                require_public_http_url(document.final_url, domains)
                fetch_provider_id = self.search_provider.provider_id
            except UnsafeFetchURL as exc:
                raise AppError(
                    422,
                    "fetch_url_blocked",
                    "The fetched page is outside the authorized public domains",
                ) from exc
            except FetchProviderTimeout as exc:
                raise AppError(504, "fetch_provider_timeout", "Web extractor timed out") from exc
            except FetchProviderError as exc:
                raise AppError(502, "fetch_provider_failed", "Web extractor failed") from exc
        if document is None:
            raise AppError(
                502,
                "fetch_provider_failed",
                "Configured web fetcher is unavailable or failed",
            )
        result = {
            "url": document.final_url,
            "title": document.title,
            "content": document.content,
            "content_type": document.content_type,
        }
        served_by = fetch_provider_id or getattr(
            self.search_provider, "provider_id", "fetch_provider"
        )
        return self._success(
            result,
            {
                "url": document.final_url,
                "content_chars": len(document.content),
                "provider_id": served_by,
            },
            [],
        )

    def _search_images(
        self,
        arguments: dict[str, Any],
        allowed_domains: list[str],
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        unknown = set(arguments) - {"query", "image_url"}
        query = arguments.get("query")
        image_url = arguments.get("image_url")
        if (
            unknown
            or not isinstance(query, str)
            or not 1 <= len(query.strip()) <= 500
            or (image_url is not None and not isinstance(image_url, str))
            or not callable(getattr(self.search_provider, "image_search", None))
        ):
            raise AppError(
                422,
                "invalid_tool_arguments",
                "search_images requires a query and an optional public image URL",
            )
        try:
            if isinstance(image_url, str):
                domains = {
                    item.strip().casefold()
                    for item in allowed_domains
                    if isinstance(item, str) and item.strip()
                }
                require_public_http_url(image_url.strip(), domains)
            images = self.search_provider.image_search(
                query.strip(),
                image_url=image_url.strip() if isinstance(image_url, str) else None,
            )
        except UnsafeFetchURL as exc:
            raise AppError(
                422,
                "image_search_url_blocked",
                "The reverse-image URL is not a safe public URL",
            ) from exc
        except SearchProviderTimeout as exc:
            raise AppError(504, "search_provider_timeout", "Image search timed out") from exc
        except SearchProviderError as exc:
            raise AppError(502, "search_provider_failed", "Image search failed") from exc
        result = {"query": query.strip(), "images": images}
        return self._success(
            result,
            {
                "result_count": len(images),
                "provider_id": self.search_provider.provider_id,
            },
            [],
        )

    def _parallel_web_research(
        self,
        arguments: dict[str, Any],
        allowed_domains: list[str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        tasks = arguments.get("tasks")
        if (
            set(arguments) != {"tasks"}
            or not isinstance(tasks, list)
            or not 2 <= len(tasks) <= MAX_PARALLEL_RESEARCH_CHILDREN
            or not all(isinstance(task, str) and 1 <= len(task.strip()) <= 500 for task in tasks)
        ):
            raise AppError(
                422,
                "invalid_tool_arguments",
                "parallel_web_research requires 2 to 4 bounded query strings",
            )
        if len({task.strip().casefold() for task in tasks}) != len(tasks):
            raise AppError(422, "invalid_tool_arguments", "Parallel research tasks must be distinct")
        parent_run_id = str(uuid4())
        children: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []

        def run_child(index: int, query: str) -> tuple[int, str, dict[str, Any], list[dict[str, Any]]]:
            result, child_sources = self._search({"query": query}, allowed_domains)
            return index, query, result, child_sources

        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_RESEARCH_CHILDREN, len(tasks))) as executor:
            futures = {
                executor.submit(run_child, index, task.strip()): (index, task.strip())
                for index, task in enumerate(tasks, start=1)
            }
            for future in as_completed(futures):
                index, query = futures[future]
                child_id = str(uuid4())
                try:
                    _, _, result, child_sources = future.result()
                    children.append(
                        {
                            "child_run_id": child_id,
                            "index": index,
                            "query": query,
                            "status": "completed",
                            "result_count": len(child_sources),
                            "results": result["results"],
                        }
                    )
                    sources.extend(child_sources)
                except AppError as exc:
                    children.append(
                        {
                            "child_run_id": child_id,
                            "index": index,
                            "query": query,
                            "status": "failed",
                            "error_code": exc.code,
                        }
                    )
                except Exception:
                    # A single provider/runtime fault must not discard the
                    # completed siblings or expose an internal traceback to
                    # the model.  The parent remains an auditable aggregate.
                    children.append(
                        {
                            "child_run_id": child_id,
                            "index": index,
                            "query": query,
                            "status": "failed",
                            "error_code": "search_child_failed",
                        }
                    )
        children.sort(key=lambda item: int(item["index"]))
        self.audit.record(
            actor_id=self.actor_id,
            action="agent.parallel_web_research",
            resource_type="agent_parent_run",
            resource_id=parent_run_id,
            outcome="success" if any(item["status"] == "completed" for item in children) else "failure",
            details={
                "child_count": len(children),
                "completed_children": sum(item["status"] == "completed" for item in children),
            },
        )
        self.extensions.db.commit()
        return {"parent_run_id": parent_run_id, "child_runs": children}, sources

    def _require_workspace_session(self, session_id: str) -> ChatSession:
        session = self.extensions.db.scalar(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.workspace_id == self.workspace_id,
            )
        )
        if session is None:
            raise AppError(
                404,
                "session_not_found",
                "The chat session does not exist in this workspace",
                {"session_id": session_id},
            )
        return session

    def _require_workspace_file(self, file_id: str) -> FileRecord:
        file = self.extensions.db.scalar(
            select(FileRecord).where(
                FileRecord.id == file_id,
                FileRecord.workspace_id == self.workspace_id,
            )
        )
        if file is None:
            raise AppError(
                404,
                "file_not_found",
                "The file does not exist in this workspace",
                {"file_id": file_id},
            )
        return file

    @staticmethod
    def _validated_image_bytes(
        content: bytes, *, file_id: str
    ) -> tuple[str, int, int]:
        """Decode image bytes before they cross a model/provider boundary.

        Mirrors ChatService multimodal validation: a renamed binary blob must
        never be presented to a remote model or image provider as an image.
        """

        try:
            with Image.open(BytesIO(content)) as image:
                detected_mime = AGENT_IMAGE_FORMAT_MIME_TYPES.get(
                    (image.format or "").upper()
                )
                width, height = image.size
                if (
                    detected_mime is None
                    or width < 1
                    or height < 1
                    or width * height > AGENT_IMAGE_INPUT_MAX_PIXELS
                ):
                    raise ValueError("unsupported or oversized image dimensions")
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
        except (
            UnidentifiedImageError,
            Image.DecompressionBombError,
            OSError,
            ValueError,
        ) as exc:
            raise AppError(
                415,
                "invalid_image_file",
                "The stored image bytes could not be decoded safely",
                {"file_id": file_id},
            ) from exc
        return detected_mime, width, height

    def _read_stored_file_bytes(self, file: FileRecord, *, limit_bytes: int) -> bytes:
        if file.storage_status != "stored":
            raise AppError(
                409,
                "file_unavailable",
                "The file is not available in object storage",
                {"file_id": file.id, "storage_status": file.storage_status},
            )
        if file.size_bytes > limit_bytes:
            raise AppError(
                413,
                "file_too_large",
                "The file exceeds the readable size limit for this tool",
                {"file_id": file.id, "max_bytes": limit_bytes},
            )
        storage = object_storage_provider(
            self.extensions.db, self.workspace_id, self.settings or get_settings()
        )
        try:
            return storage.read_bytes(file.object_key, limit_bytes=limit_bytes)
        except AppError as exc:
            raise AppError(
                409,
                "file_unavailable",
                "The file could not be read from object storage",
                {"file_id": file.id},
            ) from exc

    def _materialize_session_file(
        self, chat_session_id: str, file: FileRecord
    ) -> dict[str, Any]:
        """Put a durable file into the session workspace inputs/ tree."""

        if file.storage_status != "stored":
            raise AppError(
                409,
                "file_unavailable",
                "The file is not available in object storage",
                {"file_id": file.id, "storage_status": file.storage_status},
            )
        if self.sandbox is not None and self.sandbox_authorized:
            views = self.sandbox.seed_chat_attachments(
                chat_session_id=chat_session_id,
                files=[file],
                include_images=True,
            )
            view = views[0] if views else None
        else:
            workspace = SessionWorkspaceService(
                self.extensions.db,
                self.workspace_id,
                self.actor_id,
                self.settings or get_settings(),
            )
            view = workspace.link_file_record(
                chat_session_id=chat_session_id,
                file=file,
                role="input",
                source="chat_attachment",
            )
        if view is None:
            raise AppError(
                409,
                "file_unavailable",
                "The file could not be materialized into the session workspace",
                {"file_id": file.id},
            )
        self.extensions.db.commit()
        return view

    def _list_session_files(
        self, arguments: dict[str, Any], *, chat_session_id: str
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        raw_session = arguments.get("session_id")
        target_session_id = (
            raw_session.strip()
            if isinstance(raw_session, str) and raw_session.strip()
            else chat_session_id
        )
        self._require_workspace_session(target_session_id)
        cross_session = target_session_id != chat_session_id
        db = self.extensions.db

        entries: dict[str, dict[str, Any]] = {}
        attachment_rows = db.execute(
            select(FileRecord, Message, FileReference)
            .join(FileReference, FileReference.file_id == FileRecord.id)
            .join(Message, FileReference.target_id == Message.id)
            .where(
                FileReference.workspace_id == self.workspace_id,
                FileReference.target_type == "message",
                Message.session_id == target_session_id,
                FileRecord.workspace_id == self.workspace_id,
            )
            .order_by(Message.created_at)
        ).all()
        for file, message, reference in attachment_rows:
            entries.setdefault(
                file.id,
                {
                    "file_id": file.id,
                    "filename": file.original_name,
                    "mime_type": file.mime_type,
                    "size_bytes": file.size_bytes,
                    # ImageGenerationService also records generated files as
                    # message references; keep their origin distinguishable
                    # from files the user uploaded.
                    "origin": (
                        "generated_image"
                        if reference.relation == "generated_image"
                        else "user_attachment"
                    ),
                    "relation": reference.relation,
                    "message_id": message.id,
                    "is_image": is_image_attachment(file),
                    "storage_status": file.storage_status,
                    "created_at": (
                        file.created_at.isoformat() if file.created_at else None
                    ),
                },
            )
        generated_rows = db.execute(
            select(FileRecord, ImageGenerationTask)
            .join(ImageGenerationTask, ImageGenerationTask.file_id == FileRecord.id)
            .where(
                ImageGenerationTask.workspace_id == self.workspace_id,
                ImageGenerationTask.session_id == target_session_id,
                ImageGenerationTask.status == "completed",
            )
            .order_by(ImageGenerationTask.created_at)
        ).all()
        for file, task in generated_rows:
            entries.setdefault(
                file.id,
                {
                    "file_id": file.id,
                    "filename": file.original_name,
                    "mime_type": file.mime_type,
                    "size_bytes": file.size_bytes,
                    "origin": "generated_image",
                    "message_id": task.message_id,
                    "prompt_summary": task.prompt_summary or None,
                    "is_image": True,
                    "storage_status": file.storage_status,
                    "created_at": (
                        task.created_at.isoformat() if task.created_at else None
                    ),
                },
            )
        listed = sorted(entries.values(), key=lambda item: item["created_at"] or "")
        truncated = len(listed) > SESSION_FILE_LIST_MAX
        if truncated:
            listed = listed[-SESSION_FILE_LIST_MAX:]
        if cross_session:
            self.audit.record(
                actor_id=self.actor_id,
                action="agent.session_files.cross_session_list",
                resource_type="chat_session",
                resource_id=target_session_id,
                details={
                    "requesting_session_id": chat_session_id,
                    "file_count": len(listed),
                },
            )
            db.commit()
        return self._success(
            {
                "session_id": target_session_id,
                "cross_session": cross_session,
                "count": len(listed),
                "truncated": truncated,
                "files": listed,
                "hint": (
                    "Use read_session_file(file_id=...) to view a file. To modify "
                    "an existing image, pass its file_id in "
                    "generate_image.source_file_ids."
                ),
            },
            {
                "tool": "list_session_files",
                "session_id": target_session_id,
                "cross_session": cross_session,
                "file_count": len(listed),
            },
            [],
        )

    def _read_session_file(
        self,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
        model_supports_image_input: bool,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        file_id_raw = arguments.get("file_id")
        if not isinstance(file_id_raw, str) or not file_id_raw.strip():
            raise AppError(422, "invalid_tool_arguments", "file_id is required")
        target_raw = arguments.get("target")
        target = (
            target_raw.strip().casefold()
            if isinstance(target_raw, str) and target_raw.strip()
            else "context"
        )
        if target not in {"context", "workspace"}:
            raise AppError(
                422,
                "invalid_tool_arguments",
                "target must be 'context' or 'workspace'",
            )
        file = self._require_workspace_file(file_id_raw.strip())
        base_result = {
            "file_id": file.id,
            "filename": file.original_name,
            "mime_type": file.mime_type,
            "size_bytes": file.size_bytes,
        }

        if target == "workspace":
            view = self._materialize_session_file(chat_session_id, file)
            return self._success(
                {
                    **base_result,
                    "workspace_path": view.get("path"),
                    "note": (
                        "File materialized into the session workspace. Use "
                        "sandbox_list_files / sandbox_exec to process it "
                        "(sandbox_read_file only decodes UTF-8 text)."
                    ),
                },
                {
                    "tool": "read_session_file",
                    "file_id": file.id,
                    "workspace_path": view.get("path"),
                },
                [],
            )

        if is_image_attachment(file) or file.mime_type.casefold().startswith("image/"):
            if file.size_bytes > AGENT_IMAGE_INPUT_MAX_BYTES:
                view = self._materialize_session_file(chat_session_id, file)
                return self._success(
                    {
                        **base_result,
                        "image_attached": False,
                        "workspace_path": view.get("path"),
                        "note": (
                            "Image exceeds the direct model input limit; it was "
                            "materialized into the session workspace instead."
                        ),
                    },
                    {
                        "tool": "read_session_file",
                        "file_id": file.id,
                        "workspace_path": view.get("path"),
                    },
                    [],
                )
            content = self._read_stored_file_bytes(
                file, limit_bytes=AGENT_IMAGE_INPUT_MAX_BYTES
            )
            mime_type, width, height = self._validated_image_bytes(
                content, file_id=file.id
            )
            if not model_supports_image_input:
                return self._success(
                    {
                        **base_result,
                        "mime_type": mime_type,
                        "width": width,
                        "height": height,
                        "image_attached": False,
                        "note": (
                            "The active chat model does not support image input, "
                            "so the picture cannot be shown to you directly. You "
                            "can still pass this file_id in "
                            "generate_image.source_file_ids to edit it, or call "
                            "read_session_file with target='workspace' to process "
                            "it with sandbox tools."
                        ),
                    },
                    {
                        "tool": "read_session_file",
                        "file_id": file.id,
                        "image_attached": False,
                    },
                    [],
                )
            encoded = base64.b64encode(content).decode("ascii")
            image_part = {
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{encoded}",
                "detail": "auto",
                "file_id": file.id,
                "original_name": file.original_name,
            }
            return self._success(
                {
                    **base_result,
                    "mime_type": mime_type,
                    "width": width,
                    "height": height,
                    "image_attached": True,
                    "note": (
                        "The image is attached to the conversation right after "
                        "this tool result; look at it directly. To edit it, call "
                        "generate_image with source_file_ids=[this file_id]."
                    ),
                },
                {
                    "tool": "read_session_file",
                    "file_id": file.id,
                    "image_attached": True,
                    # Ephemeral model input consumed by the chat tool loop; it is
                    # stripped before the meta is persisted or streamed.
                    "model_image_parts": [image_part],
                },
                [],
            )

        if file.size_bytes <= SESSION_FILE_TEXT_MAX_BYTES:
            content = self._read_stored_file_bytes(
                file, limit_bytes=SESSION_FILE_TEXT_MAX_BYTES
            )
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                text = None
            if text is not None:
                text_truncated = len(text) > SESSION_FILE_TEXT_MAX_CHARS
                return self._success(
                    {
                        **base_result,
                        "encoding": "utf-8",
                        "text": text[:SESSION_FILE_TEXT_MAX_CHARS],
                        "text_truncated": text_truncated,
                    },
                    {
                        "tool": "read_session_file",
                        "file_id": file.id,
                        "text_truncated": text_truncated,
                    },
                    [],
                )

        view = self._materialize_session_file(chat_session_id, file)
        return self._success(
            {
                **base_result,
                "workspace_path": view.get("path"),
                "note": (
                    "The file is binary or too large to inline; it was "
                    "materialized into the session workspace. Use "
                    "sandbox_list_files / sandbox_exec to process it."
                ),
            },
            {
                "tool": "read_session_file",
                "file_id": file.id,
                "workspace_path": view.get("path"),
            },
            [],
        )

    def _execute_generate_image(
        self,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
        assistant_message_id: str | None,
        assistant_version_id: str | None,
        source_message_id: str | None,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        if not self._image_available or self.image_provider is None or self.settings is None:
            return self._failure(
                "image_provider_unavailable",
                "No usable image generation Provider is configured for this workspace",
            )
        if not assistant_message_id or not assistant_version_id:
            return self._failure(
                "agent_image_context_missing",
                "Image generation requires the active assistant message context",
            )
        prompt_raw = arguments.get("prompt")
        if not isinstance(prompt_raw, str) or not prompt_raw.strip():
            raise AppError(422, "invalid_tool_arguments", "prompt is required")
        prompt = " ".join(prompt_raw.split())
        if len(prompt) > MAX_AGENT_IMAGE_PROMPT_CHARS:
            raise AppError(
                422,
                "invalid_tool_arguments",
                f"prompt must be at most {MAX_AGENT_IMAGE_PROMPT_CHARS} characters",
            )
        title_raw = arguments.get("title")
        title = (
            " ".join(title_raw.split())[:120]
            if isinstance(title_raw, str) and title_raw.strip()
            else prompt[:80]
        )
        provider_id_raw = arguments.get("provider_id")
        model_id_raw = arguments.get("model_id")
        provider_id = (
            provider_id_raw.strip()
            if isinstance(provider_id_raw, str) and provider_id_raw.strip()
            else None
        )
        model_id = (
            model_id_raw.strip()
            if isinstance(model_id_raw, str) and model_id_raw.strip()
            else None
        )
        source_file_ids_raw = arguments.get("source_file_ids")
        source_file_ids: list[str] = []
        if source_file_ids_raw is not None:
            if not isinstance(source_file_ids_raw, list) or not all(
                isinstance(item, str) and item.strip()
                for item in source_file_ids_raw
            ):
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "source_file_ids must be a list of file_id strings",
                )
            source_file_ids = list(
                dict.fromkeys(item.strip() for item in source_file_ids_raw)
            )
            if len(source_file_ids) > MAX_IMAGE_EDIT_SOURCES:
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    f"source_file_ids supports at most {MAX_IMAGE_EDIT_SOURCES} images",
                )
        source_inputs: list[ImageSourceInput] = []
        for source_file_id in source_file_ids:
            source_file = self._require_workspace_file(source_file_id)
            if not (
                is_image_attachment(source_file)
                or source_file.mime_type.casefold().startswith("image/")
            ):
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "source_file_ids must reference image files",
                    {"file_id": source_file.id, "mime_type": source_file.mime_type},
                )
            content = self._read_stored_file_bytes(
                source_file, limit_bytes=AGENT_IMAGE_INPUT_MAX_BYTES
            )
            source_mime, _, _ = self._validated_image_bytes(
                content, file_id=source_file.id
            )
            source_inputs.append(
                ImageSourceInput(
                    image_bytes=content,
                    mime_type=source_mime,
                    name=Path(source_file.original_name).name[:100] or "source.png",
                )
            )
        image_provider = self.image_provider
        if provider_id is not None or model_id is not None:
            if self.image_provider_resolver is None:
                return self._failure(
                    "image_model_selection_unavailable",
                    "This Agent runtime cannot resolve a selected image model",
                )
            image_provider = self.image_provider_resolver(provider_id, model_id)
        if (
            image_provider is None
            or not bool(getattr(image_provider, "available", True))
            or not bool(getattr(image_provider, "remote_capability", False))
        ):
            return self._failure(
                "image_model_unavailable",
                "The selected image generation model is not configured and enabled",
            )

        images = ImageGenerationService(
            self.extensions.db,
            self.workspace_id,
            self.actor_id,
            self.settings,
        )
        task = images.create(
            session_id=chat_session_id,
            message_id=assistant_message_id,
            message_version_id=assistant_version_id,
            source_message_id=source_message_id or assistant_message_id,
            provider_id=image_provider.provider_id,
            model_id=image_provider.model_id,
            prompt=prompt,
            commit=False,
        )
        images.mark_running(task)
        final_event = None
        usage: dict[str, Any] = {}
        try:
            for event in image_provider.stream_generate(
                ImageGenerationRequest(
                    prompt=prompt,
                    partial_images=0,
                    source_images=tuple(source_inputs),
                )
            ):
                if event.type == "completed":
                    final_event = event
                    usage = dict(event.usage or {})
                    break
            if final_event is None:
                raise AppError(
                    502,
                    "image_generation_incomplete",
                    "The image Provider stream ended without a final image",
                )
            file = images.store_image(
                task,
                final_event.image_bytes,
                final_event.mime_type,
                partial_index=None,
                completed=True,
                provider_trace={
                    "remote_request_id": getattr(
                        image_provider, "last_request_id", None
                    ),
                    "agent_tool": "generate_image",
                    "source_file_ids": source_file_ids,
                },
            )
        except AppError as exc:
            images.fail(task, exc.code, exc.message)
            return self._failure(exc.code, exc.message, data=exc.details or {})
        except Exception as exc:
            error_detail = " ".join(str(exc).split()).strip()[:300]
            images.fail(
                task,
                "image_generation_failed",
                error_detail or "Image generation failed",
            )
            return self._failure(
                "image_generation_failed",
                (
                    f"The authorized image generation Provider failed: {error_detail}"
                    if error_detail
                    else "The authorized image generation Provider failed"
                ),
            )

        part = {
            "type": "image",
            "status": "completed",
            "content": title,
            "data": {
                "generation_id": task.id,
                "provider_id": image_provider.provider_id,
                "model_id": image_provider.model_id,
                "file_id": file.id,
                "mime_type": file.mime_type,
                "title": title,
                "alt": prompt[:240],
                "prompt": prompt,
                "source_file_ids": source_file_ids,
                "progress_mode": "completed",
                "preview_revision": 1,
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
            },
        }
        self.audit.record(
            actor_id=self.actor_id,
            action="agent.generate_image",
            resource_type="image_generation_task",
            resource_id=task.id,
            details={
                "chat_session_id": chat_session_id,
                "message_id": assistant_message_id,
                "file_id": file.id,
                "provider_id": image_provider.provider_id,
                "model_id": image_provider.model_id,
            },
        )
        self.extensions.db.commit()
        return self._success(
            {
                "generated": True,
                "generation_id": task.id,
                "file_id": file.id,
                "mime_type": file.mime_type,
                "title": title,
                "prompt": prompt,
                "source_file_ids": source_file_ids,
            },
            {
                "image": True,
                "tool": "generate_image",
                "file_id": file.id,
                "generation_id": task.id,
                "artifact": part,
            },
            [],
        )

    def _success(
        self,
        data: dict[str, Any],
        meta: dict[str, Any],
        sources: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        content, truncated = self._bounded_content(data)
        return content, {"status": "completed", **meta, "result_truncated": truncated}, sources

    def _failure(
        self,
        code: str,
        message: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        details = data or {}
        content, truncated = self._bounded_content(
            {"error": code, "message": message, "details": details}
        )
        meta: dict[str, Any] = {
            "status": "failed",
            "reason": code,
            "error_code": code,
            "error_message": message,
            "details": details,
            "result_truncated": truncated,
        }
        # Surface structured sandbox authorization challenges to the Chat SSE
        # assembler so the client can open an explicit grant dialog.
        if code == "sandbox_auth_required":
            meta["sandbox_auth_required"] = {
                "action": details.get("action") or "delete_path",
                "paths": details.get("paths") or [],
                "chat_session_id": details.get("chat_session_id"),
                "affects_host_files": bool(details.get("affects_host_files", False)),
                "message_zh": details.get("message_zh")
                or "智能体请求删除会话工作区内的文件；不影响你电脑上的真实文件。",
            }
        return content, meta, []

    @staticmethod
    def _bounded_content(value: dict[str, Any]) -> tuple[str, bool]:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        raw = encoded.encode("utf-8")
        if len(raw) <= AGENT_TOOL_RESULT_MAX_BYTES:
            return encoded, False
        prefix = raw[: AGENT_TOOL_RESULT_MAX_BYTES - 256].decode("utf-8", errors="ignore")
        return (
            json.dumps(
                {
                    "truncated": True,
                    "message": "Tool result exceeded the Agent transcript limit",
                    "preview": prefix,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            True,
        )
