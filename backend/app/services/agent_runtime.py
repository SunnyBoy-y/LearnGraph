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
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select

# memory_tools is duck-typed (MemoryService) to avoid circular imports.

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.domain.models import (
    ChatSession,
    FileRecord,
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
from app.services.canvas_cards import MAX_MAGIC_CARD_PREVIEW_CHARS
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
# Hosted 文搜图/图搜图 is slower than plain web search and occasionally
# exceeds its HTTP budget on the first attempt; one extra try converts a
# transient upstream slowness into a success instead of a tool failure.
IMAGE_SEARCH_TIMEOUT_RETRIES = 1
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
# subapp_observe (T1.4): read-only observation of SubAppInteractionEvent rows.
# Hard caps keep the tool pure-observation: bounded row count, bounded
# per-type map, and a size/field-limited payload digest (payloads are
# untrusted data and never returned verbatim).
SUBAPP_OBSERVE_MAX_LIMIT = 50
SUBAPP_OBSERVE_MAX_EVENT_TYPES = 50
SUBAPP_OBSERVE_SESSION_ID_MAX = 36
SUBAPP_OBSERVE_EVENT_TYPE_MAX = 120
SUBAPP_OBSERVE_PAYLOAD_SUMMARY_KEYS = 20
SUBAPP_OBSERVE_PAYLOAD_SCALAR_CHARS = 80
SUBAPP_OBSERVE_PAYLOAD_PREVIEW_CHARS = 200


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
        image_search_provider: SearchProviderPort | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.search_provider = search_provider
        self.image_search_provider = image_search_provider
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
        capability_families: set[str] | None = None,
        activated_capabilities: set[str] | None = None,
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
        # Read-only observation of interactive sub-application events (T1.4).
        # The write-side subapp_patch_state (T2.5) is registered through its
        # own method right below so the observe list stays pure observation.
        # Both tools share the Agent-mode gate: definitions() returns [] when
        # agent mode is off, so neither subapp tool is exposed otherwise.
        definitions.extend(self._subapp_observe_tool_definitions())
        definitions.extend(self._subapp_patch_state_tool_definitions())
        # Durable session files (attachments + generated images) are always
        # addressable in Agent mode; without these tools the model cannot see
        # or reference an image from an earlier turn.
        definitions.extend(self._session_file_tool_definitions())
        # Trusted host-side acquisition bridges public search results and the
        # durable session workspace without granting network access to sandbox
        # commands. Every remote host still passes generic egress approval.
        definitions.extend(self._external_acquisition_tool_definitions())
        # Progressive disclosure is gated by settings so a single feature flag
        # can restore the previous eager behavior without code changes. When the
        # flag is off, family/activation args are dropped and every eligible
        # extension schema is exposed as before. Discovery tools remain available
        # in Agent mode regardless; they are additive and never hide existing
        # capabilities.
        progressive_enabled = True
        if self.settings is not None:
            progressive_enabled = bool(
                getattr(
                    self.settings,
                    "agent_progressive_tool_disclosure_enabled",
                    True,
                )
            )
        effective_families = capability_families if progressive_enabled else None
        effective_activated = activated_capabilities if progressive_enabled else None
        definitions.extend(
            self.extensions.agent_tool_definitions(
                capability_families=effective_families,
                activated_capabilities=effective_activated,
            )
        )
        # search_web / parallel_web_research follow the explicit "联网" search
        # toggle — they need an authorized SearchProvider and the user's
        # web_search flag. search_images (文搜图/图搜图) uses its own dedicated
        # provider lane (qwen_image_search) but keeps the same toggle.
        # fetch_web_page is decoupled: a Firecrawl-style FetchProvider (or a
        # Qwen companion with .fetch) makes it available even when "联网" is
        # off, since fetching a single authorized URL is not a blanket
        # web-search action and the URL is always SSRF/allow-list gated.
        if web_search_enabled and self._search_available:
            definitions.extend(self._web_tool_definitions())
        if web_search_enabled and self._image_search_available:
            definitions.append(self._image_search_tool_definition())
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
                    "name": "lg_graph_create",
                    "description": (
                        "Create a NEW reviewable target-graph proposal for the "
                        "confirmed Goal bound to this session (first generation "
                        "only). Use this only when the Goal has no graph yet; if a "
                        "candidate or published graph already exists, use "
                        "lg_graph_propose_change to update it instead. This tool "
                        "writes a reviewable proposal and never mutates the "
                        "formal graph by itself: the user confirms the review "
                        "card, and that confirmation is treated as the review "
                        "passing — the graph is created, published and the Goal "
                        "approved at that moment, and the Session binds it. "
                        "Hierarchy rules (enforced by "
                        "host): contains edges define layers (parent -> child); "
                        "layer 0 is the single root. First generation spans "
                        "layer 0 (root), layer 1 (trunk) and up to two expansion "
                        "layers per trunk node (layer-2 children, layer-3 "
                        "grandchildren; max depth 3): no orphans and no skipped "
                        "layers — every non-root node attaches directly under a "
                        "contains parent exactly one layer above. Deeper layers "
                        "are produced by later lg_graph_propose_change splits. "
                        "Self-validate before calling: exactly one root; unique "
                        "non-duplicate labels; every non-root node has exactly "
                        "one contains parent; no prerequisite/contains cycles; "
                        "no self-loop edges. "
                        "Invalid proposals are rejected and no review card is "
                        "shown."
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
            },
            {
                "type": "function",
                "function": {
                    "name": "lg_graph_propose_change",
                    "description": (
                        "Update an EXISTING candidate or published graph that "
                        "belongs to the Goal of this session: propose an "
                        "incremental reviewed change set (split a node into "
                        "next-layer children, fix a label or description). Use "
                        "lg_graph_create first when the Goal has no graph yet. "
                        "This tool never publishes or mutates the formal graph; "
                        "the user confirms the review card before the change is "
                        "applied. graph_id defaults to the graph bound to this "
                        "session, falling back to the session Goal's latest "
                        "graph. Read the current graph first with lg_graph_read; "
                        "keep exactly one root; never re-add existing concepts "
                        "(update instead); every non-root node needs exactly one "
                        "contains parent; avoid prerequisite/contains cycles and "
                        "duplicate labels. A later split of a node must only add "
                        "the next layer under that already-existing parent "
                        "(contains parent_depth+1), not multi-layer chains or "
                        "free-floating nodes. Invalid proposals are rejected and "
                        "no review card is shown."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "graph_id": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 36,
                                "description": "Optional explicit graph to update; defaults to the graph bound to this session or the Goal's latest graph.",
                            },
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
            },
            {
                "type": "function",
                "function": {
                    "name": "lg_goal_read",
                    "description": (
                        "Read the Goal currently bound to this session (and the Graph "
                        "bound to it, if any). Use before creating or proposing anything "
                        "so you never duplicate an existing Goal or propose a graph for "
                        "the wrong target. This tool is read-only."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "goal_id": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 36,
                                "description": "Optional explicit Goal id; defaults to the session-bound Goal.",
                            }
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "lg_goal_create",
                    "description": (
                        "Create a Goal draft from the current conversation and bind it to "
                        "this session, so later Goal/Graph tools can act on it. Only call "
                        "this after the user's intent is clear enough to name a subject "
                        "title; ask 1-3 targeted questions first when key facts are "
                        "missing. The Goal stays a reviewable draft (status "
                        "\"clarifying\") unless auto_confirm=true and the user has "
                        "explicitly agreed in this conversation. Never fabricate the "
                        "user's deadline, availability, or desired outcome."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 240,
                                "description": "Clean subject/topic phrase, e.g. \"数据库原理与应用\".",
                            },
                            "raw_prompt": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 10_000,
                                "description": "Optional original user request; defaults to the source user message.",
                            },
                            "intent": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 240,
                                "description": "Main learning scenario, e.g. \"考试\", \"项目\", \"面试\".",
                            },
                            "time_limit": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 120,
                                "description": "Narrative time budget the user stated, e.g. \"每天 2 小时\".",
                            },
                            "desired_outcome": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 4_000,
                                "description": "How the user wants to verify learning.",
                            },
                            "constraints": {
                                "type": "object",
                                "description": "Structured constraints (file ids, clarification answers, etc.).",
                            },
                            "assumptions": {
                                "type": "array",
                                "items": {"type": "object"},
                                "description": "Transparent assumptions made; each entry needs source/field/assumption keys.",
                            },
                            "auto_confirm": {
                                "type": "boolean",
                                "description": "Set the Goal to confirmed only when the user explicitly agreed in this conversation.",
                            },
                        },
                        "required": ["title"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "lg_goal_confirm",
                    "description": (
                        "Confirm the Goal bound to this session (draft -> confirmed). "
                        "Call only after the user explicitly agreed to the Goal in this "
                        "conversation. Optionally update Goal fields in the same call. "
                        "Once confirmed, lg_graph_create becomes available."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 240,
                            },
                            "intent": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 240,
                            },
                            "time_limit": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 120,
                            },
                            "desired_outcome": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 4_000,
                            },
                            "target_weight": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 100,
                            },
                            "availability": {"type": "object"},
                            "preferences": {"type": "object"},
                            "constraints": {"type": "object"},
                            "assumptions": {
                                "type": "array",
                                "items": {"type": "object"},
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "lg_goal_ask",
                    "description": (
                        "Ask the user a question with an interactive card, never "
                        "plain text. This is the ONLY way to ask the user while "
                        "working on a Goal: emit a single_choice / multiple_choice "
                        "card for selection questions, or fill_blank / "
                        "short_answer_table for open answers. Keep it to one "
                        "question per card and prefer 3-5 options; questions must "
                        "change the Goal boundary, depth, or acceptance criteria. "
                        "After emitting, briefly tell the user to answer the card "
                        "and stop; the answer arrives as the next user message, "
                        "then continue (create/confirm the Goal or propose the "
                        "graph). Never fabricate the user's answer."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 500,
                                "description": "The question shown as the card title.",
                            },
                            "input_type": {
                                "type": "string",
                                "enum": [
                                    "single_choice",
                                    "multiple_choice",
                                    "fill_blank",
                                    "short_answer_table",
                                    "date",
                                ],
                                "description": "Defaults to single_choice. Use date for time/date questions — the card renders a calendar with the user's learning schedule.",
                            },
                            "options": {
                                "type": "array",
                                "maxItems": 8,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {
                                            "type": "string",
                                            "minLength": 1,
                                            "maxLength": 80,
                                        },
                                        "label": {
                                            "type": "string",
                                            "minLength": 1,
                                            "maxLength": 500,
                                        },
                                        "description": {
                                            "type": "string",
                                            "maxLength": 2_000,
                                        },
                                    },
                                    "required": ["id", "label"],
                                    "additionalProperties": False,
                                },
                                "description": "Required for single/multiple choice.",
                            },
                            "allow_custom": {
                                "type": "boolean",
                                "description": "Let the user type their own answer (default true).",
                            },
                            "allow_skip": {
                                "type": "boolean",
                                "description": "Let the user skip; a transparent assumption is recorded (default true).",
                            },
                            "component_id": {
                                "type": "string",
                                "description": "Optional stable card id so a follow-up can reference it.",
                            },
                        },
                        "required": ["question"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "lg_goal_ask_batch",
                    "description": (
                        "Ask 2-5 related Goal-clarification questions in ONE "
                        "aggregated card (preferred over lg_goal_ask for any "
                        "batch of questions). Each sub-question is a small "
                        "control: single_choice / multiple_choice for selection, "
                        "fill_blank / short_answer_table for open answers. The "
                        "user submits all answers together; they arrive as the "
                        "next user message. Only ask questions that change the "
                        "Goal boundary, depth, order, or acceptance criteria — "
                        "never a fixed questionnaire, and never plain-text "
                        "questions in your reply. One card per batch; keep 3-5 "
                        "options per choice question. After emitting, tell the "
                        "user to answer the card and stop."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 500,
                                "description": "Card title, e.g. 目标澄清（3 个问题，一次提交）.",
                            },
                            "questions": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 8,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "key": {
                                            "type": "string",
                                            "minLength": 1,
                                            "maxLength": 80,
                                        },
                                        "prompt": {
                                            "type": "string",
                                            "minLength": 1,
                                            "maxLength": 500,
                                        },
                                        "input_type": {
                                            "type": "string",
                                            "enum": [
                                                "single_choice",
                                                "multiple_choice",
                                                "fill_blank",
                                                "short_answer_table",
                                                "date",
                                            ],
                                        },
                                        "options": {
                                            "type": "array",
                                            "maxItems": 8,
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "id": {
                                                        "type": "string",
                                                        "minLength": 1,
                                                        "maxLength": 80,
                                                    },
                                                    "label": {
                                                        "type": "string",
                                                        "minLength": 1,
                                                        "maxLength": 500,
                                                    },
                                                    "description": {
                                                        "type": "string",
                                                        "maxLength": 2_000,
                                                    },
                                                },
                                                "required": ["id", "label"],
                                                "additionalProperties": False,
                                            },
                                            "description": "Required for choice questions.",
                                        },
                                        "allow_custom": {
                                            "type": "boolean",
                                            "description": "Let the user type their own answer (default true).",
                                        },
                                        "allow_skip": {
                                            "type": "boolean",
                                            "description": "Let the user skip this sub-question (default true).",
                                        },
                                        "required": {
                                            "type": "boolean",
                                            "description": "Block submit until answered (default false).",
                                        },
                                    },
                                    "required": ["key", "prompt"],
                                    "additionalProperties": False,
                                },
                            },
                            "component_id": {
                                "type": "string",
                                "description": "Optional stable card id for follow-ups.",
                            },
                        },
                        "required": ["title", "questions"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "lg_goal_edit_draft",
                    "description": (
                        "Open the Goal draft in a two-way editable card (sub-page "
                        "style) so the user can directly adjust title / intent / "
                        "time limit / desired outcome before confirmation. Use it "
                        "when the user wants to tweak the Goal instead of answering "
                        "question by question, or after a first draft exists. The "
                        "user edits the fields and submits; the edited values "
                        "arrive as the next user message, then confirm the Goal "
                        "with lg_goal_confirm using those values. Requires a "
                        "session-bound Goal (create it first with lg_goal_create)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "goal_id": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 36,
                                "description": "Optional explicit Goal id; defaults to the session-bound Goal.",
                            },
                            "focus": {
                                "type": "string",
                                "enum": ["title", "time", "outcome", "all"],
                                "description": "Which fields to highlight; defaults to all.",
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
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
    def _image_search_available(self) -> bool:
        provider = self.image_search_provider
        return (
            provider is not None
            and bool(getattr(provider, "available", True))
            and callable(getattr(provider, "image_search", None))
        )

    def _image_search_tool_definition(self) -> dict[str, Any]:
        # Lightweight REST 文搜图 lanes (Tavily/Openverse/Pexels/Pixabay) are
        # text-only: hide the image_url parameter so the Agent never attempts a
        # reverse search that the lane cannot serve.
        supports_reverse = bool(
            getattr(self.image_search_provider, "supports_reverse_image", True)
        )
        if supports_reverse:
            mode_description = (
                "Pass only a text query for text-to-image search (文搜图), or add "
                "a public image URL for reverse image search (图搜图). "
            )
        else:
            mode_description = (
                "The active provider lane only supports 文搜图 (text-to-image "
                "search): never pass an image_url — reverse image search is not "
                "available on this lane. "
            )
        properties: dict[str, Any] = {
            "query": {
                "type": "string",
                "minLength": 1,
                "maxLength": 500,
            },
        }
        if supports_reverse:
            properties["image_url"] = {
                "type": "string",
                "description": (
                    "Optional public image URL for reverse image search "
                    "(图搜图); omit for text-to-image search (文搜图). "
                    "Must be a public http(s) URL; the server validates it "
                    "against the authorized domains."
                ),
            }
        return {
            "type": "function",
            "function": {
                "name": "search_images",
                "description": (
                    "Search the public web for images through the configured "
                    "文搜图/图搜图 provider lane. "
                    + mode_description
                    + "Keep the query short and single-language (about 60 "
                    "characters or fewer): long or mixed-language queries make "
                    "the upstream slower and can time out. After the results "
                    "come back, if the user wants the actual image files (to "
                    "view, analyze, edit, or use in materials), call "
                    "download_external_image with the selected image URLs — "
                    "pass several URLs in the urls array to download them in "
                    "parallel."
                ),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        }

    @property
    def _fetch_available(self) -> bool:
        provider = self.fetch_provider
        return provider is not None and not bool(getattr(provider, "reason", "")) and bool(
            getattr(provider, "available", True)
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
                        "weather_card requires location, condition, temperature_c. "
                        "For graded practice, include correct_option_ids (or option.is_correct) "
                        "and optional explanation so the UI can grade after the learner confirms. "
                        "When emitting multiple practice questions in one turn, emit them "
                        "as consecutive trusted components so the client stacks them into "
                        "one paged exercise control."
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
                                    "goal_draft_editor",
                                    "question_batch",
                                ],
                            },
                            "props": {
                                "type": "object",
                                "description": (
                                    "Component data. Examples: "
                                    'single_choice → {title, options:[{id,label}], '
                                    'correct_option_ids:["a"], explanation:"..."}; '
                                    'fill_blank → {title, prompt, blank_ids:["answer"], '
                                    'correct_answers:["..."]}; '
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
                        "Publish a channel-B magic_card Message Part from a complete, "
                        "self-contained preview_html document or fragment. Inline HTML, "
                        "CSS, and JavaScript run inside an opaque-origin sandboxed iframe, "
                        "not the host DOM. Include all executable source in this call; do "
                        "not depend on sandbox files or a React build. Do not use CDN or "
                        "remote scripts, images, fonts, module imports, fetch, WebSocket, "
                        "or other network resources. Do not use this for ordinary forms."
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
                            "preview_html": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": MAX_MAGIC_CARD_PREVIEW_CHARS,
                                "description": (
                                    "Complete self-contained inline HTML/CSS/JavaScript; "
                                    "all network access is blocked by the host sandbox."
                                ),
                            },
                            "goal_id": {"type": "string"},
                            "node_id": {"type": "string"},
                        },
                        "required": ["title", "preview_html"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "artifact_publish_card",
                    "description": (
                        "Publish a card you emitted earlier (canvas_emit_magic_card "
                        "or canvas_emit_trusted_component) as an immutable version "
                        "in the workspace artifacts page. Publishing freezes the "
                        "card's current preview as a version and marks the card "
                        "published; later draft edits never change a published "
                        "version, and repeated publishes create v2, v3, ... "
                        "versions. Call this ONLY when the card is the final "
                        "deliverable for the user's request — not during draft "
                        "iteration. Use the card_id from the emit tool's result."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "card_id": {
                                "type": "string",
                                "description": (
                                    "The card_id returned by canvas_emit_magic_card "
                                    "or canvas_emit_trusted_component for the card to publish."
                                ),
                            },
                            "release_notes": {
                                "type": "string",
                                "description": "Optional short release notes for this version.",
                            },
                        },
                        "required": ["card_id"],
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

    @staticmethod
    def _subapp_observe_tool_definitions() -> list[dict[str, Any]]:
        """Read-only observation of interactive sub-application events (T1.4).

        Always available in Agent mode (independent of provider toggles). Pure
        observe: no data is written, no state is patched.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "subapp_observe",
                    "description": (
                        "Read-only: observe interaction events emitted by "
                        "interactive sub-applications (forms, practice cards, "
                        "planners) in this workspace. Returns the total matching "
                        "event count, a count breakdown per event_type, and a "
                        "bounded digest of the most recent events (ids, event "
                        "types, created_at timestamps, actor, payload byte size "
                        "and a size/field-limited payload summary). Payloads are "
                        "untrusted data: do not infer semantic correctness from "
                        "them. This tool never writes or mutates any data. "
                        "Optional filters: session_id (sub-application "
                        "interaction session), event_type, time_range, limit."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "session_id": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": SUBAPP_OBSERVE_SESSION_ID_MAX,
                                "description": (
                                    "Optional sub-application interaction session "
                                    "id. Omit to observe events across this "
                                    "workspace."
                                ),
                            },
                            "event_type": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": SUBAPP_OBSERVE_EVENT_TYPE_MAX,
                                "description": (
                                    "Optional event-type filter (e.g. "
                                    "exercise.completed). Omit to observe all "
                                    "event types."
                                ),
                            },
                            "time_range": {
                                "type": "object",
                                "properties": {
                                    "from": {
                                        "type": "string",
                                        "format": "date-time",
                                    },
                                    "to": {
                                        "type": "string",
                                        "format": "date-time",
                                    },
                                },
                                "additionalProperties": False,
                                "description": (
                                    "Optional ISO-8601 time window on the events' "
                                    "created_at timestamps."
                                ),
                            },
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": SUBAPP_OBSERVE_MAX_LIMIT,
                                "description": (
                                    "Optional number of most recent events to "
                                    "return in the digest (default 20)."
                                ),
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            }
        ]

    @staticmethod
    def _subapp_patch_state_tool_definitions() -> list[dict[str, Any]]:
        """Write-side state patch for interactive sub-application sessions (T2.5).

        Always available in Agent mode (independent of provider toggles). This
        is the agent's ONLY path to change a sub-application's state: the write
        goes through the server (contract validation + optimistic versioning +
        renderer push). The agent never holds an iframe reference and must not
        try to postMessage or mutate the renderer directly.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "subapp_patch_state",
                    "description": (
                        "Write a new state snapshot to an interactive "
                        "sub-application session in this workspace. The write "
                        "MUST go through the server: the state is validated "
                        "against the session's contract schema, versioned with "
                        "an optimistic lock, and pushed to the renderer by the "
                        "host. The agent cannot operate the iframe directly — "
                        "no postMessage, no direct state mutation. "
                        "expected_version is an optimistic-lock integer that "
                        "must equal the session's current state_version, which "
                        "you must read first with subapp_observe (or the "
                        "session's current state). If the version has moved, "
                        "the write fails with a version conflict and the "
                        "current version is returned so you can retry. Returns "
                        "the new state version and the canonical-JSON sha256 "
                        "of the state that was persisted."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "session_id": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": SUBAPP_OBSERVE_SESSION_ID_MAX,
                                "description": (
                                    "Sub-application interaction session id to "
                                    "patch. Must belong to this workspace."
                                ),
                            },
                            "state": {
                                "type": "object",
                                "description": (
                                    "Full new state snapshot (not a diff). "
                                    "Must satisfy the session's contract "
                                    "state_schema or the write is rejected."
                                ),
                            },
                            "expected_version": {
                                "type": "integer",
                                "minimum": 0,
                                "description": (
                                    "Optimistic lock: the session's current "
                                    "state_version read before writing. The "
                                    "write is rejected with a version conflict "
                                    "if the session has moved on."
                                ),
                            },
                        },
                        "required": ["session_id", "state", "expected_version"],
                        "additionalProperties": False,
                    },
                },
            }
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
                                    "search | image_search | fetch | deep_research | memory | "
                                    "transcription"
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
                        "(reasoning efforts, search route, image input, etc.). "
                        "Works on every discovery-capable Provider role: chat "
                        "model, image generation, vision, transcription, "
                        "embedding, and deep research."
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
            {
                "type": "function",
                "function": {
                    "name": "probe_provider",
                    "description": (
                        "Run a non-destructive connectivity check for one Provider. "
                        "For chat / image / vision Providers this lists models via "
                        "the official GET /models endpoint (zero-cost; it never "
                        "generates content). For search / fetch / deep-research / "
                        "memory Providers it runs their role-specific health probe. "
                        "It never enables a Provider, changes its status, or "
                        "disables same-role peers."
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
                    "name": "validate_provider_default_models",
                    "description": (
                        "Audit the default model of one or every discovery-capable "
                        "Provider: a default is consistent when it names a model "
                        "present in the latest discovery snapshot and that model is "
                        "enabled. Pass repair=true (requires workspace.manage) to "
                        "move broken defaults to the first enabled model. "
                        "Read-only when repair is false."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "provider_id": {"type": "string"},
                            "repair": {
                                "type": "boolean",
                                "default": False,
                                "description": "Fix broken defaults (workspace.manage required).",
                            },
                        },
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
                        "name": "refresh_provider_models",
                        "description": (
                            "Force a fresh model-discovery snapshot for one Provider "
                            "and prune stale model entries: models no longer reported "
                            "are dropped, new models are enabled by default, and the "
                            "role-specific default model is repaired if it pointed at "
                            "a missing or disabled model. Requires workspace.manage."
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
                        "name": "verify_provider_declaration",
                        "description": (
                            "Confirm or re-flag a Provider's declared capabilities: "
                            "unverified_user_input (default), user_confirmed (the "
                            "workspace owner reviewed the declared role and "
                            "capabilities), or verified_by_probe (a live probe "
                            "matched the declaration). Requires workspace.manage."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "provider_id": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": [
                                        "unverified_user_input",
                                        "user_confirmed",
                                        "verified_by_probe",
                                    ],
                                },
                            },
                            "required": ["provider_id", "status"],
                            "additionalProperties": False,
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "configure_dashscope_balance",
                        "description": (
                            "Associate an Aliyun AccessKey secret label with a "
                            "DashScope Provider so get_provider_balance can query "
                            "the account balance through the BSS OpenAPI. The label "
                            "must resolve (purpose aliyun_access_key) to JSON with "
                            "access_key_id and access_key_secret. Only the label is "
                            "stored; the plaintext is never returned. Requires "
                            "workspace.manage."
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
                                "default_realtime_transcription_model_id": {
                                    "type": "string"
                                },
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
                            "(thinking, search, image input). Requires workspace.manage. "
                            "Works on every discovery-capable Provider role; on "
                            "non-chat roles the snapshot is stored as per-model "
                            "metadata and does not affect chat reasoning."
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
            self._function_definition(
                "list_budget_policies",
                "List workspace token-cost budget policies with their scope, period, limits, and enabled state.",
                {},
            ),
            self._function_definition(
                "get_budget_status",
                "Read the current spending vs. limit status of every workspace budget policy.",
                {},
            ),
            self._function_definition(
                "list_budget_alerts",
                "List workspace budget alerts (soft/hard threshold hits) and whether each is acknowledged.",
                {},
            ),
            self._function_definition(
                "get_exchange_rate",
                "Read the currently effective USD/CNY exchange rate used for cost conversion.",
                {},
            ),
            self._function_definition(
                "list_manual_prices",
                "List workspace-defined manual model prices that override catalog defaults.",
                {},
            ),
            self._function_definition(
                "get_usage_summary",
                "Read persisted token and cost usage aggregates (optional provider/model/feature filter).",
                {
                    "provider_id": {"type": "string", "maxLength": 80},
                    "model_id": {"type": "string", "maxLength": 160},
                    "feature": {"type": "string", "maxLength": 80},
                },
            ),
            self._function_definition(
                "list_usage_events",
                "List persisted usage events (optional provider/model/feature filter). Returns the most recent events first.",
                {
                    "provider_id": {"type": "string", "maxLength": 80},
                    "model_id": {"type": "string", "maxLength": 160},
                    "feature": {"type": "string", "maxLength": 80},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            ),
            self._function_definition(
                "get_memory_policy",
                "Read the workspace (and optional session) shared-memory policy: enabled, recall, and learning switches.",
                {
                    "session_id": {"type": "string", "minLength": 1, "maxLength": 36},
                },
            ),
            self._function_definition(
                "list_plugins",
                "List workspace plugins (including trusted-component plugins) with their enabled state and status.",
                {},
            ),
            self._function_definition(
                "get_local_probe_policy",
                "Read the local Skill probe policy: whether local scanning is enabled and which roots are allowed.",
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
                self._function_definition(
                    "set_models_enabled",
                    "Enable or disable many models of one Provider at once. Works on every discovery-capable role (chat, image generation, vision, transcription, embedding, deep research). Requires workspace.manage.",
                    {
                        "provider_id": {"type": "string"},
                        "states": {
                            "type": "object",
                            "description": "Mapping of model_id to a boolean enable flag.",
                        },
                    },
                    required=["provider_id", "states"],
                ),
                self._function_definition(
                    "test_alert_email",
                    "Send a test email through the configured SMTP alert configuration. Requires workspace.manage.",
                    {},
                ),
                self._function_definition(
                    "delete_budget_policy",
                    "Permanently delete a workspace budget policy by id. Requires workspace.manage.",
                    {"policy_id": {"type": "string", "minLength": 1, "maxLength": 36}},
                    required=["policy_id"],
                ),
                self._function_definition(
                    "acknowledge_budget_alert",
                    "Acknowledge a budget alert by id so it no longer surfaces as unhandled. Requires workspace.manage.",
                    {"alert_id": {"type": "string", "minLength": 1, "maxLength": 36}},
                    required=["alert_id"],
                ),
                self._function_definition(
                    "set_exchange_rate",
                    "Set the workspace USD/CNY exchange rate used for cost conversion. Requires workspace.manage.",
                    {"rate": {"type": "number", "minimum": 0.01}},
                    required=["rate"],
                ),
                self._function_definition(
                    "refresh_exchange_rate",
                    "Refresh the USD/CNY exchange rate from the network. Requires workspace.manage.",
                    {},
                ),
                self._function_definition(
                    "upsert_manual_price",
                    "Create or replace a workspace manual model price overriding the catalog. Requires workspace.manage.",
                    {
                        "model_id": {"type": "string", "minLength": 1, "maxLength": 160},
                        "provider_id": {"type": "string", "minLength": 1, "maxLength": 80},
                        "currency": {"type": "string", "enum": ["USD", "CNY"]},
                        "input_per_million": {"type": "number", "minimum": 0},
                        "cached_input_per_million": {
                            "type": ["number", "null"],
                            "minimum": 0,
                        },
                        "output_per_million": {"type": "number", "minimum": 0},
                        "fixed_per_call": {"type": "number", "minimum": 0},
                    },
                    required=["model_id", "input_per_million", "output_per_million"],
                ),
                self._function_definition(
                    "remove_manual_price",
                    "Remove a workspace manual model price by model_id. Requires workspace.manage.",
                    {"model_id": {"type": "string", "minLength": 1, "maxLength": 160}},
                    required=["model_id"],
                ),
                self._function_definition(
                    "refresh_models_dev_snapshot",
                    "Re-fetch the models.dev price snapshot from the network. Requires workspace.manage.",
                    {},
                ),
                self._function_definition(
                    "update_memory_policy",
                    "Change the workspace or a session shared-memory policy: enabled, recall, and learning switches. Requires workspace.manage.",
                    {
                        "workspace_enabled": {"type": "boolean"},
                        "workspace_recall_enabled": {"type": "boolean"},
                        "workspace_learning_enabled": {"type": "boolean"},
                        "session_id": {"type": "string", "minLength": 1, "maxLength": 36},
                        "session_enabled": {"type": "boolean"},
                        "session_recall_enabled": {"type": "boolean"},
                        "session_learning_enabled": {"type": "boolean"},
                    },
                ),
                self._function_definition(
                    "reindex_memory_embeddings",
                    "Re-embed every active memory under the configured embedding model (best-effort). Requires workspace.manage.",
                    {},
                ),
                self._function_definition(
                    "toggle_plugin",
                    "Enable or disable a workspace plugin by id. Requires workspace.manage.",
                    {
                        "plugin_id": {"type": "string"},
                        "enabled": {"type": "boolean"},
                    },
                    required=["plugin_id", "enabled"],
                ),
                self._function_definition(
                    "update_local_probe_policy",
                    "Enable or disable local Skill probing and set the allowed scan roots. Requires workspace.manage.",
                    {
                        "enabled": {"type": "boolean"},
                        "allowed_roots": {
                            "type": "array",
                            "maxItems": 20,
                            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
                        },
                    },
                    required=["enabled"],
                ),
                self._function_definition(
                    "refresh_mcp_server",
                    "Re-discover the capability snapshot of a registered MCP server. Requires workspace.manage.",
                    {
                        "server_id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 36,
                        }
                    },
                    required=["server_id"],
                ),
                self._function_definition(
                    "update_skill_manifest",
                    "Update a declarative Skill's manifest (instructions, required tools, permissions, steps). Changing the manifest invalidates the skill's authorization. Requires workspace.manage.",
                    {
                        "skill_id": {"type": "string", "minLength": 1, "maxLength": 36},
                        "name": {"type": "string", "minLength": 1, "maxLength": 160},
                        "source": {"type": "string", "minLength": 1, "maxLength": 255},
                        "version": {"type": "string", "minLength": 1, "maxLength": 80},
                        "manifest": {"type": "object"},
                    },
                    required=["skill_id", "manifest"],
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
                "Create a readable, durable pie, line, or bar chart with per-series colors and structured source data. Charts with real-world data MUST pass authoritative sources (title + URL from search_web/fetch_web_page results); never invent numbers.",
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
                    "sources": {
                        "type": "array",
                        "maxItems": 8,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["title", "url"],
                            "properties": {
                                "title": {"type": "string", "minLength": 1, "maxLength": 240},
                                "url": {"type": "string", "format": "uri", "minLength": 1, "maxLength": 1000},
                                "note": {"type": "string", "maxLength": 500},
                            },
                        },
                        "description": (
                            "REQUIRED for any chart with real-world data: the "
                            "authoritative sources (title + URL) behind the "
                            "numbers, obtained from search_web / fetch_web_page. "
                            "Never fabricate figures, ratios, or trends; without "
                            "a citable source, do not draw a data chart."
                        ),
                    },
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
                            "size": {
                                "type": "string",
                                "enum": [
                                    "auto",
                                    "2048x2048",
                                    "2048x1152",
                                    "1152x2048",
                                    "1536x1152",
                                    "1152x1536",
                                ],
                                "description": (
                                    "Output dimensions. Use 2048x1152 for 16:9, "
                                    "1152x2048 for 9:16, 1536x1152 for 4:3, "
                                    "1152x1536 for 3:4, or auto when unspecified."
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

    @staticmethod
    def _external_acquisition_tool_definitions() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "download_external_image",
                    "description": (
                        "Download one or more public images through LearnGraph's trusted "
                        "host-side acquisition gateway. The sandbox has NO internet: this "
                        "is the only way to turn a remote image link into a real image file "
                        "the sandbox can read. Call it AFTER search_images (文搜图/图搜图) "
                        "returns useful image URLs, or when the user pastes image links they "
                        "want saved. Pass a single url with destination_path, or several URLs "
                        "in the urls array (2-8) with destination_dir to download them in "
                        "PARALLEL. Images are verified, sanitized (metadata stripped), "
                        "hashed, and injected into the offline session workspace with an "
                        "immutable provenance receipt. IMPORTANT: after a successful download, "
                        "embed the picture directly inside your final answer at the exact "
                        "position you want with markdown-image syntax "
                        "![简短描述](sandbox:目的地路径), using the destination_path for a single "
                        "download, or the per-file path values returned in the tool result for "
                        "a batch, e.g. ![架构示意图](sandbox:inputs/images/architecture.png). "
                        "Only files written by this tool can be embedded inline this way."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "format": "uri",
                                "description": "Single public HTTPS image URL (use EITHER url or urls, not both).",
                            },
                            "urls": {
                                "type": "array",
                                "items": {"type": "string", "format": "uri"},
                                "minItems": 2,
                                "maxItems": 8,
                                "description": "Multiple image URLs to download in parallel (use EITHER url or urls, not both).",
                            },
                            "destination_path": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 1000,
                                "description": (
                                    "Sandbox-relative target file for a SINGLE image, normally "
                                    "inputs/images/<name>. Your final answer can embed this "
                                    "image inline by writing ![简短描述](sandbox:<destination_path>) "
                                    "at the desired position."
                                ),
                            },
                            "destination_dir": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 1000,
                                "description": "Sandbox-relative directory for MULTIPLE images; defaults to inputs/images.",
                            },
                            "expected_sha256": {
                                "type": "string",
                                "pattern": "^[0-9a-fA-F]{64}$",
                                "description": "Optional expected SHA-256 for a SINGLE image; omit for url batches.",
                            },
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "download_github_source",
                    "description": (
                        "Acquire a GitHub file, directory, or bounded repository snapshot "
                        "so the sandbox can study real source code WITHOUT any network: the "
                        "sandbox cannot run git clone. Call it when the user wants to learn "
                        "from, read, or download a GitHub project, or shares a GitHub URL. "
                        "Resolves the requested ref to an immutable commit, downloads regular "
                        "files through approved GitHub hosts (symlinks/submodules/LFS are "
                        "refused), creates a per-file hash manifest, and injects everything "
                        "into the offline workspace."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "owner": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 100,
                                "description": "GitHub owner/org from the URL, e.g. https://github.com/OWNER/repo.",
                            },
                            "repo": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 100,
                                "description": "GitHub repository name from the URL.",
                            },
                            "ref": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 200,
                                "description": "Branch/tag/commit to pin, e.g. main or v1.0.0; omit for HEAD.",
                            },
                            "path": {
                                "type": "string",
                                "maxLength": 1000,
                                "description": "Optional subdirectory or single file path inside the repo; omit for the whole repository.",
                            },
                            "destination_root": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 1000,
                                "description": "Sandbox-relative destination, normally inputs/github/<repo>.",
                            },
                        },
                        "required": ["owner", "repo", "destination_root"],
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
        return definitions

    def _web_fetch_policy(self) -> dict[str, Any]:
        from app.domain.models import UserWebFetchPolicy, WorkspaceSetting

        setting = self.extensions.db.scalar(
            select(WorkspaceSetting).where(
                WorkspaceSetting.workspace_id == self.workspace_id,
                WorkspaceSetting.key == "web_fetch.policy",
            )
        )
        value = setting.value if setting is not None and isinstance(setting.value, dict) else {}
        workspace_domains = value.get("allowed_domains")
        user_policy = self.extensions.db.scalar(
            select(UserWebFetchPolicy).where(
                UserWebFetchPolicy.workspace_id == self.workspace_id,
                UserWebFetchPolicy.user_id == self.actor_id,
            )
        )
        workspace_domain_list = (
            [item for item in workspace_domains if isinstance(item, str)]
            if isinstance(workspace_domains, list)
            else []
        )
        user_domains = (
            user_policy.allowed_domains if user_policy is not None else []
        )
        from app.providers.factory import access_allow_all, access_allowlist_domains

        unified_domains = access_allowlist_domains(
            self.extensions.db, self.workspace_id
        )
        allow_all = access_allow_all(self.extensions.db, self.workspace_id)
        return {
            "allow_without_confirmation": bool(
                value.get("allow_without_confirmation", False)
                or (user_policy is not None and user_policy.allow_without_confirmation)
                or allow_all
            ),
            "allowed_domains": list(
                dict.fromkeys(
                    [
                        *workspace_domain_list,
                        *(item for item in user_domains if isinstance(item, str)),
                        *sorted(unified_domains),
                    ]
                )
            ),
        }

    def _fetch_authorization_challenge(
        self,
        tool_call: dict[str, Any],
        arguments: dict[str, Any],
        chat_session_id: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        raw_url = arguments.get("url")
        hostname = urlparse(raw_url.strip()).hostname if isinstance(raw_url, str) else None
        hostname = hostname.casefold() if hostname else ""
        from app.domain.models import FetchAuthorizationRequest

        tool_call_id = str(tool_call.get("id") or "")
        pending = self.extensions.db.scalar(
            select(FetchAuthorizationRequest).where(
                FetchAuthorizationRequest.workspace_id == self.workspace_id,
                FetchAuthorizationRequest.chat_session_id == chat_session_id,
                FetchAuthorizationRequest.tool_call_id == tool_call_id,
            )
        )
        if pending is None:
            pending = FetchAuthorizationRequest(
                workspace_id=self.workspace_id,
                chat_session_id=chat_session_id,
                actor_id=self.actor_id,
                tool_call_id=tool_call_id,
                requested_url=raw_url.strip() if isinstance(raw_url, str) else "",
                hostname=hostname,
            )
            self.extensions.db.add(pending)
            self.extensions.db.commit()
        return self._failure(
            "fetch_domain_authorization_required",
            "网页抓取需要用户授权",
            data={
                "authorization_request_id": pending.id,
                "tool_call_id": tool_call_id,
                "tool_name": "fetch_web_page",
                "tool_label": "网页抓取工具",
                "requested_url": raw_url.strip() if isinstance(raw_url, str) else "",
                "hostname": hostname,
                "message_zh": (
                    f"我将使用网页抓取工具抓取 {raw_url.strip()} 网页，是否批准？"
                    if isinstance(raw_url, str)
                    else "我将使用网页抓取工具抓取网页，是否批准？"
                ),
            },
        )

    def _egress_authorization_challenge(
        self,
        hostname: str,
        chat_session_id: str,
        *,
        purpose: str | None = None,
        tool_call_id: str | None = None,
        resume_payload: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Durable user approval before a generic Agent egress host may be used.

        Mirrors ``_fetch_authorization_challenge`` but for the D2.1 generic
        egress channel: the only authorization resource is the canonical
        hostname (contract A). Pending is a suspension, never a failure — the
        Agent run is marked ``waiting_egress_authorization`` so the transcript
        shows a reviewable card instead of a hard error, and the model can
        resume once the user decides.
        """
        from app.core.config import get_settings
        from app.services.egress_approvals import EgressApprovalService

        settings = get_settings()
        service = EgressApprovalService(
            self.extensions.db,
            self.workspace_id,
            settings,
        )
        request = service.create_request(
            hostname=hostname,
            requested_by=self.actor_id,
            chat_session_id=chat_session_id or None,
            purpose=purpose,
            request_context={
                "tool_name": "sandbox_exec",
                "origin": "agent_runtime",
            },
            tool_call_id=tool_call_id,
            resume_payload=resume_payload,
        )
        return self._failure(
            "egress_authorization_required",
            "沙箱出站访问需要用户授权",
            data={
                "authorization_request_id": request.id,
                "tool_call_id": tool_call_id,
                "tool_name": "sandbox_exec",
                "tool_label": "沙箱命令工具",
                "hostname": request.hostname,
                "message_zh": (
                    f"沙箱内的智能体需要访问主机 {request.hostname}，是否批准这次出站连接？"
                ),
                "resume_mode": "server" if resume_payload else None,
            },
        )

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
        disclosed_tool_names: set[str] | None = None,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return self._failure("invalid_tool_call", "Tool call is malformed")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            return self._failure("invalid_tool_call", "Tool call has no function name")
        if disclosed_tool_names is not None and name not in disclosed_tool_names:
            return self._failure(
                "agent_tool_not_disclosed",
                "The requested tool was not disclosed for this model round; "
                "search and activate the capability before calling it",
            )
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
                "artifact_publish_card",
            }:
                return self._execute_canvas_tool(name, arguments, chat_session_id=chat_session_id)
            if name in {
                "component_register_manifest",
                "component_authorize",
                "component_list",
            }:
                return self._execute_component_admin_tool(name, arguments)
            if name in {"lg_graph_create", "lg_graph_propose_change"}:
                return self._execute_graph_proposal_tool(
                    name,
                    arguments,
                    chat_session_id=chat_session_id,
                    assistant_message_id=assistant_message_id,
                    source_message_id=source_message_id,
                )
            if name in {
                "lg_goal_read",
                "lg_goal_create",
                "lg_goal_confirm",
                "lg_goal_ask",
                "lg_goal_ask_batch",
                "lg_goal_edit_draft",
            }:
                return self._execute_goal_tool(
                    name,
                    arguments,
                    chat_session_id=chat_session_id,
                    assistant_message_id=assistant_message_id,
                    source_message_id=source_message_id,
                )
            if name == "subapp_observe":
                return self._execute_subapp_observe(arguments)
            if name == "subapp_patch_state":
                return self._execute_subapp_patch_state(arguments)
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
                "refresh_provider_models",
                "probe_provider",
                "verify_provider_declaration",
                "validate_provider_default_models",
                "configure_dashscope_balance",
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
                "set_models_enabled",
                "get_alert_email_config",
                "update_alert_email_config",
                "test_alert_email",
                "get_functional_model_defaults",
                "set_functional_model_default",
                "list_secret_labels",
                # Billing / usage settings.
                "list_budget_policies",
                "delete_budget_policy",
                "get_budget_status",
                "list_budget_alerts",
                "acknowledge_budget_alert",
                "get_exchange_rate",
                "set_exchange_rate",
                "refresh_exchange_rate",
                "list_manual_prices",
                "upsert_manual_price",
                "remove_manual_price",
                "get_usage_summary",
                "list_usage_events",
                "refresh_models_dev_snapshot",
                # Memory settings.
                "get_memory_policy",
                "update_memory_policy",
                "reindex_memory_embeddings",
                # Plugins / local Skill probe / MCP refresh / Skill manifest.
                "list_plugins",
                "toggle_plugin",
                "get_local_probe_policy",
                "update_local_probe_policy",
                "refresh_mcp_server",
                "update_skill_manifest",
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
            if name in {"download_external_image", "download_github_source"}:
                return self._execute_external_acquisition(
                    name,
                    tool_call,
                    arguments,
                    chat_session_id=chat_session_id,
                    assistant_message_id=assistant_message_id,
                    source_message_id=source_message_id,
                )
            if name == "fetch_web_page":
                policy = self._web_fetch_policy()
                policy_domains = policy["allowed_domains"]
                effective_domains = list(
                    dict.fromkeys([*allowed_domains, *policy_domains])
                )
                if not effective_domains and not policy["allow_without_confirmation"]:
                    from app.domain.models import FetchAuthorizationRequest

                    requested_url = arguments.get("url")
                    one_time = self.extensions.db.scalar(
                        select(FetchAuthorizationRequest).where(
                            FetchAuthorizationRequest.workspace_id == self.workspace_id,
                            FetchAuthorizationRequest.chat_session_id == chat_session_id,
                            FetchAuthorizationRequest.status == "approved",
                            FetchAuthorizationRequest.decision == "allow_once",
                            FetchAuthorizationRequest.requested_url == (
                                requested_url.strip() if isinstance(requested_url, str) else ""
                            ),
                        )
                    )
                    if one_time is not None:
                        one_time.status = "consumed"
                        self.extensions.db.commit()
                        effective_domains = [one_time.hostname]
                    else:
                        # Tool-runtime unit callers may supply a synthetic
                        # session id. A real chat authorization card requires a
                        # durable ChatSession foreign-key target; without one,
                        # preserve the legacy hard refusal rather than trying
                        # to insert an invalid authorization request.
                        session_exists = self.extensions.db.scalar(
                            select(ChatSession.id).where(
                                ChatSession.id == chat_session_id,
                                ChatSession.workspace_id == self.workspace_id,
                            )
                        )
                        if session_exists is None:
                            return self._failure(
                                "fetch_domain_not_authorized",
                                "Full-page extraction requires an explicitly authorized domain",
                            )
                        return self._fetch_authorization_challenge(
                            tool_call, arguments, chat_session_id
                        )
                if policy["allow_without_confirmation"] and not effective_domains:
                    requested_url = arguments.get("url")
                    hostname = (
                        urlparse(requested_url.strip()).hostname
                        if isinstance(requested_url, str)
                        else None
                    )
                    if hostname:
                        effective_domains = [hostname.casefold()]
                return self._fetch_web_page(arguments, effective_domains)
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
                if status in {"failed", "sandbox_timeout", "sandbox_command_failed"} and name == "sandbox_exec":
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
            activation = result.get("capability_activation")
            if isinstance(activation, dict):
                extension_meta["capability_activation"] = activation
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
        tool_name: str,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
        assistant_message_id: str | None,
        source_message_id: str | None,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Persist an inert graph change set from the current Agent turn.

        ``lg_graph_create`` targets a fresh graph for the session Goal (create
        mode); ``lg_graph_propose_change`` updates the existing graph linked to
        the Goal (update mode). Writes stay reviewable, the created graph gets
        its real IDs at confirmation, and a learning Session only ever binds a
        published Graph.
        """

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
        is_create = tool_name == "lg_graph_create"
        graph = None
        mode = "create" if is_create else "update"
        base_revision = 0
        if is_create:
            if goal.status not in {"confirmed", "candidate_ready", "approved"}:
                raise AppError(
                    409,
                    "goal_not_confirmed_for_graph",
                    "Confirm the Goal before creating a graph proposal",
                )
            existing_graph = db.scalar(
                select(Graph).where(
                    Graph.workspace_id == self.workspace_id,
                    Graph.goal_id == goal.id,
                )
            )
            if existing_graph is not None:
                raise AppError(
                    409,
                    "graph_already_exists",
                    "This Goal already has a graph; use lg_graph_propose_change to update it instead of creating another",
                    {"graph_id": existing_graph.id, "graph_status": existing_graph.status},
                )
        else:
            if goal.status not in {"confirmed", "candidate_ready", "approved"}:
                raise AppError(
                    409,
                    "goal_not_confirmed_for_graph",
                    "Confirm the Goal before proposing a graph update",
                )
            requested_graph_id = arguments.get("graph_id")
            if isinstance(requested_graph_id, str) and requested_graph_id:
                graph = db.scalar(
                    select(Graph).where(
                        Graph.workspace_id == self.workspace_id,
                        Graph.id == requested_graph_id,
                    )
                )
                if graph is None:
                    raise AppError(
                        404,
                        "graph_not_found",
                        "The requested Graph was not found",
                    )
                if graph.goal_id != goal.id:
                    raise AppError(
                        409,
                        "graph_goal_mismatch",
                        "The requested Graph does not belong to the session Goal",
                    )
            elif session.graph_id:
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
            else:
                graph = db.scalar(
                    select(Graph)
                    .where(
                        Graph.workspace_id == self.workspace_id,
                        Graph.goal_id == goal.id,
                        Graph.status == "published",
                    )
                    .order_by(Graph.created_at.desc())
                )
                if graph is None:
                    graph = db.scalar(
                        select(Graph)
                        .where(
                            Graph.workspace_id == self.workspace_id,
                            Graph.goal_id == goal.id,
                        )
                        .order_by(Graph.created_at.desc())
                    )
            if graph is None:
                raise AppError(
                    409,
                    "graph_update_target_required",
                    "No graph is linked to this session Goal yet; use lg_graph_create to create one",
                )
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
                "tool_name": tool_name,
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
                "graph_id_assigned_on_confirm": is_create,
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


    def _execute_goal_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
        assistant_message_id: str | None,
        source_message_id: str | None,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Goal tools: read / create / confirm the session-bound Goal.

        ``lg_goal_create`` binds a reviewable Goal draft to the session so the
        Goal/Graph orchestration loop can act on it. ``lg_goal_confirm`` moves
        it to ``confirmed`` (required before ``lg_graph_create``). Both write
        through the workspace-scoped session row and keep an audit trail.
        """

        if name == "lg_goal_read":
            return self._execute_goal_read(arguments, chat_session_id=chat_session_id)
        if name == "lg_goal_create":
            return self._execute_goal_create(
                arguments,
                chat_session_id=chat_session_id,
                assistant_message_id=assistant_message_id,
                source_message_id=source_message_id,
            )
        if name == "lg_goal_confirm":
            return self._execute_goal_confirm(
                arguments,
                chat_session_id=chat_session_id,
            )
        if name == "lg_goal_ask":
            return self._execute_goal_ask(
                arguments,
                chat_session_id=chat_session_id,
            )
        if name == "lg_goal_ask_batch":
            return self._execute_goal_ask_batch(
                arguments,
                chat_session_id=chat_session_id,
            )
        if name == "lg_goal_edit_draft":
            return self._execute_goal_edit_draft(
                arguments,
                chat_session_id=chat_session_id,
            )
        raise AppError(400, "unknown_goal_tool", f"Unknown goal tool: {name}")

    def _load_session_goal(
        self,
        chat_session_id: str,
        *,
        goal_id: str | None = None,
    ):
        from sqlalchemy import select

        from app.domain.models import ChatSession, Goal, Graph

        db = self.extensions.db
        session = db.scalar(
            select(ChatSession).where(
                ChatSession.workspace_id == self.workspace_id,
                ChatSession.id == chat_session_id,
            )
        )
        if session is None:
            raise AppError(404, "session_not_found", "Session was not found")
        resolved_goal_id = goal_id or session.goal_id
        if not resolved_goal_id:
            return session, None, None
        goal = db.scalar(
            select(Goal).where(
                Goal.workspace_id == self.workspace_id,
                Goal.id == resolved_goal_id,
            )
        )
        if goal is None:
            raise AppError(404, "goal_not_found", "The session Goal was not found")
        graph = None
        if session.graph_id:
            graph = db.scalar(
                select(Graph).where(
                    Graph.workspace_id == self.workspace_id,
                    Graph.id == session.graph_id,
                    Graph.goal_id == goal.id,
                )
            )
        if graph is None:
            # The candidate graph created through a confirmed proposal is not
            # bound to the Session (a learning Session can only bind a
            # published Graph), so fall back to the Goal's latest graph so
            # Goal/Graph tools can still address it by its real graph_id.
            graph = db.scalar(
                select(Graph)
                .where(
                    Graph.workspace_id == self.workspace_id,
                    Graph.goal_id == goal.id,
                    Graph.status == "published",
                )
                .order_by(Graph.created_at.desc())
            )
            if graph is None:
                graph = db.scalar(
                    select(Graph)
                    .where(
                        Graph.workspace_id == self.workspace_id,
                        Graph.goal_id == goal.id,
                    )
                    .order_by(Graph.created_at.desc())
                )
        return session, goal, graph

    def _goal_tool_summary(
        self,
        goal,
        graph=None,
        *,
        session_id: str,
        session_bound_graph_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "goal_id": goal.id,
            "title": goal.title,
            "status": goal.status,
            "intent": goal.intent,
            "time_limit": goal.time_limit,
            "desired_outcome": goal.desired_outcome,
            "raw_prompt": (goal.raw_prompt or "")[:2_000],
            "target_weight": goal.target_weight,
            "assumptions": goal.assumptions or [],
            "session_id": session_id,
            "graph_id": graph.id if graph else None,
            "graph_status": getattr(graph, "status", None),
            # A learning Session can only bind a published Graph: a
            # candidate graph is addressable via graph_id but is not
            # yet session-bound until the graph is published.
            "graph_bound_to_session": bool(graph)
            and graph.id == session_bound_graph_id,
        }

    def _execute_goal_read(
        self,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        goal_id = arguments.get("goal_id") if isinstance(arguments.get("goal_id"), str) else None
        session, goal, graph = self._load_session_goal(
            chat_session_id, goal_id=goal_id
        )
        if goal is None:
            return self._success(
                {
                    "session_id": session.id,
                    "goal_bound": False,
                    "message": "This session has no confirmed Goal yet. Use lg_goal_create to bind one.",
                },
                {"goal_bound": False},
                [],
            )
        summary = self._goal_tool_summary(
            goal, graph, session_id=session.id, session_bound_graph_id=session.graph_id
        )
        return self._success(
            summary,
            {
                "goal_id": goal.id,
                "goal_status": goal.status,
                "graph_bound": graph is not None,
                "graph_bound_to_session": summary["graph_bound_to_session"],
            },
            [],
        )

    def _execute_goal_create(
        self,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
        assistant_message_id: str | None,
        source_message_id: str | None,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        from sqlalchemy import select

        from app.domain.models import Goal, Message

        db = self.extensions.db
        session, existing_goal, graph = self._load_session_goal(chat_session_id)
        if existing_goal is not None:
            summary = self._goal_tool_summary(
                existing_goal,
                graph,
                session_id=session.id,
                session_bound_graph_id=session.graph_id,
            )
            return self._success(
                {**summary, "already_bound": True},
                {"goal_id": existing_goal.id, "already_bound": True, "goal_status": existing_goal.status},
                [],
            )

        if not assistant_message_id or not source_message_id:
            raise AppError(
                409,
                "goal_create_message_context_missing",
                "Creating a Goal requires the current persisted user and assistant messages",
            )
        source_user_message = db.scalar(
            select(Message).where(
                Message.workspace_id == self.workspace_id,
                Message.id == source_message_id,
                Message.session_id == session.id,
                Message.role == "user",
            )
        )
        if source_user_message is None:
            raise AppError(
                409,
                "goal_create_message_context_invalid",
                "The source user message does not belong to the current session",
            )

        title = str(arguments.get("title") or "").strip()
        if not title:
            raise AppError(422, "invalid_tool_arguments", "title is required")
        auto_confirm = bool(arguments.get("auto_confirm", False))
        raw_prompt = (
            str(arguments.get("raw_prompt") or "").strip()
            or (source_user_message.content or "").strip()
        )
        goal = Goal(
            workspace_id=self.workspace_id,
            title=title[:240],
            raw_prompt=raw_prompt[:10_000],
            status="confirmed" if auto_confirm else "clarifying",
            intent=str(arguments.get("intent") or "")[:240],
            time_limit=str(arguments.get("time_limit") or "")[:120],
            desired_outcome=str(arguments.get("desired_outcome") or "")[:4_000],
            constraints=(
                arguments.get("constraints")
                if isinstance(arguments.get("constraints"), dict)
                else {}
            ),
            assumptions=(
                arguments.get("assumptions")
                if isinstance(arguments.get("assumptions"), list)
                else []
            ),
        )
        db.add(goal)
        db.flush()
        session.goal_id = goal.id
        self.audit.record(
            actor_id=self.actor_id,
            action="goal.create",
            resource_type="goal",
            resource_id=goal.id,
            details={"source": "agent_tool", "tool": "lg_goal_create"},
        )
        db.commit()
        db.refresh(goal)
        summary = self._goal_tool_summary(goal, None, session_id=session.id)
        return self._success(
            {**summary, "already_bound": False},
            {
                "goal_id": goal.id,
                "goal_status": goal.status,
                "bound_session_id": session.id,
                "review_required": goal.status != "confirmed",
            },
            [],
        )

    def _execute_goal_confirm(
        self,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        from app.domain.models import Goal

        db = self.extensions.db
        session, goal, graph = self._load_session_goal(chat_session_id)
        if goal is None:
            raise AppError(
                409,
                "goal_not_found",
                "This session has no Goal bound yet; use lg_goal_create first",
            )
        if goal.status == "approved":
            raise AppError(
                409,
                "goal_already_published",
                "Published goals cannot be silently rewritten",
            )
        changed = False
        updates = {
            "title": (str, 240),
            "intent": (str, 240),
            "time_limit": (str, 120),
            "desired_outcome": (str, 4_000),
        }
        for field, (kind, limit) in updates.items():
            if field in arguments and arguments[field] is not None:
                value = str(arguments[field]).strip()
                if value:
                    setattr(goal, field, value[:limit])
                    changed = True
        if isinstance(arguments.get("target_weight"), int) and 1 <= arguments["target_weight"] <= 100:
            goal.target_weight = arguments["target_weight"]
            changed = True
        for nested_field in ("availability", "preferences", "constraints"):
            value = arguments.get(nested_field)
            if isinstance(value, dict):
                if nested_field in ("availability", "preferences"):
                    from app.domain.schemas.goals import (
                        AVAILABILITY_FIELDS,
                        PREFERENCES_FIELDS,
                        sanitize_goal_nested_dict,
                    )

                    value = sanitize_goal_nested_dict(
                        value,
                        allowed_fields=(
                            AVAILABILITY_FIELDS
                            if nested_field == "availability"
                            else PREFERENCES_FIELDS
                        ),
                    )
                setattr(goal, nested_field, value)
                changed = True
        if isinstance(arguments.get("assumptions"), list):
            goal.assumptions = arguments["assumptions"]
            changed = True
        if goal.status != "confirmed":
            goal.status = "confirmed"
            changed = True
        if not changed:
            # Still surface a stable result for the model.
            summary = self._goal_tool_summary(
                goal, graph, session_id=session.id, session_bound_graph_id=session.graph_id
            )
            return self._success(
                {**summary, "already_confirmed": True},
                {"goal_id": goal.id, "goal_status": goal.status},
                [],
            )
        self.audit.record(
            actor_id=self.actor_id,
            action="goal.confirm",
            resource_type="goal",
            resource_id=goal.id,
            details={"source": "agent_tool", "tool": "lg_goal_confirm"},
        )
        db.commit()
        db.refresh(goal)
        summary = self._goal_tool_summary(
            goal, graph, session_id=session.id, session_bound_graph_id=session.graph_id
        )
        return self._success(
            {**summary, "already_confirmed": False},
            {
                "goal_id": goal.id,
                "goal_status": goal.status,
                "graph_propose_available": True,
            },
            [],
        )

    def _execute_goal_ask(
        self,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Ask the user with an interactive card (never plain text).

        Emits a channel-A trusted component question card (single_choice /
        multiple_choice / fill_blank / short_answer_table). The user's answer
        arrives as the next user message and the Agent continues from it — the
        answer is never fabricated here. The card is delivered through the same
        artifact pipeline as canvas_emit_trusted_component so the UI renders it
        inline and two-way interaction stays within the conversation.
        """
        from app.services.canvas_cards import build_trusted_component_part

        db = self.extensions.db
        question = str(arguments.get("question") or "").strip()
        if not question:
            raise AppError(422, "invalid_tool_arguments", "question is required")
        if len(question) > 500:
            raise AppError(422, "invalid_tool_arguments", "question is too long")
        input_type = str(arguments.get("input_type") or "single_choice").strip()
        if input_type not in {
            "single_choice",
            "multiple_choice",
            "fill_blank",
            "short_answer_table",
            "date",
        }:
            raise AppError(422, "invalid_tool_arguments", "input_type is not supported")
        options_raw = arguments.get("options")
        options: list[dict[str, Any]] = []
        if options_raw is not None:
            if not isinstance(options_raw, list) or len(options_raw) > 8:
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "options must be an array of at most 8 items",
                )
            for item in options_raw:
                if not isinstance(item, dict):
                    raise AppError(
                        422, "invalid_tool_arguments", "options items must be objects"
                    )
                option_id = str(item.get("id") or "").strip()
                label = str(item.get("label") or "").strip()
                if not option_id or not label:
                    raise AppError(
                        422, "invalid_tool_arguments", "options items need id and label"
                    )
                entry: dict[str, Any] = {"id": option_id[:80], "label": label[:500]}
                description = item.get("description")
                if isinstance(description, str) and description.strip():
                    entry["description"] = description.strip()[:2_000]
                options.append(entry)
        if input_type in {"single_choice", "multiple_choice"} and not options:
            raise AppError(
                422, "invalid_tool_arguments", "choice questions need options"
            )
        allow_custom = arguments.get("allow_custom", True)
        allow_skip = arguments.get("allow_skip", True)
        component_id = (
            str(arguments["component_id"]).strip()[:160]
            if isinstance(arguments.get("component_id"), str)
            and arguments["component_id"].strip()
            else None
        )
        if input_type in {"single_choice", "multiple_choice"}:
            props: dict[str, Any] = {
                "title": question,
                "options": options,
                "allow_custom": bool(allow_custom),
                "allow_skip": bool(allow_skip),
            }
        else:
            props = {
                "title": question,
                "description": "直接在下方填写，回答会用于确认目标与图谱边界。",
                "multiline": input_type == "short_answer_table",
                "placeholder": "请输入你的回答…",
            }
        if input_type == "date":
            # Date questions render a calendar card that also shows the user's
            # learning schedule, so the chosen slot fits their plan.
            part = build_trusted_component_part(
                component_type="question_batch",
                props={
                    "title": question,
                    "description": (
                        "从日历中选择日期（日历中已标注你的学习日程），"
                        "或直接在下方手动输入。"
                    ),
                    "questions": [
                        {
                            "key": "date",
                            "prompt": question,
                            "input_type": "date",
                            "allow_custom": True,
                            "allow_skip": bool(allow_skip),
                            "required": False,
                        }
                    ],
                    "submit_label": "确认日期",
                },
                component_id=component_id or f"question_batch_{uuid4().hex[:10]}",
                allowed_events=["submit"],
                schema_version="1.0",
            )
        else:
            part = build_trusted_component_part(
                component_type=input_type,
                props=props,
                component_id=component_id,
                allowed_events=["submit"],
                schema_version="1.0",
            )
        component_data = part.get("data") or {}
        self.audit.record(
            actor_id=self.actor_id,
            action="goal.ask_card",
            resource_type="chat_session",
            resource_id=chat_session_id,
            details={
                "component_id": component_data.get("component_id"),
                "input_type": input_type,
                "question": question[:200],
            },
        )
        db.commit()
        return self._success(
            {
                "asked": True,
                "component_id": component_data.get("component_id"),
                "component_type": input_type,
                "question": question,
                "waiting_for_answer": True,
                "note": (
                    "问题卡片已发出。等待用户回答后继续：下一轮用户消息就是答案，"
                    "据此创建/确认 Goal 或生成图谱。不要编造用户答案。"
                ),
            },
            {
                "tool": "lg_goal_ask",
                "component_id": component_data.get("component_id"),
                "artifact": part,
            },
            [],
        )

    def _execute_goal_ask_batch(
        self,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Ask 2-8 related Goal questions in one aggregated card.

        The card renders each sub-question as its own control and submits all
        answers together; the answers arrive as the next user message. This is
        the preferred way to batch clarification and cuts round trips.
        """
        from app.services.canvas_cards import build_trusted_component_part

        db = self.extensions.db
        title = str(arguments.get("title") or "").strip() or "目标澄清（聚合问答）"
        if len(title) > 500:
            raise AppError(422, "invalid_tool_arguments", "title is too long")
        raw_questions = arguments.get("questions")
        if not isinstance(raw_questions, list) or not 2 <= len(raw_questions) <= 8:
            raise AppError(
                422,
                "invalid_tool_arguments",
                "questions must be an array of 2 to 8 items",
            )
        questions: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for item in raw_questions:
            if not isinstance(item, dict):
                raise AppError(422, "invalid_tool_arguments", "questions items must be objects")
            key = str(item.get("key") or "").strip()
            prompt = str(item.get("prompt") or "").strip()
            if not key or not prompt:
                raise AppError(
                    422, "invalid_tool_arguments", "every question needs key and prompt"
                )
            if key in seen_keys:
                raise AppError(422, "invalid_tool_arguments", "question keys must be unique")
            seen_keys.add(key)
            input_type = str(item.get("input_type") or "single_choice").strip()
            if input_type not in {
                "single_choice",
                "multiple_choice",
                "fill_blank",
                "short_answer_table",
                "date",
            }:
                raise AppError(422, "invalid_tool_arguments", "input_type is not supported")
            options: list[dict[str, Any]] = []
            options_raw = item.get("options")
            if options_raw is not None:
                if not isinstance(options_raw, list) or len(options_raw) > 8:
                    raise AppError(
                        422,
                        "invalid_tool_arguments",
                        "options must be an array of at most 8 items",
                    )
                for option in options_raw:
                    if not isinstance(option, dict):
                        raise AppError(
                            422,
                            "invalid_tool_arguments",
                            "options items must be objects",
                        )
                    option_id = str(option.get("id") or "").strip()
                    label = str(option.get("label") or "").strip()
                    if not option_id or not label:
                        raise AppError(
                            422,
                            "invalid_tool_arguments",
                            "options items need id and label",
                        )
                    entry: dict[str, Any] = {
                        "id": option_id[:80],
                        "label": label[:500],
                    }
                    description = option.get("description")
                    if isinstance(description, str) and description.strip():
                        entry["description"] = description.strip()[:2_000]
                    options.append(entry)
            if input_type in {"single_choice", "multiple_choice"} and not options:
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    f"question {key}: choice questions need options",
                )
            questions.append(
                {
                    "key": key[:80],
                    "prompt": prompt[:500],
                    "input_type": input_type,
                    "options": options,
                    "allow_custom": bool(item.get("allow_custom", True)),
                    "allow_skip": bool(item.get("allow_skip", True)),
                    "required": bool(item.get("required", False)),
                }
            )
        component_id = (
            str(arguments["component_id"]).strip()[:160]
            if isinstance(arguments.get("component_id"), str)
            and arguments["component_id"].strip()
            else None
        )
        part = build_trusted_component_part(
            component_type="question_batch",
            props={
                "title": title,
                "description": (
                    "一张卡片内包含多个问题，全部作答后一次提交；"
                    "可跳过的问题会记下透明假设。"
                ),
                "questions": questions,
                "submit_label": "一次提交全部答案",
            },
            component_id=component_id or f"question_batch_{uuid4().hex[:10]}",
            allowed_events=["submit"],
            schema_version="1.0",
        )
        component_data = part.get("data") or {}
        self.audit.record(
            actor_id=self.actor_id,
            action="goal.ask_batch_card",
            resource_type="chat_session",
            resource_id=chat_session_id,
            details={
                "component_id": component_data.get("component_id"),
                "question_count": len(questions),
                "keys": [q["key"] for q in questions],
            },
        )
        db.commit()
        return self._success(
            {
                "asked": True,
                "component_id": component_data.get("component_id"),
                "component_type": "question_batch",
                "question_count": len(questions),
                "questions": [
                    {"key": q["key"], "prompt": q["prompt"], "input_type": q["input_type"]}
                    for q in questions
                ],
                "waiting_for_answer": True,
                "note": (
                    "聚合问答卡片已发出。用户一次提交全部答案后，下一条用户消息就是答案，"
                    "据此继续创建/确认 Goal 或生成图谱。不要编造用户答案。"
                ),
            },
            {
                "tool": "lg_goal_ask_batch",
                "component_id": component_data.get("component_id"),
                "artifact": part,
            },
            [],
        )

    def _execute_goal_edit_draft(
        self,
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Open a two-way editable Goal-draft card (sub-page style).

        The card is pre-filled with the current Goal fields; the user edits and
        submits, and the edited values arrive as the next user message. The
        Agent then applies them through lg_goal_confirm. Requires a session-
        bound Goal (created with lg_goal_create).
        """
        from app.services.canvas_cards import build_trusted_component_part

        db = self.extensions.db
        goal_id = (
            str(arguments["goal_id"]).strip()
            if isinstance(arguments.get("goal_id"), str)
            and arguments["goal_id"].strip()
            else None
        )
        session, goal, graph = self._load_session_goal(
            chat_session_id, goal_id=goal_id
        )
        if goal is None:
            raise AppError(
                409,
                "goal_not_found",
                "This session has no Goal bound yet; use lg_goal_create first",
            )
        focus = str(arguments.get("focus") or "all").strip()
        if focus not in {"title", "time", "outcome", "all"}:
            focus = "all"
        props = {
            "title": "编辑目标草稿（双向同步）",
            "description": "修改后点击提交，智能体会按你改动的字段继续确认目标。",
            "goal_id": goal.id,
            "goal_status": goal.status,
            "focus": focus,
            "draft": {
                "title": goal.title,
                "intent": goal.intent or "",
                "time_limit": goal.time_limit or "",
                "desired_outcome": goal.desired_outcome or "",
            },
            "submit_label": "提交草稿修改",
        }
        part = build_trusted_component_part(
            component_type="goal_draft_editor",
            props=props,
            component_id=f"goal_draft_editor_{goal.id[:8]}",
            allowed_events=["submit"],
            schema_version="1.0",
        )
        component_data = part.get("data") or {}
        self.audit.record(
            actor_id=self.actor_id,
            action="goal.edit_draft_card",
            resource_type="goal",
            resource_id=goal.id,
            details={
                "component_id": component_data.get("component_id"),
                "focus": focus,
                "chat_session_id": chat_session_id,
            },
        )
        db.commit()
        return self._success(
            {
                "opened": True,
                "goal_id": goal.id,
                "goal_status": goal.status,
                "component_id": component_data.get("component_id"),
                "note": (
                    "目标草稿编辑卡片已发出。用户提交后，"
                    "用 lg_goal_confirm 按提交的字段确认目标。"
                ),
            },
            {
                "tool": "lg_goal_edit_draft",
                "goal_id": goal.id,
                "artifact": part,
            },
            [],
        )

    def _execute_subapp_observe(
        self,
        arguments: dict[str, Any],
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Read-only observation of workspace sub-application interaction events.

        Returns bounded aggregates (total count, per-event-type counts) plus a
        small, size/field-limited digest of the most recent events. Payloads are
        treated as untrusted data: only a bounded top-level key list, truncated
        scalar values, byte sizes, and a sha256 are surfaced — never verbatim
        content. This tool performs SELECTs only and never writes.
        """
        from app.domain.models import SubAppInteractionEvent

        db = self.extensions.db
        workspace_id = self.workspace_id

        # --- validate / bound arguments (defensive; never trust the model) ---
        session_id = arguments.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise AppError(422, "invalid_tool_arguments", "session_id must be a string")
        session_id = session_id.strip() if isinstance(session_id, str) else None
        if session_id and len(session_id) > SUBAPP_OBSERVE_SESSION_ID_MAX:
            raise AppError(422, "invalid_tool_arguments", "session_id is too long")

        event_type = arguments.get("event_type")
        if event_type is not None and not isinstance(event_type, str):
            raise AppError(422, "invalid_tool_arguments", "event_type must be a string")
        event_type = event_type.strip() if isinstance(event_type, str) else None
        if event_type and len(event_type) > SUBAPP_OBSERVE_EVENT_TYPE_MAX:
            raise AppError(422, "invalid_tool_arguments", "event_type is too long")

        limit = arguments.get("limit", 20)
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise AppError(422, "invalid_tool_arguments", "limit must be an integer")
        limit = max(1, min(limit, SUBAPP_OBSERVE_MAX_LIMIT))

        time_from: datetime | None = None
        time_to: datetime | None = None
        time_range = arguments.get("time_range")
        if time_range is not None:
            if not isinstance(time_range, dict):
                raise AppError(422, "invalid_tool_arguments", "time_range must be an object")
            unknown = set(time_range) - {"from", "to"}
            if unknown:
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    f"time_range has unexpected field(s): {sorted(unknown)}",
                )
            time_from = self._parse_iso_datetime(time_range.get("from"), "time_range.from")
            time_to = self._parse_iso_datetime(time_range.get("to"), "time_range.to")
        if (
            time_from is not None
            and time_to is not None
            and time_from > time_to
        ):
            raise AppError(
                422,
                "invalid_tool_arguments",
                "time_range.from must not be after time_range.to",
            )

        # --- scope: workspace only, optional filters ---
        filters = [SubAppInteractionEvent.workspace_id == workspace_id]
        if session_id:
            filters.append(SubAppInteractionEvent.session_id == session_id)
        if event_type:
            filters.append(SubAppInteractionEvent.event_type == event_type)
        if time_from is not None:
            filters.append(SubAppInteractionEvent.created_at >= time_from)
        if time_to is not None:
            filters.append(SubAppInteractionEvent.created_at <= time_to)

        # --- aggregates via SQL (bounded output) ---
        total_count = int(
            db.scalar(
                select(func.count(SubAppInteractionEvent.id)).where(*filters)
            )
            or 0
        )

        type_rows = db.execute(
            select(
                SubAppInteractionEvent.event_type,
                func.count(SubAppInteractionEvent.id),
            )
            .where(*filters)
            .group_by(SubAppInteractionEvent.event_type)
            .order_by(func.count(SubAppInteractionEvent.id).desc())
        ).all()
        count_by_type: dict[str, int] = {}
        other_count = 0
        for row in type_rows:
            key = str(row[0] or "unknown")
            if len(count_by_type) < SUBAPP_OBSERVE_MAX_EVENT_TYPES:
                count_by_type[key] = int(row[1])
            else:
                other_count += int(row[1])
        if other_count:
            count_by_type["__other_types__"] = other_count

        recent_rows = list(
            db.scalars(
                select(SubAppInteractionEvent)
                .where(*filters)
                .order_by(
                    SubAppInteractionEvent.created_at.desc(),
                    SubAppInteractionEvent.id.desc(),
                )
                .limit(limit)
            ).all()
        )
        recent_events: list[dict[str, Any]] = [
            self._subapp_event_digest(event) for event in recent_rows
        ]

        return self._success(
            {
                "filters_applied": {
                    "session_id": session_id,
                    "event_type": event_type,
                    "time_range": {
                        "from": time_from.isoformat() if time_from else None,
                        "to": time_to.isoformat() if time_to else None,
                    },
                    "limit": limit,
                },
                "total_events": total_count,
                "events_by_type": count_by_type,
                "recent_events": recent_events,
                "recent_events_truncated": total_count > len(recent_events),
            },
            {
                "tool": "subapp_observe",
                "total_events": total_count,
                "event_type_count": len(count_by_type),
                "recent_events_returned": len(recent_events),
            },
            [],
        )

    def _execute_subapp_patch_state(
        self,
        arguments: dict[str, Any],
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Write one state snapshot to an interactive sub-application session (T2.5).

        This is the agent's ONLY write path for sub-application state. It goes
        through ``SubAppService.propose_state``, which contract-validates the
        state against the session's snapshotted ``state_schema``, CAS-advances
        the optimistic-lock ``state_version``, and persists an immutable
        snapshot (with a canonical-JSON sha256). The agent never holds an
        iframe reference and must not try to postMessage or otherwise mutate
        the renderer directly.

        ``expected_version`` is mandatory: the caller must read the session's
        current ``state_version`` first (e.g. via ``subapp_observe`` or the
        session view). A stale expectation fails fast here with the current
        version so the caller can re-read and retry; the authoritative race
        guard remains the CAS inside ``propose_state``.
        """
        from app.services.subapps import SubAppService

        db = self.extensions.db
        workspace_id = self.workspace_id
        actor_id = self.actor_id

        # --- validate / bound arguments (defensive; never trust the model) ---
        session_id = arguments.get("session_id")
        if not isinstance(session_id, str):
            raise AppError(422, "invalid_tool_arguments", "session_id must be a string")
        session_id = session_id.strip()
        if not session_id:
            raise AppError(422, "invalid_tool_arguments", "session_id must not be empty")
        if len(session_id) > SUBAPP_OBSERVE_SESSION_ID_MAX:
            raise AppError(
                422,
                "invalid_tool_arguments",
                "session_id is too long",
                {"max_length": SUBAPP_OBSERVE_SESSION_ID_MAX},
            )

        state = arguments.get("state")
        if not isinstance(state, dict):
            raise AppError(422, "invalid_tool_arguments", "state must be an object")

        expected_version = arguments.get("expected_version")
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
        ):
            raise AppError(
                422,
                "invalid_tool_arguments",
                "expected_version must be an integer >= 0",
            )

        service = SubAppService(db, workspace_id, actor_id)

        # --- pre-read the current version: scope the session to this workspace
        # and give the caller the exact version to retry with on a conflict.
        # This is a helper, not the race guard — propose_state's CAS UPDATE is
        # the authoritative check against a concurrent writer. ---
        try:
            current = service.get_session(session_id)
        except AppError as exc:
            return self._failure(
                exc.code,
                exc.message,
                data={"session_id": session_id},
            )
        if current.state_version != expected_version:
            return self._failure(
                "subapp_state_version_conflict",
                "Sub-application state version moved; re-read the current "
                "state_version and retry",
                data={
                    "session_id": session_id,
                    "current_version": current.state_version,
                    "expected_version": expected_version,
                },
            )

        # --- CAS write through the server (contract + protocol validation) ---
        try:
            new_version = service.propose_state(
                session_id,
                state,
                expected_version=expected_version,
            )
        except AppError as exc:
            details = dict(exc.details or {})
            details.setdefault("session_id", session_id)
            return self._failure(exc.code, exc.message, data=details)

        # --- read back the persisted sha256 for the write confirmation ---
        try:
            after = service.get_session(session_id)
        except AppError:
            after = None
        state_sha256 = (
            getattr(after, "state_sha256", None) if after is not None else None
        )

        return self._success(
            {
                "session_id": session_id,
                "new_state_version": new_version,
                "state_sha256": state_sha256,
            },
            {
                "tool": "subapp_patch_state",
                "new_state_version": new_version,
                "state_sha256": state_sha256,
            },
            [],
        )

    def _subapp_event_digest(self, event: Any) -> dict[str, Any]:
        """One bounded summary row for a SubAppInteractionEvent.

        Serializes created_at to ISO-8601 (JSON-safe) and reduces the payload to
        a size/field-limited digest.
        """
        created_at = getattr(event, "created_at", None)
        return {
            "event_id": getattr(event, "id", None),
            "session_id": getattr(event, "session_id", None),
            "event_type": getattr(event, "event_type", None),
            "actor_id": getattr(event, "actor_id", None),
            "chat_session_id": getattr(event, "chat_session_id", None),
            "artifact_version_id": getattr(event, "artifact_version_id", None),
            "created_at": (
                created_at.isoformat() if isinstance(created_at, datetime) else None
            ),
            "payload": self._bounded_payload_summary(
                getattr(event, "payload_json", None),
                getattr(event, "payload_sha256", None),
            ),
        }

    def _bounded_payload_summary(self, raw: Any, sha: Any) -> dict[str, Any]:
        """Reduce an untrusted payload to a bounded, field-limited digest.

        Never echoes arbitrary payload text back verbatim: object payloads
        expose a bounded top-level key list and truncated scalar values;
        non-object payloads expose a short truncated preview; oversized or
        unparseable payloads are reduced to size + sha256 only.
        """
        sha = str(sha) if sha else None
        if not isinstance(raw, str) or not raw:
            return {"state": "empty", "size_bytes": 0, "sha256": sha}
        try:
            size_bytes = len(raw.encode("utf-8"))
        except Exception:
            size_bytes = len(raw)
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {
                "state": "unparseable",
                "size_bytes": size_bytes,
                "preview": raw[:SUBAPP_OBSERVE_PAYLOAD_PREVIEW_CHARS],
                "sha256": sha,
            }
        if isinstance(payload, dict):
            keys = list(payload.keys())[:SUBAPP_OBSERVE_PAYLOAD_SUMMARY_KEYS]
            scalars: dict[str, str] = {}
            for key in keys:
                value = payload.get(key)
                if isinstance(value, (str, int, float, bool)) and value is not None:
                    scalars[key] = self._summarize_scalar(value)
            return {
                "state": "object",
                "size_bytes": size_bytes,
                "keys": keys,
                "scalars": scalars,
                "sha256": sha,
            }
        if isinstance(payload, (list, str, int, float, bool)):
            preview = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            return {
                "state": type(payload).__name__,
                "size_bytes": size_bytes,
                "preview": preview[:SUBAPP_OBSERVE_PAYLOAD_PREVIEW_CHARS],
                "sha256": sha,
            }
        return {"state": "unknown", "size_bytes": size_bytes, "sha256": sha}

    @staticmethod
    def _summarize_scalar(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        text = str(value)
        if len(text) > SUBAPP_OBSERVE_PAYLOAD_SCALAR_CHARS:
            return text[:SUBAPP_OBSERVE_PAYLOAD_SCALAR_CHARS] + "…"
        return text

    @staticmethod
    def _parse_iso_datetime(value: Any, label: str) -> datetime | None:
        """Parse an optional ISO-8601 date-time into a tz-aware UTC datetime.

        Returns None when the value is missing/blank; raises AppError on any
        malformed or non-string input so invalid filters fail loudly.
        """
        if value is None:
            return None
        if not isinstance(value, str):
            raise AppError(422, "invalid_tool_arguments", f"{label} must be an ISO-8601 string")
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        elif raw.endswith("z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise AppError(
                422,
                "invalid_tool_arguments",
                f"{label} is not a valid ISO-8601 date-time",
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

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
                    "default_realtime_transcription_model_id",
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
        if name == "refresh_provider_models":
            self._require_provider_manage()
            provider_id = str(arguments.get("provider_id") or "").strip()
            if not provider_id:
                raise AppError(422, "invalid_tool_arguments", "provider_id is required")
            result = service.refresh_models(provider_id)
            return self._success(
                result,
                {
                    "tool": name,
                    "provider_id": provider_id,
                    "mutated": True,
                },
                [],
            )
        if name == "probe_provider":
            provider_id = str(arguments.get("provider_id") or "").strip()
            if not provider_id:
                raise AppError(422, "invalid_tool_arguments", "provider_id is required")
            result = service.probe_connectivity(provider_id)
            return self._success(
                result,
                {"tool": name, "provider_id": provider_id},
                [],
            )
        if name == "verify_provider_declaration":
            self._require_provider_manage()
            provider_id = str(arguments.get("provider_id") or "").strip()
            status = str(arguments.get("status") or "").strip()
            if not provider_id or not status:
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "provider_id and status are required",
                )
            result = service.update_declaration_status(provider_id, status)
            return self._success(
                result,
                {
                    "tool": name,
                    "provider_id": provider_id,
                    "mutated": True,
                },
                [],
            )
        if name == "validate_provider_default_models":
            repair = arguments.get("repair") is True
            if repair:
                self._require_provider_manage()
            provider_id = (
                str(arguments["provider_id"]).strip()
                if isinstance(arguments.get("provider_id"), str)
                and arguments["provider_id"].strip()
                else None
            )
            result = service.validate_default_models(provider_id, repair=repair)
            return self._success(
                result,
                {"tool": name, "provider_id": provider_id, "repair": repair},
                [],
            )
        if name == "configure_dashscope_balance":
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
            result = service.configure_balance_credential(provider_id, secret_label)
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
            BudgetAlertView,
            BudgetPolicyView,
            MemoryPolicyUpdateRequest,
            ManualPriceView,
            PluginToggleRequest,
            PluginView,
            SettingUpdateRequest,
            UsageEventView,
        )
        from app.domain.settings import FUNCTIONAL_MODEL_DEFAULTS_SETTING_KEY
        from app.providers.catalog import provider_type_spec
        from app.services import alert_email
        from app.services.billing import BillingService
        from app.services.management import PluginService, SettingsService, UsageService
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
            from app.domain.schemas.management import ProviderBalanceView

            provider_id = str(arguments.get("provider_id") or "").strip()
            if not provider_id:
                raise AppError(
                    422, "invalid_tool_arguments", "provider_id is required"
                )
            raw = self._provider_service().balance(provider_id)
            # balance() returns a datetime queried_at; serialize through the
            # view schema so the transcript stays JSON-safe.
            result = ProviderBalanceView.model_validate(raw).model_dump(mode="json")
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
                enabled,
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
        if name == "set_models_enabled":
            provider_id = str(arguments.get("provider_id") or "").strip()
            states = arguments.get("states")
            if not provider_id or not isinstance(states, dict) or not states:
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "provider_id and a non-empty states object are required",
                )
            normalized = {
                str(model_id): bool(value)
                for model_id, value in states.items()
                if isinstance(model_id, str) and model_id.strip()
            }
            if not normalized:
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "states must map model ids to booleans",
                )
            result = self._provider_service().update_model_states(
                provider_id, normalized
            )
            return self._success(
                result,
                {
                    "tool": name,
                    "provider_id": provider_id,
                    "updated_models": len(normalized),
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

        # --- Billing / usage settings -------------------------------------------------
        if name in {"list_budget_policies", "get_budget_status", "list_budget_alerts"}:
            from app.domain.schemas.management import BudgetStatusView

            billing = BillingService(db, self.workspace_id, self.actor_id)
            if name == "list_budget_policies":
                items = [
                    BudgetPolicyView.model_validate(item).model_dump(mode="json")
                    for item in billing.list_budget_policies()
                ]
            elif name == "get_budget_status":
                items = [
                    BudgetStatusView.model_validate(item).model_dump(mode="json")
                    for item in billing.budget_statuses()
                ]
            else:
                items = [
                    BudgetAlertView.model_validate(item).model_dump(mode="json")
                    for item in billing.list_alerts()
                ]
            return self._success(
                {"items": items, "count": len(items)},
                {"tool": name, "count": len(items)},
                [],
            )
        if name == "delete_budget_policy":
            self._require_provider_manage()
            policy_id = str(arguments.get("policy_id") or "").strip()
            if not policy_id:
                raise AppError(422, "invalid_tool_arguments", "policy_id is required")
            BillingService(db, self.workspace_id, self.actor_id).delete_budget_policy(
                policy_id
            )
            return self._success(
                {"policy_id": policy_id, "deleted": True},
                {"tool": name, "policy_id": policy_id, "mutated": True},
                [],
            )
        if name == "acknowledge_budget_alert":
            self._require_provider_manage()
            alert_id = str(arguments.get("alert_id") or "").strip()
            if not alert_id:
                raise AppError(422, "invalid_tool_arguments", "alert_id is required")
            alert = BillingService(
                db, self.workspace_id, self.actor_id
            ).acknowledge_alert(alert_id)
            return self._success(
                BudgetAlertView.model_validate(alert).model_dump(mode="json"),
                {"tool": name, "alert_id": alert_id, "mutated": True},
                [],
            )
        if name == "get_exchange_rate":
            from app.domain.schemas.management import ExchangeRateInfo

            rate = BillingService(
                db, self.workspace_id, self.actor_id
            ).current_exchange_rate()
            return self._success(
                ExchangeRateInfo.model_validate(rate, from_attributes=True).model_dump(
                    mode="json"
                ),
                {"tool": name},
                [],
            )
        if name == "set_exchange_rate":
            self._require_provider_manage()
            rate = arguments.get("rate")
            if not isinstance(rate, (int, float)) or rate <= 0:
                raise AppError(
                    422, "invalid_tool_arguments", "rate must be a positive number"
                )
            from app.domain.schemas.management import ExchangeRateInfo

            result = BillingService(
                db, self.workspace_id, self.actor_id
            ).set_exchange_rate(float(rate))
            return self._success(
                ExchangeRateInfo.model_validate(
                    result, from_attributes=True
                ).model_dump(mode="json"),
                {"tool": name, "mutated": True},
                [],
            )
        if name == "refresh_exchange_rate":
            self._require_provider_manage()
            from app.domain.schemas.management import ExchangeRateInfo

            result = BillingService(
                db, self.workspace_id, self.actor_id
            ).refresh_exchange_rate_from_network()
            return self._success(
                ExchangeRateInfo.model_validate(
                    result, from_attributes=True
                ).model_dump(mode="json"),
                {"tool": name, "mutated": True},
                [],
            )
        if name == "list_manual_prices":
            raw = BillingService(
                db, self.workspace_id, self.actor_id
            ).list_manual_prices()
            items = [
                ManualPriceView.model_validate(item).model_dump(mode="json")
                for item in raw
            ]
            return self._success(
                {"items": items, "count": len(items)},
                {"tool": name, "count": len(items)},
                [],
            )
        if name == "upsert_manual_price":
            self._require_provider_manage()
            from app.domain.schemas.management import ManualPriceUpsertRequest

            model_id = str(arguments.get("model_id") or "").strip()
            if not model_id:
                raise AppError(422, "invalid_tool_arguments", "model_id is required")
            try:
                payload = ManualPriceUpsertRequest(
                    model_id=model_id,
                    provider_id=str(arguments.get("provider_id") or "*").strip(),
                    currency=str(arguments.get("currency") or "USD").strip(),
                    input_per_million=float(arguments.get("input_per_million", 0)),
                    cached_input_per_million=(
                        float(arguments["cached_input_per_million"])
                        if arguments.get("cached_input_per_million") is not None
                        else None
                    ),
                    output_per_million=float(arguments.get("output_per_million", 0)),
                    fixed_per_call=float(arguments.get("fixed_per_call", 0)),
                )
            except (TypeError, ValueError) as exc:
                raise AppError(
                    422, "invalid_tool_arguments", f"Invalid manual price: {exc}"
                ) from exc
            result = BillingService(
                db, self.workspace_id, self.actor_id
            ).upsert_manual_price(**payload.model_dump())
            return self._success(
                ManualPriceView.model_validate(result).model_dump(mode="json"),
                {"tool": name, "model_id": model_id, "mutated": True},
                [],
            )
        if name == "remove_manual_price":
            self._require_provider_manage()
            model_id = str(arguments.get("model_id") or "").strip()
            if not model_id:
                raise AppError(422, "invalid_tool_arguments", "model_id is required")
            removed = BillingService(
                db, self.workspace_id, self.actor_id
            ).remove_manual_price(model_id)
            return self._success(
                {"model_id": model_id, "removed_count": removed},
                {"tool": name, "model_id": model_id, "mutated": True},
                [],
            )
        if name in {"get_usage_summary", "list_usage_events"}:
            usage_service = UsageService(db, self.workspace_id)
            provider_id = (
                str(arguments["provider_id"]).strip()
                if isinstance(arguments.get("provider_id"), str)
                else None
            )
            model_id = (
                str(arguments["model_id"]).strip()
                if isinstance(arguments.get("model_id"), str)
                else None
            )
            feature = (
                str(arguments["feature"]).strip()
                if isinstance(arguments.get("feature"), str)
                else None
            )
            if name == "get_usage_summary":
                summary = usage_service.summary(
                    provider_id=provider_id,
                    model_id=model_id,
                    feature=feature,
                )
                return self._success(
                    summary.model_dump(mode="json"),
                    {"tool": name},
                    [],
                )
            events = usage_service.events(
                provider_id=provider_id,
                model_id=model_id,
                feature=feature,
            )
            limit = (
                int(arguments["limit"]) if isinstance(arguments.get("limit"), int) else 100
            )
            items = [
                UsageEventView.model_validate(item).model_dump(mode="json")
                for item in events[: min(max(limit, 1), 200)]
            ]
            return self._success(
                {"items": items, "count": len(items)},
                {"tool": name, "count": len(items)},
                [],
            )
        if name == "refresh_models_dev_snapshot":
            self._require_provider_manage()
            from app.domain.schemas.management import ModelsDevSnapshotStatus
            from app.providers.models_dev import refresh_snapshot

            try:
                status = ModelsDevSnapshotStatus.model_validate(
                    refresh_snapshot()
                ).model_dump(mode="json")
            except Exception as exc:  # noqa: BLE001 - network and payload faults alike
                raise AppError(
                    502,
                    "models_dev_refresh_failed",
                    f"Refreshing tariffs from models.dev failed: {exc}",
                ) from exc
            return self._success(
                status,
                {"tool": name, "mutated": True},
                [],
            )
        if name == "test_alert_email":
            self._require_provider_manage()
            config = alert_email.load_config(db, self.workspace_id)
            try:
                alert_email.send_mail(
                    config,
                    "[LearnGraph] 预算告警测试邮件",
                    "这是一封来自 LearnGraph 用量预算模块的测试邮件；收到即表示 SMTP 配置可用。",
                )
            except AppError:
                raise
            except Exception as exc:  # noqa: BLE001 - report SMTP faults verbatim
                return self._success(
                    {"ok": False, "detail": str(exc)},
                    {"tool": name, "sent": False},
                    [],
                )
            return self._success(
                {"ok": True, "detail": "测试邮件已发送"},
                {"tool": name, "sent": True},
                [],
            )

        # --- Memory settings -----------------------------------------------------------
        if name in {"get_memory_policy", "update_memory_policy"}:
            if self.memory_tools is None:
                return self._failure(
                    "memory_tools_unavailable", "Memory tools are unavailable"
                )
            if name == "get_memory_policy":
                session_id = (
                    str(arguments["session_id"]).strip()
                    if isinstance(arguments.get("session_id"), str)
                    and arguments["session_id"].strip()
                    else None
                )
                view = self.memory_tools.policy(session_id=session_id)
            else:
                self._require_provider_manage()
                try:
                    payload = MemoryPolicyUpdateRequest.model_validate(arguments)
                except Exception as exc:
                    raise AppError(
                        422,
                        "invalid_tool_arguments",
                        "Memory policy arguments are invalid",
                        {"validation_error": str(exc)},
                    ) from exc
                view = self.memory_tools.update_policy(payload)
            return self._success(
                view.model_dump(mode="json"),
                {"tool": name, "mutated": name == "update_memory_policy"},
                [],
            )
        if name == "reindex_memory_embeddings":
            self._require_provider_manage()
            from app.services.memory_enhancement import reindex_memory_embeddings

            result = reindex_memory_embeddings(
                db, self.workspace_id, self.settings or get_settings()
            )
            return self._success(
                result,
                {"tool": name, "mutated": True},
                [],
            )

        # --- Plugins / local Skill probe / MCP refresh / Skill manifest ---------------
        if name == "list_plugins":
            plugins = PluginService(db, self.workspace_id, self.actor_id).list()
            items = [
                PluginView.model_validate(item).model_dump(mode="json")
                for item in plugins
            ]
            return self._success(
                {"plugins": items, "count": len(items)},
                {"tool": name, "count": len(items)},
                [],
            )
        if name == "toggle_plugin":
            self._require_provider_manage()
            plugin_id = str(arguments.get("plugin_id") or "").strip()
            enabled = arguments.get("enabled")
            if not plugin_id or not isinstance(enabled, bool):
                raise AppError(
                    422, "invalid_tool_arguments", "plugin_id and enabled are required"
                )
            plugin = PluginService(db, self.workspace_id, self.actor_id).toggle(
                plugin_id, PluginToggleRequest(enabled=enabled)
            )
            return self._success(
                PluginView.model_validate(plugin).model_dump(mode="json"),
                {"tool": name, "plugin_id": plugin_id, "mutated": True},
                [],
            )
        if name in {"get_local_probe_policy", "update_local_probe_policy"}:
            from app.domain.schemas.extensions import SkillLocalProbePolicyUpdate
            from app.services.skill_local_probe import SkillLocalProbeService

            probe_service = SkillLocalProbeService(
                db, self.workspace_id, self.actor_id, self.settings or get_settings()
            )
            if name == "get_local_probe_policy":
                view = probe_service.get_policy()
                return self._success(
                    view.model_dump(mode="json"),
                    {"tool": name},
                    [],
                )
            self._require_provider_manage()
            view = probe_service.update_policy(
                SkillLocalProbePolicyUpdate(
                    enabled=bool(arguments.get("enabled")),
                    allowed_roots=[
                        str(item)
                        for item in (arguments.get("allowed_roots") or [])
                        if isinstance(item, str) and item.strip()
                    ],
                )
            )
            return self._success(
                view.model_dump(mode="json"),
                {"tool": name, "mutated": True},
                [],
            )
        if name == "refresh_mcp_server":
            self._require_provider_manage()
            server_id = str(arguments.get("server_id") or "").strip()
            if not server_id:
                raise AppError(422, "invalid_tool_arguments", "server_id is required")
            snapshot = self.extensions.refresh_server(server_id)
            return self._success(
                {
                    "server_id": server_id,
                    "snapshot": snapshot.model_dump(mode="json"),
                },
                {"tool": name, "server_id": server_id, "mutated": True},
                [],
            )
        if name == "update_skill_manifest":
            self._require_provider_manage()
            from app.domain.schemas.extensions import SkillUpdateRequest, SkillView

            skill_id = str(arguments.get("skill_id") or "").strip()
            manifest = arguments.get("manifest")
            if not skill_id or not isinstance(manifest, dict):
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    "skill_id and manifest are required",
                )
            try:
                payload = SkillUpdateRequest(
                    name=(
                        str(arguments.get("name") or "").strip()
                        if arguments.get("name")
                        else None
                    ),
                    source=str(arguments.get("source") or "agent_manifest_update").strip(),
                    version=str(arguments.get("version") or "1.0.0").strip(),
                    manifest=manifest,
                )
            except Exception as exc:
                raise AppError(
                    422,
                    "invalid_tool_arguments",
                    f"Skill manifest failed validation: {exc}",
                ) from exc
            skill = self.extensions.update_skill(skill_id, payload)
            return self._success(
                SkillView.model_validate(skill).model_dump(mode="json"),
                {
                    "tool": name,
                    "skill_id": skill_id,
                    "mutated": True,
                    "reauthorization_required": True,
                },
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
                purpose="stored",
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
        sources: list[dict[str, str]] = []
        raw_sources = arguments.get("sources")
        if raw_sources is not None:
            if not isinstance(raw_sources, list) or len(raw_sources) > 8:
                raise AppError(
                    422,
                    "invalid_chart_sources",
                    "sources must be an array of at most 8 items",
                )
            for item in raw_sources:
                if not isinstance(item, dict):
                    raise AppError(422, "invalid_chart_sources", "sources items must be objects")
                source_title = str(item.get("title") or "").strip()
                source_url = str(item.get("url") or "").strip()
                if not source_title or not source_url:
                    raise AppError(
                        422,
                        "invalid_chart_sources",
                        "every source needs title and url",
                    )
                entry: dict[str, str] = {"title": source_title[:240], "url": source_url[:1000]}
                note = item.get("note")
                if isinstance(note, str) and note.strip():
                    entry["note"] = note.strip()[:500]
                sources.append(entry)
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
            "sources": sources,
            "data_verified": bool(sources),
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
            card_data = part.get("data") or {}
            self.audit.record(
                actor_id=self.actor_id,
                action="canvas.emit_magic_card",
                resource_type="chat_session",
                resource_id=chat_session_id,
                details={
                    "card_id": card_data.get("card_id"),
                    "card_instance_id": card_data.get("card_instance_id"),
                    "status": card_data.get("status"),
                },
            )
            self.extensions.db.commit()
            return self._success(
                {
                    "published": True,
                    "channel": "sandboxed_html_preview",
                    "runtime_available": card_data.get("status") == "ready",
                    "runtime": card_data.get("runtime"),
                    "part_type": "magic_card",
                    "card_instance_id": card_data.get("card_instance_id"),
                    "status": card_data.get("status"),
                    "reason": card_data.get("reason"),
                },
                {
                    "canvas": True,
                    "tool": name,
                    "artifact": part,
                },
                [],
            )
        if name == "artifact_publish_card":
            card_id = arguments.get("card_id")
            if not isinstance(card_id, str) or not card_id.strip():
                raise AppError(422, "invalid_tool_arguments", "card_id is required")
            release_notes = arguments.get("release_notes")
            if not isinstance(release_notes, str):
                release_notes = ""
            from app.domain.models import Workspace
            from app.services.artifact_cards import ArtifactCardService

            db = self.extensions.db
            workspace = db.get(Workspace, self.workspace_id)
            tenant_id = workspace.tenant_id if workspace is not None else "local-tenant"
            version = ArtifactCardService(
                db, self.workspace_id, tenant_id
            ).publish_version(
                card_id.strip(),
                release_notes=release_notes.strip(),
                actor_id=self.actor_id,
                publish_source="agent",
            )
            self.audit.record(
                actor_id=self.actor_id,
                action="canvas.publish_card",
                resource_type="artifact_card_version",
                resource_id=version.id,
                details={
                    "card_id": card_id.strip(),
                    "version": version.version,
                    "chat_session_id": chat_session_id,
                },
            )
            db.commit()
            return self._success(
                {
                    "published": True,
                    "card_id": card_id.strip(),
                    "version": version.version,
                    "status": "published",
                    "note": (
                        "The card draft is now frozen as this version in the "
                        "artifacts page. Further draft edits will not change it; "
                        "publish again to create a newer version."
                    ),
                },
                {
                    "canvas": True,
                    "tool": name,
                    "card_version": version.version,
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

    def _execute_external_acquisition(
        self,
        tool_name: str,
        tool_call: dict[str, Any],
        arguments: dict[str, Any],
        *,
        chat_session_id: str,
        assistant_message_id: str | None = None,
        source_message_id: str | None = None,
        _retried: bool = False,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        import logging

        from app.domain.models import (
            EXTERNAL_ACQUISITION_CAPABILITY,
            EgressAuthorizationRequest,
            HostAuthorizationGrant,
        )
        from app.services.egress_approvals import EgressApprovalService

        _logger = logging.getLogger(__name__)
        from app.services.external_acquisition import (
            AcquisitionApprovalRequired,
            ExternalAcquisitionService,
        )

        settings = self.settings or get_settings()
        acquisition = ExternalAcquisitionService(
            self.extensions.db,
            self.workspace_id,
            self.actor_id,
            settings,
        )
        if tool_name == "download_external_image":
            unknown = set(arguments) - {"url", "urls", "destination_path", "destination_dir", "expected_sha256"}
            url = arguments.get("url")
            urls = arguments.get("urls")
            destination = arguments.get("destination_path")
            destination_dir = arguments.get("destination_dir")
            expected_sha = arguments.get("expected_sha256")
            has_url = isinstance(url, str) and bool(url.strip())
            has_urls = (
                isinstance(urls, list)
                and len(urls) >= 2
                and all(isinstance(item, str) and item.strip() for item in urls)
            )
            sha_valid = expected_sha is None or (
                isinstance(expected_sha, str)
                and len(expected_sha) == 64
                and all(char in "0123456789abcdefABCDEF" for char in expected_sha)
            )
            if unknown or has_url == has_urls or not sha_valid:
                raise AppError(422, "invalid_tool_arguments", "download_external_image arguments are invalid")
            kind = "image"
            label = "可信图片下载工具"
            if has_url:
                if not isinstance(destination, str) or not destination.strip():
                    raise AppError(422, "invalid_tool_arguments", "download_external_image requires destination_path for a single url")
                normalized = {
                    "url": url.strip(),
                    "destination_path": destination.strip(),
                }
                if expected_sha:
                    normalized["expected_sha256"] = expected_sha.casefold()
                purpose = f"下载并验证图片 {url.strip()}"
            else:
                normalized = {
                    "urls": sorted({item.strip() for item in urls}),
                    "destination_dir": (
                        destination_dir.strip()
                        if isinstance(destination_dir, str) and destination_dir.strip()
                        else "inputs/images"
                    ),
                }
                purpose = f"并行下载 {len(normalized['urls'])} 张图片"
        else:
            unknown = set(arguments) - {"owner", "repo", "ref", "path", "destination_root"}
            owner = arguments.get("owner")
            repo = arguments.get("repo")
            destination = arguments.get("destination_root")
            ref = arguments.get("ref", "HEAD")
            source_path = arguments.get("path", "")
            if (
                unknown
                or not isinstance(owner, str)
                or not owner.strip()
                or not isinstance(repo, str)
                or not repo.strip()
                or not isinstance(destination, str)
                or not destination.strip()
                or not isinstance(ref, str)
                or not isinstance(source_path, str)
            ):
                raise AppError(422, "invalid_tool_arguments", "download_github_source arguments are invalid")
            normalized = {
                "owner": owner.strip(),
                "repo": repo.strip(),
                "ref": ref.strip() or "HEAD",
                "path": source_path.strip(),
                "destination_root": destination.strip(),
            }
            kind = "github_snapshot"
            label = "GitHub 源码下载工具"
            purpose = f"下载 GitHub 源码 {owner.strip()}/{repo.strip()}@{ref.strip() or 'HEAD'}"

        _spec, spec_sha = acquisition.canonical_spec(kind, normalized)
        approval_service = EgressApprovalService(
            self.extensions.db,
            self.workspace_id,
            settings,
            capability=EXTERNAL_ACQUISITION_CAPABILITY,
        )
        allowed_hosts = set(approval_service.effective_allowed_hosts(actor_id=self.actor_id))
        related_requests = self.extensions.db.scalars(
            select(EgressAuthorizationRequest).where(
                EgressAuthorizationRequest.workspace_id == self.workspace_id,
                EgressAuthorizationRequest.capability == EXTERNAL_ACQUISITION_CAPABILITY,
                EgressAuthorizationRequest.requested_by == self.actor_id,
                EgressAuthorizationRequest.chat_session_id == chat_session_id,
                EgressAuthorizationRequest.status.in_(["approved", "consumed"]),
                EgressAuthorizationRequest.expires_at > datetime.now(timezone.utc),
            )
        ).all()
        matching_once: list[EgressAuthorizationRequest] = []
        approval_by_host: dict[str, str] = {}
        grants = self.extensions.db.scalars(
            select(HostAuthorizationGrant).where(
                HostAuthorizationGrant.workspace_id == self.workspace_id,
                HostAuthorizationGrant.capability == EXTERNAL_ACQUISITION_CAPABILITY,
                HostAuthorizationGrant.revoked_at.is_(None),
            )
        ).all()
        for grant in grants:
            if grant.hostname in allowed_hosts and grant.source_request_id:
                approval_by_host[grant.hostname] = grant.source_request_id
        for request in related_requests:
            context = request.request_context if isinstance(request.request_context, dict) else {}
            if context.get("request_spec_sha256") != spec_sha:
                continue
            approval_by_host[request.hostname] = request.id
            if request.status == "approved" and request.decision == "allow_once":
                matching_once.append(request)
                allowed_hosts.add(request.hostname)

        claimed: list[EgressAuthorizationRequest] = []

        def release_claimed() -> None:
            for request in claimed:
                try:
                    approval_service.release_once(request_id=request.id)
                except Exception:
                    _logger.exception(
                        "failed to release acquisition approval %s",
                        request.id,
                    )

        try:
            # Atomically claim every matching single-use lease BEFORE any network
            # activity. A concurrent caller can only claim each lease once.
            for request in list(matching_once):
                claim = approval_service.claim_once(
                    request_id=request.id,
                    actor_id=self.actor_id,
                )
                if claim is None:
                    release_claimed()
                    if _retried:
                        # Already retried: drop this host and continue without it.
                        allowed_hosts.discard(request.hostname)
                        matching_once.remove(request)
                    else:
                        return self._execute_external_acquisition(
                            tool_name,
                            tool_call,
                            arguments,
                            chat_session_id=chat_session_id,
                            assistant_message_id=assistant_message_id,
                            source_message_id=source_message_id,
                            _retried=True,
                        )
                else:
                    claimed.append(claim)
            if tool_name == "download_external_image":
                if "urls" in normalized:
                    result = acquisition.download_images(
                        chat_session_id=chat_session_id,
                        allowed_hosts=allowed_hosts,
                        request_spec_sha256=spec_sha,
                        approval_by_host=approval_by_host,
                        **normalized,
                    )
                else:
                    result = acquisition.download_image(
                        chat_session_id=chat_session_id,
                        allowed_hosts=allowed_hosts,
                        request_spec_sha256=spec_sha,
                        approval_by_host=approval_by_host,
                        **normalized,
                    )
            else:
                result = acquisition.download_github_source(
                    chat_session_id=chat_session_id,
                    allowed_hosts=allowed_hosts,
                    request_spec_sha256=spec_sha,
                    approval_by_host=approval_by_host,
                    **normalized,
                )
        except AcquisitionApprovalRequired as exc:
            release_claimed()
            request = approval_service.create_request(
                hostname=exc.hostname,
                requested_by=self.actor_id,
                chat_session_id=chat_session_id,
                purpose=purpose,
                request_context={
                    "tool_name": tool_name,
                    "tool_label": label,
                    "origin": "external_acquisition",
                    "request_spec_sha256": spec_sha,
                    "resource_summary": purpose,
                    "destination_path": normalized.get("destination_path")
                    or normalized.get("destination_root"),
                },
                dedupe_key=f"acquire:{spec_sha[:32]}:{exc.hostname}"[:80],
                tool_call_id=str(tool_call.get("id") or "") or None,
                assistant_message_id=assistant_message_id,
                user_message_id=source_message_id,
            )
            if request.status == "approved":
                # Unified allowlist can approve synchronously; retry once with the
                # newly materialized persistent grant instead of showing a card.
                return self._execute_external_acquisition(
                    tool_name,
                    tool_call,
                    arguments,
                    chat_session_id=chat_session_id,
                    assistant_message_id=assistant_message_id,
                    source_message_id=source_message_id,
                    _retried=True,
                )
            return self._failure(
                "egress_authorization_required",
                "外部资源下载需要用户授权",
                data={
                    "authorization_request_id": request.id,
                    "tool_call_id": str(tool_call.get("id") or ""),
                    "tool_name": tool_name,
                    "tool_label": label,
                    "hostname": request.hostname,
                    "requested_url": normalized.get("url"),
                    "request_spec_sha256": spec_sha,
                    "resource_summary": purpose,
                    "destination_path": normalized.get("destination_path")
                    or normalized.get("destination_root"),
                    "message_zh": f"{purpose}，需要访问主机 {request.hostname}，是否批准？",
                },
            )

        except Exception:
            release_claimed()
            raise
        for request in claimed:
            approval_service.consume_once(request_id=request.id)
        if tool_name == "download_external_image" and "urls" in normalized:
            # Batch parallel download: the result text lists every image, so no
            # single-file artifact card is emitted.
            meta: dict[str, Any] = {
                "tool": tool_name,
                "request_spec_sha256": spec_sha,
                "downloaded_count": len(result.get("downloaded", [])),
                "failed_count": len(result.get("failed", [])),
            }
        else:
            meta = {
                "tool": tool_name,
                "request_spec_sha256": spec_sha,
                "file_id": result.get("file_id"),
                "path": result.get("path") or result.get("destination_root"),
                "size_bytes": result.get("size_bytes") or result.get("total_bytes"),
                "sha256": result.get("blob_sha256") or result.get("manifest_sha256"),
                "mime_type": result.get("mime_type"),
                "title": "已下载的图片" if tool_name == "download_external_image" else "GitHub 源码快照",
            }
        return self._success(result, meta, [])

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
        provider = self.image_search_provider
        if provider is None or not callable(getattr(provider, "image_search", None)):
            # Legacy fallback: the search lane itself may be the Qwen Responses
            # companion exposing image_search.
            provider = self.search_provider
        if (
            unknown
            or not isinstance(query, str)
            or not 1 <= len(query.strip()) <= 500
            or (image_url is not None and not isinstance(image_url, str))
            or provider is None
            or not callable(getattr(provider, "image_search", None))
        ):
            raise AppError(
                422,
                "invalid_tool_arguments",
                "search_images requires a query, an optional public image URL, "
                "and a configured 文搜图/图搜图 provider",
            )
        try:
            if isinstance(image_url, str):
                domains = {
                    item.strip().casefold()
                    for item in allowed_domains
                    if isinstance(item, str) and item.strip()
                }
                require_public_http_url(image_url.strip(), domains)
        except UnsafeFetchURL as exc:
            raise AppError(
                422,
                "image_search_url_blocked",
                "The reverse-image URL is not a safe public URL",
            ) from exc
        timeout_seconds = getattr(provider, "timeout_seconds", None)
        images: list[dict[str, str]] = []
        last_timeout: SearchProviderTimeout | None = None
        for _attempt in range(1 + IMAGE_SEARCH_TIMEOUT_RETRIES):
            try:
                images = provider.image_search(
                    query.strip(),
                    image_url=image_url.strip() if isinstance(image_url, str) else None,
                )
                break
            except SearchProviderTimeout as exc:
                # Transient upstream slowness; retry before failing.
                last_timeout = exc
            except SearchProviderError as exc:
                raise AppError(
                    502,
                    "search_provider_failed",
                    f"Image search failed: {exc}",
                ) from exc
        else:
            budget = f" after {timeout_seconds}s" if timeout_seconds else ""
            raise AppError(
                504,
                "search_provider_timeout",
                f"Image search timed out{budget} across "
                f"{1 + IMAGE_SEARCH_TIMEOUT_RETRIES} attempts; the provider is slow "
                "(not an argument error). Retry with a shorter single-language "
                "query or try again later",
            ) from last_timeout
        result = {"query": query.strip(), "images": images}
        return self._success(
            result,
            {
                "result_count": len(images),
                "provider_id": provider.provider_id,
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

        # Merge FileReference + ImageGenerationTask + session_workspace_entries
        # so download_external_image results (which never create a
        # FileReference) are visible exactly like sandbox_list_files sees them.
        from app.services.session_files import collect_session_files

        listed = collect_session_files(
            db,
            workspace_id=self.workspace_id,
            session_id=target_session_id,
        )
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
        size_raw = arguments.get("size")
        size = (
            size_raw.strip().casefold()
            if isinstance(size_raw, str) and size_raw.strip()
            else "auto"
        )
        allowed_sizes = {
            "auto",
            "2048x2048",
            "2048x1152",
            "1152x2048",
            "1536x1152",
            "1152x1536",
        }
        if size not in allowed_sizes:
            raise AppError(422, "invalid_tool_arguments", "size is not supported")
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

        if source_file_ids and not getattr(
            image_provider, "supports_image_edit", False
        ):
            return self._failure(
                "image_edit_model_unsupported",
                "Image editing requires a supported image-edit model (gpt-image-2 or qwen-image-edit-max). The active text LLM may still be DeepSeek; select an image-edit model for this image tool call.",
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
                    size=size,
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
                    "image_size": size,
                    "source_file_ids": source_file_ids,
                },
            )
            image_width: int | None = None
            image_height: int | None = None
            try:
                with Image.open(BytesIO(final_event.image_bytes)) as image:
                    image_width, image_height = image.size
            except Exception:
                # Dimensions are presentation-only; never fail the tool over them.
                image_width = None
                image_height = None
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

        part_data: dict[str, Any] = {
            "generation_id": task.id,
            "provider_id": image_provider.provider_id,
            "model_id": image_provider.model_id,
            "file_id": file.id,
            "mime_type": file.mime_type,
            "title": title,
            "alt": prompt[:240],
            "prompt": prompt,
            "image_size": size,
            "source_file_ids": source_file_ids,
            "progress_mode": "completed",
            "preview_revision": 1,
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        }
        if image_width and image_height:
            part_data["width"] = int(image_width)
            part_data["height"] = int(image_height)
            part_data["aspect_ratio"] = f"{int(image_width)} / {int(image_height)}"
        part = {
            "type": "image",
            "status": "completed",
            "content": title,
            "data": part_data,
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
        if code == "fetch_domain_authorization_required":
            meta["fetch_authorization_required"] = {
                "authorization_request_id": details.get("authorization_request_id"),
                "tool_call_id": details.get("tool_call_id"),
                "tool_name": details.get("tool_name") or "fetch_web_page",
                "tool_label": details.get("tool_label") or "网页抓取工具",
                "requested_url": details.get("requested_url"),
                "hostname": details.get("hostname"),
                "message_zh": details.get("message_zh") or "网页抓取需要用户授权。",
            }
        # Surface the D2.1 generic egress approval card (pending suspension).
        if code == "egress_authorization_required":
            meta["egress_authorization_required"] = {
                "authorization_request_id": details.get("authorization_request_id"),
                "tool_call_id": details.get("tool_call_id"),
                "tool_name": details.get("tool_name") or "sandbox_exec",
                "tool_label": details.get("tool_label") or "沙箱命令工具",
                "hostname": details.get("hostname"),
                "requested_url": details.get("requested_url"),
                "request_spec_sha256": details.get("request_spec_sha256"),
                "resource_summary": details.get("resource_summary"),
                "destination_path": details.get("destination_path"),
                "message_zh": details.get("message_zh")
                or "沙箱出站访问需要用户授权。",
            }
        # Surface structured sandbox authorization challenges to the Chat SSE
        # assembler so the client can open an explicit grant dialog.
        if code == "sandbox_auth_required":
            meta["sandbox_auth_required"] = {
                "action": details.get("action") or "delete_path",
                "paths": details.get("paths") or [],
                "chat_session_id": details.get("chat_session_id"),
                "sandbox_session_id": details.get("sandbox_session_id"),
                "command_intent_digest": details.get("command_intent_digest"),
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
