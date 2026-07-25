from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit

from jsonschema import ValidationError
from jsonschema.validators import validator_for
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import Principal, SecretCipher, mask_secret
from app.domain.extension_models import (
    ExtensionInvocation,
    ExtensionPermissionGrant,
    MCPCapabilitySnapshot,
    MCPServer,
    MCPServerCredential,
    SkillPackageFile,
    SkillRecord,
    SkillTranslationCache,
)
from app.domain.models import (
    AnswerRecord,
    Exercise,
    Graph,
    GraphEdge,
    GraphNode,
    MasterySchedule,
    Message,
    FileRecord,
    Workspace,
    utc_now,
)
from app.domain.schemas.extensions import (
    MCPInvokeRequest,
    MCPServerCreateRequest,
    MCPServerUpdateRequest,
    PermissionDecisionRequest,
    SkillCreateRequest,
    SkillInvokeRequest,
    SkillManifest,
    SkillUpdateRequest,
)
from app.domain.schemas.learning import EvidenceCreateRequest
from app.domain.schemas.workflow import ActionCreate, ActionUpdate
from app.providers.local.mcp import UnavailableStdioMCPAdapter
from app.providers.ports.mcp import (
    MCPProbeResult,
    MCPProtocolFailure,
    MCPResponseTooLarge,
    MCPTransportFailure,
    MCPTransportPort,
    MCPTransportTimeout,
    MCPTransportUnavailable,
)
from app.providers.remote.mcp_http import PROTOCOL_VERSION, StreamableHTTPMCPAdapter
from app.repositories.audit import AuditRepository
from app.repositories.extensions import (
    ExtensionInvocationRepository,
    ExtensionPermissionGrantRepository,
    MCPCapabilitySnapshotRepository,
    MCPServerCredentialRepository,
    MCPServerRepository,
    SkillRepository,
)
from app.services.authorization import AuthorizationService
from app.services.billing import BillingService
from app.services.learning import EvidenceService
from app.services.management import UsageService
from app.services.workflow import WorkflowService


TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "authorization",
    "bearer_token",
    "password",
    "refresh_token",
    "secret",
    "token",
}
BUILTIN_TOOL_PERMISSIONS: dict[str, list[str]] = {
    "builtin.review.list_due": ["mastery.read"],
    "builtin.graph.read": ["graph.read"],
    "builtin.graph.update_candidate_node": ["graph.write"],
    "builtin.roadmap.read": ["roadmap.read"],
    "builtin.roadmap.replan": ["roadmap.write"],
    "builtin.action.list": ["roadmap.read"],
    "builtin.action.create": ["roadmap.write"],
    "builtin.action.update": ["roadmap.write"],
    "builtin.learning.mastery.read": ["learning.read"],
    "builtin.learning.evidence.record": ["learning.write"],
    "builtin.usage.summary": ["usage.read"],
    "builtin.usage.budget.create": ["usage.write"],
    "builtin.usage.budget.update": ["usage.write"],
}

# The same bounded definitions back both declarative Skills and Agent function
# calls.  They intentionally expose domain operations rather than database or
# HTTP implementation details, so a tool cannot escape its workspace scope.
BUILTIN_TOOL_SPECS: dict[str, dict[str, Any]] = {
    "builtin.review.list_due": {
        "function_name": "lg_review_list_due",
        "description": "Read the currently due LearnGraph review nodes.",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
            "additionalProperties": False,
        },
    },
    "builtin.graph.read": {
        "function_name": "lg_graph_read",
        "description": "Read a permitted target graph and optionally retrieve matching nodes by label or description.",
        "parameters": {
            "type": "object",
            "properties": {
                "graph_id": {"type": "string", "minLength": 1, "maxLength": 36},
                "query": {"type": "string", "maxLength": 500},
                "limit": {"type": "integer", "minimum": 1, "maximum": 80},
                "edge_limit": {"type": "integer", "minimum": 1, "maximum": 240},
            },
            "required": ["graph_id"],
            "additionalProperties": False,
        },
    },
    "builtin.graph.update_candidate_node": {
        "function_name": "lg_graph_update_candidate_node",
        "description": "Update one node in a candidate graph revision. Published graphs are immutable and must go through a reviewed proposal instead.",
        "parameters": {
            "type": "object",
            "properties": {
                "graph_id": {"type": "string", "minLength": 1, "maxLength": 36},
                "node_id": {"type": "string", "minLength": 1, "maxLength": 36},
                "expected_revision": {"type": "integer", "minimum": 1},
                "label": {"type": "string", "minLength": 1, "maxLength": 200},
                "description": {"type": "string", "maxLength": 4000},
                "target_weight": {"type": "integer", "minimum": 1, "maximum": 100},
                "attention_state": {"type": "string", "maxLength": 40},
                "external_concept_id": {"type": "string", "maxLength": 255},
            },
            "required": ["graph_id", "node_id", "expected_revision"],
            "additionalProperties": False,
        },
    },
    "builtin.roadmap.read": {
        "function_name": "lg_roadmap_read",
        "description": "Read a permitted roadmap by roadmap ID or the latest roadmap for a Goal.",
        "parameters": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string", "minLength": 1, "maxLength": 36},
                "roadmap_id": {"type": "string", "minLength": 1, "maxLength": 36},
            },
            "oneOf": [{"required": ["goal_id"]}, {"required": ["roadmap_id"]}],
            "additionalProperties": False,
        },
    },
    "builtin.roadmap.replan": {
        "function_name": "lg_roadmap_replan",
        "description": "Create a new reviewable roadmap draft from a Goal's current graph and verified learning facts. It never publishes the roadmap.",
        "parameters": {
            "type": "object",
            "properties": {"goal_id": {"type": "string", "minLength": 1, "maxLength": 36}},
            "required": ["goal_id"],
            "additionalProperties": False,
        },
    },
    "builtin.action.list": {
        "function_name": "lg_schedule_list",
        "description": "Read permitted scheduled and unscheduled learning actions for calendar planning.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "maxLength": 40},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    "builtin.action.create": {
        "function_name": "lg_schedule_create",
        "description": "Create a user-owned scheduled learning action; it never alters a published roadmap action.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 240},
                "description": {"type": "string", "maxLength": 4000},
                "action_type": {"type": "string", "maxLength": 48},
                "project_id": {"type": "string", "maxLength": 36},
                "goal_id": {"type": "string", "maxLength": 36},
                "graph_id": {"type": "string", "maxLength": 36},
                "node_id": {"type": "string", "maxLength": 36},
                "priority": {"type": "integer", "minimum": 0, "maximum": 100},
                "due_at": {"type": "string", "format": "date-time"},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
    },
    "builtin.action.update": {
        "function_name": "lg_schedule_update",
        "description": "Update a permitted learning action or its due time. Published-roadmap actions only allow safe forward progress transitions.",
        "parameters": {
            "type": "object",
            "properties": {
                "action_id": {"type": "string", "minLength": 1, "maxLength": 36},
                "title": {"type": "string", "minLength": 1, "maxLength": 240},
                "description": {"type": "string", "maxLength": 4000},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "archived"]},
                "priority": {"type": "integer", "minimum": 0, "maximum": 100},
                "position": {"type": "integer", "minimum": 0},
                "due_at": {"type": "string", "format": "date-time"},
            },
            "required": ["action_id"],
            "additionalProperties": False,
        },
    },
    "builtin.learning.mastery.read": {
        "function_name": "lg_learning_mastery_read",
        "description": "Read evidence-backed mastery and review state. Browsing or imported files alone are never treated as mastery.",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "maxLength": 36},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
    },
    "builtin.learning.evidence.record": {
        "function_name": "lg_learning_evidence_record",
        "description": "Record traceable user learning evidence as pending review, linked to an existing file, message, or correct exercise answer. This cannot directly grant mastery or accept evidence.",
        "parameters": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "minLength": 1, "maxLength": 36},
                "source_type": {"type": "string", "enum": ["conversation", "exercise", "file"]},
                "source_id": {"type": "string", "minLength": 1, "maxLength": 36},
                "summary": {"type": "string", "minLength": 1, "maxLength": 4000},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "locator": {"type": "string", "maxLength": 255},
            },
            "required": ["node_id", "source_type", "source_id", "summary"],
            "additionalProperties": False,
        },
    },
    "builtin.usage.summary": {
        "function_name": "lg_usage_summary",
        "description": "Read persisted token and cost usage, keeping USD and CNY values distinct.",
        "parameters": {
            "type": "object",
            "properties": {
                "provider_id": {"type": "string", "maxLength": 80},
                "model_id": {"type": "string", "maxLength": 160},
                "feature": {"type": "string", "maxLength": 80},
            },
            "additionalProperties": False,
        },
    },
    "builtin.usage.budget.create": {
        "function_name": "lg_usage_budget_create",
        "description": "Create a workspace token-cost budget policy; usage history itself remains immutable.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 160},
                "provider_id": {"type": "string", "minLength": 1, "maxLength": 80},
                "model_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "feature": {"type": "string", "minLength": 1, "maxLength": 80},
                "period": {
                    "type": "string",
                    "enum": ["calendar_day_utc", "calendar_month_utc"],
                },
                "soft_limit_cny": {"type": "number", "minimum": 0},
                "hard_limit_cny": {"type": "number", "minimum": 0},
                "enabled": {"type": "boolean"},
            },
            "required": ["name", "provider_id", "model_id", "feature", "period"],
            "anyOf": [
                {"required": ["soft_limit_cny"]},
                {"required": ["hard_limit_cny"]},
            ],
            "additionalProperties": False,
        },
    },
    "builtin.usage.budget.update": {
        "function_name": "lg_usage_budget_update",
        "description": "Update an existing workspace token-cost budget policy; it cannot rewrite usage history.",
        "parameters": {
            "type": "object",
            "properties": {
                "policy_id": {"type": "string", "minLength": 1, "maxLength": 36},
                "name": {"type": "string", "minLength": 1, "maxLength": 160},
                "soft_limit_cny": {"type": ["number", "null"], "minimum": 0},
                "hard_limit_cny": {"type": ["number", "null"], "minimum": 0},
                "enabled": {"type": "boolean"},
            },
            "required": ["policy_id", "name", "enabled"],
            "additionalProperties": False,
        },
    },
}
SKILL_MAX_INPUT_BYTES = 64 * 1024
SKILL_MAX_RESULT_BYTES = 256 * 1024


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).casefold() in SENSITIVE_KEYS or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _redact_sensitive_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if str(key).casefold() in SENSITIVE_KEYS
                else _redact_sensitive_keys(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_keys(item) for item in value]
    return value


def _reject_remote_schema_references(value: Any) -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and not reference.startswith("#"):
            raise AppError(
                422,
                "remote_schema_reference_forbidden",
                "Extension JSON Schemas may use only local fragment references",
            )
        for item in value.values():
            _reject_remote_schema_references(item)
    elif isinstance(value, list):
        for item in value:
            _reject_remote_schema_references(item)


def _validate_schema(schema: dict[str, Any], label: str) -> None:
    if len(_canonical_bytes(schema)) > 32 * 1024:
        raise AppError(422, "extension_schema_too_large", f"{label} exceeds 32 KiB")
    _reject_remote_schema_references(schema)
    try:
        validator_for(schema).check_schema(schema)
    except Exception as exc:
        raise AppError(
            422,
            "invalid_extension_schema",
            f"{label} is not a valid supported JSON Schema",
        ) from exc


def _validate_instance(schema: dict[str, Any], value: Any, label: str) -> None:
    try:
        validator_for(schema)(schema).validate(value)
    except ValidationError as exc:
        raise AppError(
            422,
            "extension_input_schema_mismatch",
            f"{label} does not match the declared JSON Schema",
            {"path": [str(item) for item in exc.absolute_path]},
        ) from exc


class MCPAndSkillService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings,
        *,
        workspace: Workspace | None = None,
        principal: Principal | None = None,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.workspace = workspace
        self.principal = principal
        self.servers = MCPServerRepository(db, workspace_id)
        self.snapshots = MCPCapabilitySnapshotRepository(db, workspace_id)
        self.credentials = MCPServerCredentialRepository(db, workspace_id)
        self.skills = SkillRepository(db, workspace_id)
        self.grants = ExtensionPermissionGrantRepository(db, workspace_id)
        self.invocations = ExtensionInvocationRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)

    def _runtime_context(self) -> tuple[Workspace, Principal, AuthorizationService]:
        """Return the authenticated scope needed by first-party write tools.

        Old direct service construction is still valid for the original
        review-only Skill, but domain tools must never silently run without the
        authenticated Workspace/Principal pair that the HTTP boundary supplied.
        """

        if self.workspace is None or self.principal is None:
            raise AppError(
                503,
                "builtin_tool_context_unavailable",
                "This built-in tool requires an authenticated workspace context",
            )
        if self.workspace.id != self.workspace_id:
            raise AppError(
                403,
                "builtin_tool_workspace_mismatch",
                "The built-in tool workspace does not match the current scope",
            )
        return self.workspace, self.principal, AuthorizationService(self.db, self.principal)

    @staticmethod
    def builtin_tool_catalog() -> list[dict[str, Any]]:
        """Expose first-party tool metadata for the UI and Agent registry."""

        return [
            {
                "tool": tool,
                "function_name": spec["function_name"],
                "description": spec["description"],
                "parameters": spec["parameters"],
                "permissions": BUILTIN_TOOL_PERMISSIONS[tool],
            }
            for tool, spec in BUILTIN_TOOL_SPECS.items()
        ]

    @staticmethod
    def _is_agent_skill_package(skill: SkillRecord) -> bool:
        """Return whether the skill is a D-077 Agent Skills file package."""

        return skill.kind == "agent_skill_package" or skill.package_format == "skill_md_v1"

    @staticmethod
    def _is_declarative_skill(skill: SkillRecord) -> bool:
        """Return whether the skill is a declarative review/workflow tool skill."""

        if MCPAndSkillService._is_agent_skill_package(skill):
            return False
        kind = (skill.kind or "").strip()
        return kind in {"declarative_review", "declarative_workflow", ""} or (
            skill.package_format or "declarative_json"
        ) == "declarative_json"

    def agent_tool_definitions(self) -> list[dict[str, Any]]:
        """Return hot-pluggable first-party, declarative Skill, and MCP tools.

        A Skill/MCP server reaches an Agent only after its current permission
        grant is an explicit, durable ``always`` decision.  Invocation still
        validates the live snapshot, input schema, and result limits.

        Agent Skill file packages (``agent_skill_package`` / D-077) are
        intentionally omitted here: they inject instructions into the Agent
        prompt instead of registering ``scripts/`` or package metadata as
        callable tools.  See :meth:`agent_skill_package_instructions`.
        """

        definitions = [
            {
                "type": "function",
                "function": {
                    "name": spec["function_name"],
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                },
            }
            for spec in BUILTIN_TOOL_SPECS.values()
        ]
        for skill in self.list_skills():
            if not skill.enabled or skill.status != "enabled":
                continue
            if not self._is_declarative_skill(skill):
                # File packages never become function tools (D-077).
                continue
            grant = self._usable_grant(
                "skill", skill.id, self._skill_authorization_hash(skill)
            )
            if grant is None or grant.decision != "always":
                continue
            try:
                manifest = SkillManifest.model_validate(skill.manifest_json)
            except Exception as exc:
                # A corrupt declarative row must not crash every Agent turn.
                # Surface the failure in audit and skip the broken skill.
                self.audit.record(
                    actor_id=self.actor_id,
                    action="skill.agent_registry_skipped",
                    resource_type="skill",
                    resource_id=skill.id,
                    outcome="failed",
                    details={
                        "reason": "invalid_declarative_manifest",
                        "error_type": type(exc).__name__,
                        "skill_key": skill.skill_key,
                    },
                )
                continue
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": self._agent_skill_function_name(skill.id),
                        "description": (
                            f"Run enabled LearnGraph Skill: {skill.name}. "
                            f"{manifest.instructions_markdown[:800]}"
                        ),
                        "parameters": manifest.input_schema,
                    },
                }
            )
        for server in self.list_servers():
            if not server.enabled or not server.agent_auto_invoke:
                continue
            snapshot = self._current_snapshot(server)
            if snapshot is None:
                continue
            grant = self._usable_grant(
                "mcp_server", server.id, self._server_authorization_hash(server, snapshot)
            )
            if grant is None or grant.decision != "always":
                continue
            for tool in snapshot.tools:
                name = str(tool.get("name") or "")
                if name not in server.requested_tools:
                    continue
                input_schema = tool.get("inputSchema")
                if not isinstance(input_schema, dict):
                    continue
                definitions.append(
                    {
                        "type": "function",
                        "function": {
                            "name": self._agent_mcp_function_name(server.id, name),
                            "description": (
                                f"Run enabled MCP tool {name} from {server.display_name}. "
                                f"{str(tool.get('description') or '')[:1500]}"
                            ),
                            "parameters": input_schema,
                        },
                    }
                )
        return definitions

    def agent_skill_package_instructions(
        self,
        *,
        activated_skill_keys: set[str] | None = None,
    ) -> str:
        """Return authorized Agent Skill package instructions for prompt injection.

        D-077: file packages contribute their SKILL.md instructions only.  They
        never register ``scripts/`` as tools.  Only enabled skills with a durable
        ``always`` grant are included.  Each body is capped so a large package
        cannot monopolize the model context.
        """

        sections: list[str] = []
        activated = activated_skill_keys or set()
        skills = sorted(
            self.list_skills(),
            key=lambda skill: (skill.skill_key not in activated, skill.created_at, skill.id),
        )
        for skill in skills:
            if not skill.enabled or skill.status != "enabled":
                continue
            if not self._is_agent_skill_package(skill):
                continue
            # Contextual system skills are installed durably but injected only
            # when the user explicitly activates the matching composer mode.
            if (
                skill.source == "learngraph_system"
                and skill.skill_key == "goal-learning-route"
                and skill.skill_key not in activated
            ):
                continue
            grant = self._usable_grant(
                "skill", skill.id, self._skill_authorization_hash(skill)
            )
            if grant is None or grant.decision != "always":
                continue
            instructions = (skill.instructions_markdown or "").strip()
            if not instructions:
                # Fall back to the package description when the body is empty.
                description = ""
                if isinstance(skill.manifest_json, dict):
                    raw = skill.manifest_json.get("description")
                    if isinstance(raw, str):
                        description = raw.strip()
                instructions = description
            if not instructions:
                continue
            # Bound each package so many authorized skills stay affordable.
            body = instructions[:8_000]
            if len(instructions) > 8_000:
                body = f"{body}\n…(truncated)"
            sections.append(
                f"### Skill: {skill.name} (`{skill.skill_key}`)\n"
                f"skill_id: {skill.id}\n"
                f"source: {skill.source}\n"
                f"version: {skill.version}\n\n"
                f"{body}"
            )
            if len(sections) >= 8:
                break
        if not sections:
            return ""
        return (
            "Authorized LearnGraph Agent Skill packages for this turn. "
            "Treat each block as optional skill instructions when the user's "
            "request matches its scope. Follow the instructions; do not invent "
            "host-side scripts or claim you executed package scripts unless a "
            "sandbox tool result confirms it. Package scripts are never automatic "
            "tools.\n\n"
            + "\n\n".join(sections)
        )

    def invoke_agent_function(
        self,
        function_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch a function exposed by :meth:`agent_tool_definitions`."""

        for tool_name, spec in BUILTIN_TOOL_SPECS.items():
            if spec["function_name"] == function_name:
                return self._invocation_data(
                    self.invoke_builtin_tool(tool_name, arguments)
                )
        for skill in self.list_skills():
            if self._agent_skill_function_name(skill.id) == function_name:
                if not self._is_declarative_skill(skill):
                    raise AppError(
                        403,
                        "agent_tool_not_authorized",
                        "Agent Skill packages are instruction-only and cannot be invoked as tools",
                    )
                grant = self._usable_grant(
                    "skill", skill.id, self._skill_authorization_hash(skill)
                )
                if (
                    not skill.enabled
                    or skill.status != "enabled"
                    or grant is None
                    or grant.decision != "always"
                ):
                    raise AppError(
                        403,
                        "agent_tool_not_authorized",
                        "The requested Agent Skill is not enabled with durable authorization",
                    )
                return self._invocation_data(self.invoke_skill(skill.id, SkillInvokeRequest(input=arguments)))
        for server in self.list_servers():
            snapshot = self._current_snapshot(server)
            if snapshot is None:
                continue
            for tool in snapshot.tools:
                tool_name = str(tool.get("name") or "")
                if self._agent_mcp_function_name(server.id, tool_name) == function_name:
                    grant = self._usable_grant(
                        "mcp_server",
                        server.id,
                        self._server_authorization_hash(server, snapshot),
                    )
                    if (
                        not server.enabled
                        or not server.agent_auto_invoke
                        or tool_name not in server.requested_tools
                        or grant is None
                        or grant.decision != "always"
                    ):
                        raise AppError(
                            403,
                            "agent_tool_not_authorized",
                            "The requested Agent MCP tool is not enabled with durable authorization",
                        )
                    return self._invocation_data(
                        self.invoke_mcp(
                            server.id,
                            MCPInvokeRequest(tool_name=tool_name, arguments=arguments),
                        )
                    )
        raise AppError(403, "agent_tool_not_authorized", "The requested Agent tool is not enabled")

    @staticmethod
    def _agent_skill_function_name(skill_id: str) -> str:
        return f"lg_skill_{hashlib.sha256(skill_id.encode('utf-8')).hexdigest()[:20]}"

    @staticmethod
    def _agent_mcp_function_name(server_id: str, tool_name: str) -> str:
        digest = hashlib.sha256(f"{server_id}:{tool_name}".encode("utf-8")).hexdigest()
        return f"lg_mcp_{digest[:20]}"

    @staticmethod
    def _invocation_data(invocation: ExtensionInvocation) -> dict[str, Any]:
        return {
            "invocation_id": invocation.id,
            "target_type": invocation.target_type,
            "tool_name": invocation.tool_name,
            "status": invocation.status,
            "result": invocation.result_json,
        }

    @staticmethod
    def transport_capabilities() -> list[dict[str, Any]]:
        stdio = UnavailableStdioMCPAdapter()
        return [
            {
                "transport": "streamable_http",
                "available": True,
                "protocol_version": PROTOCOL_VERSION,
                "supports_real_execution": True,
                "supports_encrypted_bearer_reference": True,
                "reason": (
                    "Real MCP Streamable HTTP JSON-RPC is available with one-attempt "
                    "tool calls, bounded responses, disabled redirects, and explicit authorization."
                ),
            },
            {
                "transport": "stdio",
                "available": stdio.available,
                "protocol_version": None,
                "supports_real_execution": False,
                "supports_encrypted_bearer_reference": False,
                "reason": stdio.unavailable_reason,
            },
        ]

    def list_servers(self) -> list[MCPServer]:
        return list(
            self.db.scalars(
                self.servers.query().order_by(MCPServer.created_at, MCPServer.id)
            ).all()
        )

    def require_server(self, server_id: str) -> MCPServer:
        return self.servers.require(server_id, "MCP server")

    def server_view_data(self, server: MCPServer) -> dict[str, Any]:
        credential = self._credential(server)
        return {
            **server.__dict__,
            "auth_configured": credential is not None,
            "auth_masked": credential.secret_masked if credential else None,
        }

    def create_server(self, payload: MCPServerCreateRequest) -> MCPServer:
        if self.db.scalar(
            self.servers.query().where(MCPServer.server_key == payload.server_key)
        ):
            raise AppError(409, "mcp_server_key_exists", "MCP server key already exists")
        self._validate_endpoint_shape(payload.transport, payload.endpoint_url)
        secret = payload.bearer_token.get_secret_value() if payload.bearer_token else None
        if secret and not self.settings.has_master_key:
            raise AppError(
                503,
                "secret_store_unavailable",
                "MCP bearer credentials require the configured encrypted secret store",
            )
        secret_fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16] if secret else None
        manifest = payload.manifest.model_dump(mode="json")
        required_permissions = self._mcp_permissions(payload.manifest.requested_tools, payload.manifest.permissions)
        manifest_hash = self._mcp_manifest_hash(
            payload.source,
            payload.version,
            payload.transport,
            payload.endpoint_url,
            manifest,
            secret_fingerprint,
            payload.timeout_ms,
            payload.max_input_bytes,
            payload.max_result_bytes,
            payload.max_concurrency,
        )
        server = self.servers.add(
            MCPServer(
                workspace_id=self.workspace_id,
                server_key=payload.server_key,
                display_name=payload.display_name.strip(),
                source=payload.source.strip(),
                version=payload.version.strip(),
                transport=payload.transport,
                endpoint_url=payload.endpoint_url.strip() if payload.endpoint_url else None,
                manifest_json=manifest,
                manifest_hash=manifest_hash,
                requested_tools=payload.manifest.requested_tools,
                required_permissions=required_permissions,
                status="unavailable" if payload.transport == "stdio" else "registered",
                enabled=False,
                agent_auto_invoke=payload.agent_auto_invoke,
                timeout_ms=payload.timeout_ms,
                max_input_bytes=payload.max_input_bytes,
                max_result_bytes=payload.max_result_bytes,
                max_concurrency=payload.max_concurrency,
                last_error=(
                    UnavailableStdioMCPAdapter.unavailable_reason
                    if payload.transport == "stdio"
                    else None
                ),
            )
        )
        if secret:
            masked, fingerprint = mask_secret(secret)
            credential = self.credentials.add(
                MCPServerCredential(
                    workspace_id=self.workspace_id,
                    server_id=server.id,
                    ciphertext=SecretCipher(self.settings.master_key).encrypt(secret),
                    secret_masked=masked,
                    secret_fingerprint=fingerprint,
                )
            )
            server.auth_reference = credential.id
        self.audit.record(
            actor_id=self.actor_id,
            action="mcp.server_register",
            resource_type="mcp_server",
            resource_id=server.id,
            details={
                "server_key": server.server_key,
                "transport": server.transport,
                "manifest_hash": server.manifest_hash,
                "auth_configured": bool(secret),
                "host_execution": False,
            },
        )
        self.db.commit()
        self.db.refresh(server)
        return server

    def update_server(self, server_id: str, payload: MCPServerUpdateRequest) -> MCPServer:
        server = self.require_server(server_id)
        if server.transport == "stdio" and payload.bearer_token is not None:
            raise AppError(
                422,
                "stdio_host_execution_forbidden",
                "stdio registrations cannot configure HTTP credentials in the host process",
            )
        endpoint = payload.endpoint_url if payload.endpoint_url is not None else server.endpoint_url
        self._validate_endpoint_shape(server.transport, endpoint)
        credential = self._credential(server)
        secret_fingerprint = credential.secret_fingerprint if credential else None
        if payload.clear_bearer_token and credential:
            self.credentials.delete(credential)
            server.auth_reference = None
            credential = None
            secret_fingerprint = None
        if payload.bearer_token is not None:
            secret = payload.bearer_token.get_secret_value()
            if not self.settings.has_master_key:
                raise AppError(
                    503,
                    "secret_store_unavailable",
                    "MCP bearer credentials require the configured encrypted secret store",
                )
            masked, fingerprint = mask_secret(secret)
            if credential is None:
                credential = self.credentials.add(
                    MCPServerCredential(
                        workspace_id=self.workspace_id,
                        server_id=server.id,
                        ciphertext=SecretCipher(self.settings.master_key).encrypt(secret),
                        secret_masked=masked,
                        secret_fingerprint=fingerprint,
                    )
                )
                server.auth_reference = credential.id
            else:
                credential.ciphertext = SecretCipher(self.settings.master_key).encrypt(secret)
                credential.secret_masked = masked
                credential.secret_fingerprint = fingerprint
            secret_fingerprint = fingerprint
        manifest = payload.manifest.model_dump(mode="json")
        timeout_ms = payload.timeout_ms if payload.timeout_ms is not None else server.timeout_ms
        max_input_bytes = (
            payload.max_input_bytes
            if payload.max_input_bytes is not None
            else server.max_input_bytes
        )
        max_result_bytes = (
            payload.max_result_bytes
            if payload.max_result_bytes is not None
            else server.max_result_bytes
        )
        max_concurrency = (
            payload.max_concurrency
            if payload.max_concurrency is not None
            else server.max_concurrency
        )
        new_hash = self._mcp_manifest_hash(
            payload.source,
            payload.version,
            server.transport,
            endpoint,
            manifest,
            secret_fingerprint,
            timeout_ms,
            max_input_bytes,
            max_result_bytes,
            max_concurrency,
        )
        changed = new_hash != server.manifest_hash
        server.display_name = payload.display_name.strip() if payload.display_name else server.display_name
        server.source = payload.source.strip()
        server.version = payload.version.strip()
        server.endpoint_url = endpoint.strip() if endpoint else None
        server.manifest_json = manifest
        server.manifest_hash = new_hash
        server.requested_tools = payload.manifest.requested_tools
        server.required_permissions = self._mcp_permissions(
            payload.manifest.requested_tools, payload.manifest.permissions
        )
        server.timeout_ms = timeout_ms
        server.max_input_bytes = max_input_bytes
        server.max_result_bytes = max_result_bytes
        server.max_concurrency = max_concurrency
        if payload.agent_auto_invoke is not None:
            server.agent_auto_invoke = payload.agent_auto_invoke
        if changed:
            self._supersede_grants("mcp_server", server.id)
            server.current_snapshot_id = None
            server.authorization_generation += 1
            server.enabled = False
            server.status = "unavailable" if server.transport == "stdio" else "registered"
            server.last_error = (
                UnavailableStdioMCPAdapter.unavailable_reason
                if server.transport == "stdio"
                else None
            )
        self.audit.record(
            actor_id=self.actor_id,
            action="mcp.server_update",
            resource_type="mcp_server",
            resource_id=server.id,
            details={
                "manifest_hash": new_hash,
                "authorization_invalidated": changed,
                "auth_configured": credential is not None,
            },
        )
        self.db.commit()
        self.db.refresh(server)
        return server

    def snapshots_for_server(self, server_id: str) -> list[MCPCapabilitySnapshot]:
        self.require_server(server_id)
        return list(
            self.db.scalars(
                self.snapshots.query()
                .where(MCPCapabilitySnapshot.server_id == server_id)
                .order_by(MCPCapabilitySnapshot.sequence.desc())
            ).all()
        )

    def refresh_server(self, server_id: str) -> MCPCapabilitySnapshot:
        server = self.require_server(server_id)
        return self._refresh_server(server)

    def _refresh_server(self, server: MCPServer) -> MCPCapabilitySnapshot:
        server.status = "probing"
        server.enabled = False
        self.db.flush()
        try:
            probe = self._adapter(server).probe()
            tools = self._normalize_tools(probe.tools)
            resources = self._normalize_named_items(probe.resources, "resource")
            prompts = self._normalize_named_items(probe.prompts, "prompt")
            snapshot_payload = {
                "protocol_version": probe.protocol_version,
                "server_identity": probe.server_identity,
                "capabilities": probe.capabilities,
                "tools": tools,
                "resources": resources,
                "prompts": prompts,
            }
            snapshot_hash = _hash(snapshot_payload)
            previous = self._current_snapshot(server)
            changed = previous is None or previous.snapshot_hash != snapshot_hash
            sequence = (
                self.db.scalar(
                    select(func.coalesce(func.max(MCPCapabilitySnapshot.sequence), 0)).where(
                        MCPCapabilitySnapshot.workspace_id == self.workspace_id,
                        MCPCapabilitySnapshot.server_id == server.id,
                    )
                )
                or 0
            ) + 1
            snapshot = self.snapshots.add(
                MCPCapabilitySnapshot(
                    workspace_id=self.workspace_id,
                    server_id=server.id,
                    sequence=sequence,
                    protocol_version=probe.protocol_version,
                    server_identity=probe.server_identity,
                    capabilities=probe.capabilities,
                    tools=tools,
                    resources=resources,
                    prompts=prompts,
                    required_permissions=server.required_permissions,
                    snapshot_hash=snapshot_hash,
                    changed=changed,
                    reauthorization_required=changed,
                )
            )
            server.current_snapshot_id = snapshot.id
            server.last_checked_at = utc_now()
            server.last_error = None
            if changed:
                self._supersede_grants("mcp_server", server.id)
                server.authorization_generation += 1
            missing = sorted(
                set(server.requested_tools) - {str(item["name"]) for item in tools}
            )
            manifest = server.manifest_json or {}
            missing_resources = sorted(
                set(manifest.get("requested_resources") or [])
                - {str(item.get("uri") or "") for item in resources}
            )
            missing_prompts = sorted(
                set(manifest.get("requested_prompts") or [])
                - {str(item.get("name") or "") for item in prompts}
            )
            if missing or missing_resources or missing_prompts:
                server.status = "unavailable"
                server.enabled = False
                server.last_error = (
                    "Requested tools, resources, or prompts are not present in the latest capability snapshot"
                )
            else:
                self._apply_grant_state(server, "mcp_server", self._server_authorization_hash(server, snapshot))
            self.audit.record(
                actor_id=self.actor_id,
                action="mcp.capability_refresh",
                resource_type="mcp_server",
                resource_id=server.id,
                details={
                    "snapshot_id": snapshot.id,
                    "snapshot_hash": snapshot.snapshot_hash,
                    "changed": changed,
                    "reauthorization_required": changed,
                    "tool_count": len(tools),
                    "resource_count": len(resources),
                    "prompt_count": len(prompts),
                    "missing_requested_tools": missing,
                    "missing_requested_resources": missing_resources,
                    "missing_requested_prompts": missing_prompts,
                },
            )
            self.db.commit()
            self.db.refresh(snapshot)
            if missing or missing_resources or missing_prompts:
                raise AppError(
                    409,
                    "mcp_requested_capabilities_unavailable",
                    "The MCP server does not expose every capability requested by its manifest",
                    {
                        "missing_tools": missing,
                        "missing_resources": missing_resources,
                        "missing_prompts": missing_prompts,
                        "snapshot_id": snapshot.id,
                    },
                )
            return snapshot
        except AppError:
            raise
        except MCPTransportFailure as exc:
            server.status = "unavailable"
            server.enabled = False
            server.last_checked_at = utc_now()
            server.last_error = str(exc)
            self.audit.record(
                actor_id=self.actor_id,
                action="mcp.capability_refresh",
                resource_type="mcp_server",
                resource_id=server.id,
                outcome="failure",
                details={"error_code": exc.code, "transport": server.transport},
            )
            self.db.commit()
            status_code = 504 if isinstance(exc, MCPTransportTimeout) else 503
            raise AppError(status_code, exc.code, str(exc), {"server_id": server.id}) from exc

    def authorize_server(
        self, server_id: str, payload: PermissionDecisionRequest
    ) -> ExtensionPermissionGrant:
        server = self.require_server(server_id)
        snapshot = self._current_snapshot(server)
        if snapshot is None:
            raise AppError(
                409,
                "mcp_capability_snapshot_required",
                "Refresh MCP capabilities before making a permission decision",
            )
        if server.status == "unavailable" and payload.decision != "deny":
            raise AppError(
                409,
                "mcp_server_unavailable",
                "Unavailable MCP capabilities cannot be authorized",
            )
        return self._authorize_subject(
            subject_type="mcp_server",
            subject_id=server.id,
            expected_permissions=server.required_permissions,
            authorization_hash=self._server_authorization_hash(server, snapshot),
            payload=payload,
            subject=server,
        )

    def invoke_mcp(self, server_id: str, payload: MCPInvokeRequest) -> ExtensionInvocation:
        server = self.require_server(server_id)
        arguments = dict(payload.arguments)
        input_size = len(_canonical_bytes(arguments))
        invocation = self._create_invocation(
            target_type="mcp_tool",
            target_id=server.id,
            tool_name=payload.tool_name,
            input_json=arguments if input_size <= server.max_input_bytes else {},
            input_size=input_size,
            timeout_ms=server.timeout_ms,
        )
        invocation.input_hash = _hash(arguments)
        if payload.tool_name not in server.requested_tools:
            self._fail_invocation(
                invocation,
                "mcp_tool_not_requested",
                "The tool is not included in the reviewed MCP manifest",
                status="denied",
                http_status=403,
            )
        if _contains_sensitive_key(arguments):
            self._fail_invocation(
                invocation,
                "extension_secret_argument_forbidden",
                "Credentials must use an encrypted MCP auth reference, not tool arguments",
                status="denied",
                http_status=422,
            )
        if input_size > server.max_input_bytes:
            self._fail_invocation(
                invocation,
                "mcp_input_too_large",
                "MCP tool input exceeded the configured byte limit",
                status="input_too_large",
                http_status=413,
            )
        try:
            snapshot = self._refresh_server(server)
        except AppError as exc:
            self._fail_invocation(
                invocation,
                exc.code,
                exc.message,
                status="unavailable" if exc.status_code >= 500 else "denied",
                http_status=exc.status_code,
                details=exc.details,
            )
        tool = next(item for item in snapshot.tools if item.get("name") == payload.tool_name)
        try:
            _validate_instance(tool["inputSchema"], arguments, "MCP tool arguments")
        except AppError as exc:
            self._fail_invocation(
                invocation,
                exc.code,
                exc.message,
                status="denied",
                http_status=exc.status_code,
                details=exc.details,
            )
        authorization_hash = self._server_authorization_hash(server, snapshot)
        grant = self._usable_grant("mcp_server", server.id, authorization_hash)
        if grant is None:
            self._fail_invocation(
                invocation,
                "extension_authorization_required",
                "The MCP capability snapshot requires an active permission decision",
                status="denied",
                http_status=409,
                details={"required_permissions": server.required_permissions},
            )
        if grant.decision == "deny":
            self._fail_invocation(
                invocation,
                "extension_permission_denied",
                "The current permission decision denies this MCP server",
                status="denied",
                http_status=403,
            )
        running = self.db.scalar(
            select(func.count()).select_from(ExtensionInvocation).where(
                ExtensionInvocation.workspace_id == self.workspace_id,
                ExtensionInvocation.target_type == "mcp_tool",
                ExtensionInvocation.target_id == server.id,
                ExtensionInvocation.status == "running",
            )
        ) or 0
        if running >= server.max_concurrency:
            self._fail_invocation(
                invocation,
                "mcp_concurrency_limit",
                "MCP server reached its configured concurrency limit",
                status="denied",
                http_status=429,
            )
        self._consume_allow_once(grant, server)
        invocation.status = "running"
        invocation.grant_id = grant.id
        invocation.authorization_hash = authorization_hash
        invocation.started_at = utc_now()
        self.db.commit()
        try:
            result = self._adapter(server).call_tool(payload.tool_name, arguments).result
            persisted = _redact_sensitive_keys(result)
            result_bytes = _canonical_bytes(persisted)
            if len(result_bytes) > server.max_result_bytes:
                raise MCPResponseTooLarge(
                    "MCP tool result exceeded the configured result-size limit"
                )
            if result.get("isError") is True:
                raise MCPProtocolFailure("MCP tool reported isError=true")
            invocation.status = "succeeded"
            invocation.result_json = persisted
            invocation.result_size_bytes = len(result_bytes)
            invocation.result_hash = hashlib.sha256(result_bytes).hexdigest()
            invocation.finished_at = utc_now()
            self.audit.record(
                actor_id=self.actor_id,
                action="mcp.tool_invoke",
                resource_type="mcp_server",
                resource_id=server.id,
                details={
                    "invocation_id": invocation.id,
                    "tool_name": payload.tool_name,
                    "input_size_bytes": invocation.input_size_bytes,
                    "result_size_bytes": invocation.result_size_bytes,
                    "snapshot_hash": snapshot.snapshot_hash,
                    "attempts": 1,
                },
            )
            self.db.commit()
            self.db.refresh(invocation)
            return invocation
        except MCPTransportFailure as exc:
            status = "timed_out" if isinstance(exc, MCPTransportTimeout) else (
                "result_too_large" if isinstance(exc, MCPResponseTooLarge) else "failed"
            )
            http_status = 504 if isinstance(exc, MCPTransportTimeout) else (
                413 if isinstance(exc, MCPResponseTooLarge) else 502
            )
            self._fail_invocation(
                invocation,
                exc.code,
                str(exc),
                status=status,
                http_status=http_status,
            )
        raise AssertionError("unreachable")

    def revoke_server(self, server_id: str, reason: str) -> MCPServer:
        server = self.require_server(server_id)
        self._supersede_grants("mcp_server", server.id, revoked=True)
        server.enabled = False
        server.status = "revoked"
        server.authorization_generation += 1
        self.audit.record(
            actor_id=self.actor_id,
            action="mcp.server_revoke",
            resource_type="mcp_server",
            resource_id=server.id,
            details={"reason": reason[:1000]},
        )
        self.db.commit()
        self.db.refresh(server)
        return server

    def list_skills(self) -> list[SkillRecord]:
        return list(
            self.db.scalars(
                self.skills.query().order_by(SkillRecord.created_at, SkillRecord.id)
            ).all()
        )

    def require_skill(self, skill_id: str) -> SkillRecord:
        return self.skills.require(skill_id, "Skill")

    def create_skill(self, payload: SkillCreateRequest) -> SkillRecord:
        if self.db.scalar(
            self.skills.query().where(SkillRecord.skill_key == payload.skill_key)
        ):
            raise AppError(409, "skill_key_exists", "Skill key already exists")
        report = self._validate_skill_manifest(payload.manifest)
        manifest_json = payload.manifest.model_dump(mode="json")
        manifest_hash = self._skill_manifest_hash(payload.source, payload.version, manifest_json)
        skill = self.skills.add(
            SkillRecord(
                workspace_id=self.workspace_id,
                skill_key=payload.skill_key,
                name=payload.name.strip(),
                source=payload.source.strip(),
                version=payload.version.strip(),
                generated_by=payload.generated_by,
                kind=payload.manifest.kind,
                package_format="declarative_json",
                content_hash=manifest_hash,
                origin_type=payload.generated_by,
                origin_ref=payload.source.strip(),
                origin_hash="",
                has_scripts=False,
                locale_source="",
                manifest_json=manifest_json,
                manifest_hash=manifest_hash,
                instructions_markdown=payload.manifest.instructions_markdown,
                required_tools=list(payload.manifest.required_tools),
                required_permissions=list(payload.manifest.permissions),
                allowed_components=list(payload.manifest.allowed_components),
                validation_report=report,
                status="authorization_required",
                enabled=False,
            )
        )
        auto_enabled = (
            payload.auto_enable_requested
            and payload.generated_by == "agent"
            and payload.manifest.kind == "declarative_review"
            and not payload.manifest.allowed_components
            and set(payload.manifest.required_tools) == {"builtin.review.list_due"}
            and set(payload.manifest.permissions) == {"mastery.read"}
        )
        if auto_enabled:
            grant = self.grants.add(
                ExtensionPermissionGrant(
                    workspace_id=self.workspace_id,
                    subject_type="skill",
                    subject_id=skill.id,
                    decision="always",
                    status="active",
                    permissions=skill.required_permissions,
                    authorization_hash=self._skill_authorization_hash(skill),
                    decided_by="system-policy",
                    reason="declarative_builtin_review_auto_enable",
                )
            )
            skill.status = "enabled"
            skill.enabled = True
            report = dict(skill.validation_report)
            report["auto_enabled_grant_id"] = grant.id
            report["reversible_notice_required"] = True
            skill.validation_report = report
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.install",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "skill_key": skill.skill_key,
                "manifest_hash": skill.manifest_hash,
                "generated_by": skill.generated_by,
                "auto_enabled": auto_enabled,
                "host_execution": False,
            },
        )
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def update_skill(self, skill_id: str, payload: SkillUpdateRequest) -> SkillRecord:
        skill = self.require_skill(skill_id)
        report = self._validate_skill_manifest(payload.manifest)
        manifest_json = payload.manifest.model_dump(mode="json")
        manifest_hash = self._skill_manifest_hash(payload.source, payload.version, manifest_json)
        changed = manifest_hash != skill.manifest_hash
        skill.name = payload.name.strip() if payload.name else skill.name
        skill.source = payload.source.strip()
        skill.version = payload.version.strip()
        skill.manifest_json = manifest_json
        skill.manifest_hash = manifest_hash
        skill.instructions_markdown = payload.manifest.instructions_markdown
        skill.required_tools = list(payload.manifest.required_tools)
        skill.required_permissions = list(payload.manifest.permissions)
        skill.allowed_components = list(payload.manifest.allowed_components)
        skill.validation_report = report
        if changed:
            self._supersede_grants("skill", skill.id)
            skill.authorization_generation += 1
            skill.enabled = False
            skill.status = "authorization_required"
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.update",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "manifest_hash": manifest_hash,
                "authorization_invalidated": changed,
            },
        )
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def authorize_skill(
        self, skill_id: str, payload: PermissionDecisionRequest
    ) -> ExtensionPermissionGrant:
        skill = self.require_skill(skill_id)
        return self._authorize_subject(
            subject_type="skill",
            subject_id=skill.id,
            expected_permissions=skill.required_permissions,
            authorization_hash=self._skill_authorization_hash(skill),
            payload=payload,
            subject=skill,
        )

    def invoke_skill(self, skill_id: str, payload: SkillInvokeRequest) -> ExtensionInvocation:
        skill = self.require_skill(skill_id)
        input_json = dict(payload.input)
        input_size = len(_canonical_bytes(input_json))
        invocation = self._create_invocation(
            target_type="skill",
            target_id=skill.id,
            skill_id=skill.id,
            tool_name="declarative_skill",
            input_json=input_json if input_size <= SKILL_MAX_INPUT_BYTES else {},
            input_size=input_size,
            timeout_ms=0,
        )
        invocation.input_hash = _hash(input_json)
        if self._is_agent_skill_package(skill):
            self._fail_invocation(
                invocation,
                "skill_package_not_invokable",
                "Agent Skill packages inject instructions only; use sandbox-run for package scripts",
                status="denied",
                http_status=409,
            )
        if _contains_sensitive_key(input_json):
            self._fail_invocation(
                invocation,
                "extension_secret_argument_forbidden",
                "Skill input cannot contain credentials or secrets",
                status="denied",
                http_status=422,
            )
        if input_size > SKILL_MAX_INPUT_BYTES:
            self._fail_invocation(
                invocation,
                "skill_input_too_large",
                "Skill input exceeded the 64 KiB limit",
                status="input_too_large",
                http_status=413,
            )
        try:
            manifest = SkillManifest.model_validate(skill.manifest_json)
        except Exception as exc:
            self._fail_invocation(
                invocation,
                "invalid_skill_manifest",
                "Skill manifest is not a valid declarative SkillManifest",
                status="denied",
                http_status=422,
                details={"error_type": type(exc).__name__},
            )
        try:
            _validate_instance(manifest.input_schema, input_json, "Skill input")
        except AppError as exc:
            self._fail_invocation(
                invocation,
                exc.code,
                exc.message,
                status="denied",
                http_status=exc.status_code,
                details=exc.details,
            )
        authorization_hash = self._skill_authorization_hash(skill)
        grant = self._usable_grant("skill", skill.id, authorization_hash)
        if grant is None:
            self._fail_invocation(
                invocation,
                "extension_authorization_required",
                "Skill requires an active permission decision",
                status="denied",
                http_status=409,
                details={"required_permissions": skill.required_permissions},
            )
        if grant.decision == "deny":
            self._fail_invocation(
                invocation,
                "extension_permission_denied",
                "The current permission decision denies this Skill",
                status="denied",
                http_status=403,
            )
        self._consume_allow_once(grant, skill)
        invocation.status = "running"
        invocation.grant_id = grant.id
        invocation.authorization_hash = authorization_hash
        invocation.started_at = utc_now()
        self.db.commit()
        try:
            results = []
            for index, step in enumerate(manifest.steps, start=1):
                arguments = self._resolve_step_arguments(step.arguments, input_json)
                result = self._run_builtin_tool(step.tool, arguments)
                results.append({"step": index, "tool": step.tool, "result": result})
            persisted = {"steps": results}
            result_bytes = _canonical_bytes(persisted)
            if len(result_bytes) > SKILL_MAX_RESULT_BYTES:
                raise AppError(
                    413,
                    "skill_result_too_large",
                    "Skill result exceeded the 256 KiB limit",
                )
            invocation.status = "succeeded"
            invocation.result_json = persisted
            invocation.result_size_bytes = len(result_bytes)
            invocation.result_hash = hashlib.sha256(result_bytes).hexdigest()
            invocation.finished_at = utc_now()
            self.audit.record(
                actor_id=self.actor_id,
                action="skill.invoke",
                resource_type="skill",
                resource_id=skill.id,
                details={
                    "invocation_id": invocation.id,
                    "manifest_hash": skill.manifest_hash,
                    "step_count": len(results),
                    "host_execution": False,
                },
            )
            self.db.commit()
            self.db.refresh(invocation)
            return invocation
        except AppError as exc:
            self._fail_invocation(
                invocation,
                exc.code,
                exc.message,
                status="failed",
                http_status=exc.status_code,
                details=exc.details,
            )
        raise AssertionError("unreachable")

    def invoke_builtin_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ExtensionInvocation:
        """Run one first-party domain tool and persist the complete invocation.

        This is intentionally the same audit/result boundary used for Skills
        and MCP.  An Agent therefore cannot turn a regular HTTP capability
        into an untracked in-process shortcut.
        """

        spec = BUILTIN_TOOL_SPECS.get(tool_name)
        if spec is None:
            raise AppError(
                422,
                "skill_tool_not_declarative_builtin",
                "The requested LearnGraph built-in tool is unavailable",
            )
        input_json = dict(arguments)
        input_size = len(_canonical_bytes(input_json))
        invocation = self._create_invocation(
            target_type="builtin_tool",
            target_id=tool_name,
            tool_name=tool_name,
            input_json=input_json if input_size <= SKILL_MAX_INPUT_BYTES else {},
            input_size=input_size,
            timeout_ms=0,
        )
        if _contains_sensitive_key(input_json):
            self._fail_invocation(
                invocation,
                "extension_secret_argument_forbidden",
                "Built-in tool input cannot contain credentials or secrets",
                status="denied",
                http_status=422,
            )
        if input_size > SKILL_MAX_INPUT_BYTES:
            self._fail_invocation(
                invocation,
                "builtin_tool_input_too_large",
                "Built-in tool input exceeded the 64 KiB limit",
                status="input_too_large",
                http_status=413,
            )
        try:
            _validate_instance(spec["parameters"], input_json, "Built-in tool input")
        except AppError as exc:
            self._fail_invocation(
                invocation,
                exc.code,
                exc.message,
                status="denied",
                http_status=exc.status_code,
                details=exc.details,
            )
        invocation.status = "running"
        invocation.started_at = utc_now()
        self.db.commit()
        try:
            result = self._run_builtin_tool(tool_name, input_json)
            result_bytes = _canonical_bytes(result)
            if len(result_bytes) > SKILL_MAX_RESULT_BYTES:
                raise AppError(
                    413,
                    "builtin_tool_result_too_large",
                    "Built-in tool result exceeded the 256 KiB limit",
                )
            invocation.status = "succeeded"
            invocation.result_json = result
            invocation.result_size_bytes = len(result_bytes)
            invocation.result_hash = hashlib.sha256(result_bytes).hexdigest()
            invocation.finished_at = utc_now()
            self.audit.record(
                actor_id=self.actor_id,
                action="builtin_tool.invoke",
                resource_type="builtin_tool",
                resource_id=tool_name,
                details={
                    "invocation_id": invocation.id,
                    "permissions": BUILTIN_TOOL_PERMISSIONS[tool_name],
                    "host_execution": False,
                },
            )
            self.db.commit()
            self.db.refresh(invocation)
            return invocation
        except AppError as exc:
            self._fail_invocation(
                invocation,
                exc.code,
                exc.message,
                status="failed",
                http_status=exc.status_code,
                details=exc.details,
            )
        raise AssertionError("unreachable")

    def revoke_skill(self, skill_id: str, reason: str) -> SkillRecord:
        skill = self.require_skill(skill_id)
        self._supersede_grants("skill", skill.id, revoked=True)
        skill.enabled = False
        skill.status = "revoked"
        skill.authorization_generation += 1
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.revoke",
            resource_type="skill",
            resource_id=skill.id,
            details={"reason": reason[:1000]},
        )
        self.db.commit()
        self.db.refresh(skill)
        return skill

    def delete_skill(self, skill_id: str, reason: str = "workspace_user_deleted") -> None:
        """Permanently remove a skill package/record and its workspace-local files."""

        skill = self.require_skill(skill_id)
        skill_key = skill.skill_key
        skill_name = skill.name
        self._supersede_grants("skill", skill.id, revoked=True)
        # Package files cascade via FK, but delete explicitly for audit clarity.
        package_files = list(
            self.db.scalars(
                select(SkillPackageFile).where(
                    SkillPackageFile.workspace_id == self.workspace_id,
                    SkillPackageFile.skill_id == skill.id,
                )
            ).all()
        )
        for row in package_files:
            self.db.delete(row)
        # Translation cache rows may point at this skill_id (nullable, no cascade).
        translations = list(
            self.db.scalars(
                select(SkillTranslationCache).where(
                    SkillTranslationCache.workspace_id == self.workspace_id,
                    SkillTranslationCache.skill_id == skill.id,
                )
            ).all()
        )
        for row in translations:
            row.skill_id = None
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.delete",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "reason": (reason or "")[:1000],
                "skill_key": skill_key,
                "name": skill_name,
                "file_count": len(package_files),
            },
        )
        self.skills.delete(skill)
        self.db.commit()

    def list_grants(
        self, subject_type: str | None = None, subject_id: str | None = None
    ) -> list[ExtensionPermissionGrant]:
        statement = self.grants.query()
        if subject_type:
            statement = statement.where(ExtensionPermissionGrant.subject_type == subject_type)
        if subject_id:
            statement = statement.where(ExtensionPermissionGrant.subject_id == subject_id)
        return list(
            self.db.scalars(
                statement.order_by(ExtensionPermissionGrant.created_at.desc())
            ).all()
        )

    def list_invocations(
        self, target_type: str | None = None, target_id: str | None = None
    ) -> list[ExtensionInvocation]:
        statement = self.invocations.query()
        if target_type:
            statement = statement.where(ExtensionInvocation.target_type == target_type)
        if target_id:
            statement = statement.where(ExtensionInvocation.target_id == target_id)
        return list(
            self.db.scalars(statement.order_by(ExtensionInvocation.created_at.desc())).all()
        )

    def _authorize_subject(
        self,
        *,
        subject_type: str,
        subject_id: str,
        expected_permissions: list[str],
        authorization_hash: str,
        payload: PermissionDecisionRequest,
        subject: MCPServer | SkillRecord,
    ) -> ExtensionPermissionGrant:
        supplied = sorted(set(payload.permissions))
        expected = sorted(set(expected_permissions))
        if payload.decision == "deny":
            if supplied:
                raise AppError(
                    409,
                    "permission_set_mismatch",
                    "deny decisions must not add permissions",
                    {"expected_permissions": []},
                )
        elif supplied != expected:
            raise AppError(
                409,
                "permission_set_mismatch",
                "Permission decision must exactly match the least-privilege request",
                {"expected_permissions": expected},
            )
        self._supersede_grants(subject_type, subject_id)
        grant = self.grants.add(
            ExtensionPermissionGrant(
                workspace_id=self.workspace_id,
                subject_type=subject_type,
                subject_id=subject_id,
                decision=payload.decision,
                status="active",
                permissions=expected if payload.decision != "deny" else [],
                authorization_hash=authorization_hash,
                decided_by=self.actor_id,
                reason=payload.reason.strip(),
            )
        )
        subject.enabled = payload.decision == "always"
        subject.status = {
            "always": "enabled",
            "allow_once": "ready_once",
            "deny": "disabled",
        }[payload.decision]
        self.audit.record(
            actor_id=self.actor_id,
            action=f"{subject_type}.permission_{payload.decision}",
            resource_type=subject_type,
            resource_id=subject_id,
            details={
                "grant_id": grant.id,
                "permissions": grant.permissions,
                "authorization_hash": authorization_hash,
            },
        )
        self.db.commit()
        self.db.refresh(grant)
        return grant

    def _supersede_grants(
        self, subject_type: str, subject_id: str, *, revoked: bool = False
    ) -> None:
        for grant in self.db.scalars(
            self.grants.query().where(
                ExtensionPermissionGrant.subject_type == subject_type,
                ExtensionPermissionGrant.subject_id == subject_id,
                ExtensionPermissionGrant.status == "active",
            )
        ):
            grant.status = "revoked" if revoked else "superseded"
            grant.revoked_at = utc_now()

    def _usable_grant(
        self, subject_type: str, subject_id: str, authorization_hash: str
    ) -> ExtensionPermissionGrant | None:
        grants = list(
            self.db.scalars(
                self.grants.query()
                .where(
                    ExtensionPermissionGrant.subject_type == subject_type,
                    ExtensionPermissionGrant.subject_id == subject_id,
                    ExtensionPermissionGrant.authorization_hash == authorization_hash,
                    ExtensionPermissionGrant.status == "active",
                )
                .order_by(ExtensionPermissionGrant.created_at.desc())
            ).all()
        )
        return grants[0] if grants else None

    def _apply_grant_state(
        self,
        subject: MCPServer | SkillRecord,
        subject_type: str,
        authorization_hash: str,
    ) -> None:
        grant = self._usable_grant(subject_type, subject.id, authorization_hash)
        if grant is None:
            subject.status = "authorization_required"
            subject.enabled = False
        elif grant.decision == "always":
            subject.status = "enabled"
            subject.enabled = True
        elif grant.decision == "allow_once":
            subject.status = "ready_once"
            subject.enabled = False
        else:
            subject.status = "disabled"
            subject.enabled = False

    @staticmethod
    def _consume_allow_once(
        grant: ExtensionPermissionGrant,
        subject: MCPServer | SkillRecord,
    ) -> None:
        if grant.decision != "allow_once":
            return
        grant.status = "consumed"
        grant.consumed_at = utc_now()
        subject.status = "authorization_required"
        subject.enabled = False

    def _create_invocation(
        self,
        *,
        target_type: str,
        target_id: str,
        tool_name: str,
        input_json: dict[str, Any],
        input_size: int,
        timeout_ms: int,
        skill_id: str | None = None,
    ) -> ExtensionInvocation:
        return self.invocations.add(
            ExtensionInvocation(
                workspace_id=self.workspace_id,
                target_type=target_type,
                target_id=target_id,
                skill_id=skill_id,
                tool_name=tool_name,
                status="pending",
                input_json=input_json,
                input_size_bytes=input_size,
                input_hash=_hash(input_json) if input_json else "",
                timeout_ms=timeout_ms,
            )
        )

    def _fail_invocation(
        self,
        invocation: ExtensionInvocation,
        code: str,
        message: str,
        *,
        status: str,
        http_status: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        invocation.status = status
        invocation.error_code = code
        invocation.error_message = message
        invocation.finished_at = utc_now()
        self.audit.record(
            actor_id=self.actor_id,
            action=f"{invocation.target_type}.invoke",
            resource_type=invocation.target_type,
            resource_id=invocation.target_id,
            outcome="failure",
            details={
                "invocation_id": invocation.id,
                "tool_name": invocation.tool_name,
                "error_code": code,
                "input_size_bytes": invocation.input_size_bytes,
            },
        )
        self.db.commit()
        raise AppError(
            http_status,
            code,
            message,
            {"invocation_id": invocation.id, **(details or {})},
        )

    def _adapter(self, server: MCPServer) -> MCPTransportPort:
        if server.transport == "stdio":
            return UnavailableStdioMCPAdapter()
        if not server.endpoint_url:
            raise MCPTransportUnavailable("MCP HTTP endpoint is not configured")
        return StreamableHTTPMCPAdapter(
            server.endpoint_url,
            bearer_token=self._credential_secret(server),
            timeout_ms=server.timeout_ms,
            max_response_bytes=server.max_result_bytes,
        )

    def _credential(self, server: MCPServer) -> MCPServerCredential | None:
        if not server.auth_reference:
            return None
        return self.db.scalar(
            self.credentials.query().where(
                MCPServerCredential.id == server.auth_reference,
                MCPServerCredential.server_id == server.id,
            )
        )

    def _credential_secret(self, server: MCPServer) -> str | None:
        credential = self._credential(server)
        if credential is None:
            return None
        if not self.settings.has_master_key:
            raise MCPTransportUnavailable(
                "MCP encrypted auth reference cannot be opened because the master key is unavailable"
            )
        try:
            return SecretCipher(self.settings.master_key).decrypt(credential.ciphertext)
        except ValueError as exc:
            raise MCPTransportUnavailable("MCP encrypted auth reference cannot be decrypted") from exc

    def _current_snapshot(self, server: MCPServer) -> MCPCapabilitySnapshot | None:
        if not server.current_snapshot_id:
            return None
        return self.db.scalar(
            self.snapshots.query().where(
                MCPCapabilitySnapshot.id == server.current_snapshot_id,
                MCPCapabilitySnapshot.server_id == server.id,
            )
        )

    @staticmethod
    def _mcp_permissions(tools: list[str], permissions: list[str]) -> list[str]:
        return sorted({*(f"mcp.tool:{name}" for name in tools), *permissions})

    @staticmethod
    def _mcp_manifest_hash(
        source: str,
        version: str,
        transport: str,
        endpoint_url: str | None,
        manifest: dict[str, Any],
        secret_fingerprint: str | None,
        timeout_ms: int,
        max_input_bytes: int,
        max_result_bytes: int,
        max_concurrency: int,
    ) -> str:
        return _hash(
            {
                "source": source.strip(),
                "version": version.strip(),
                "transport": transport,
                "endpoint_url": endpoint_url.strip() if endpoint_url else None,
                "manifest": manifest,
                "auth_fingerprint": secret_fingerprint,
                "timeout_ms": timeout_ms,
                "max_input_bytes": max_input_bytes,
                "max_result_bytes": max_result_bytes,
                "max_concurrency": max_concurrency,
            }
        )

    @staticmethod
    def _skill_manifest_hash(source: str, version: str, manifest: dict[str, Any]) -> str:
        return _hash(
            {"source": source.strip(), "version": version.strip(), "manifest": manifest}
        )

    @staticmethod
    def _server_authorization_hash(
        server: MCPServer, snapshot: MCPCapabilitySnapshot
    ) -> str:
        return _hash(
            {
                "subject_type": "mcp_server",
                "subject_id": server.id,
                "manifest_hash": server.manifest_hash,
                "snapshot_hash": snapshot.snapshot_hash,
                "permissions": server.required_permissions,
            }
        )

    @staticmethod
    def _skill_authorization_hash(skill: SkillRecord) -> str:
        return _hash(
            {
                "subject_type": "skill",
                "subject_id": skill.id,
                "manifest_hash": skill.manifest_hash,
                "permissions": skill.required_permissions,
            }
        )

    @staticmethod
    def _validate_endpoint_shape(transport: str, endpoint_url: str | None) -> None:
        if transport == "stdio":
            if endpoint_url:
                raise AppError(
                    422,
                    "stdio_host_execution_forbidden",
                    "stdio commands are not accepted without an isolated runner",
                )
            return
        parsed = urlsplit((endpoint_url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise AppError(
                422,
                "invalid_mcp_endpoint",
                "MCP endpoint must be an absolute http(s) URL",
            )
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise AppError(
                422,
                "invalid_mcp_endpoint",
                "MCP endpoint cannot contain credentials, query parameters, or fragments",
            )

    @staticmethod
    def _normalize_tools(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in values:
            name = str(raw.get("name") or "")
            if not TOOL_NAME_RE.fullmatch(name) or name in seen:
                raise MCPProtocolFailure("MCP tools/list returned an invalid or duplicate tool name")
            input_schema = raw.get("inputSchema")
            if not isinstance(input_schema, dict):
                raise MCPProtocolFailure(f"MCP tool {name} did not declare an inputSchema object")
            try:
                _validate_schema(input_schema, f"MCP tool {name} inputSchema")
                output_schema = raw.get("outputSchema")
                if output_schema is not None:
                    if not isinstance(output_schema, dict):
                        raise MCPProtocolFailure(
                            f"MCP tool {name} outputSchema must be an object"
                        )
                    _validate_schema(output_schema, f"MCP tool {name} outputSchema")
            except AppError as exc:
                raise MCPProtocolFailure(exc.message) from exc
            seen.add(name)
            item: dict[str, Any] = {
                "name": name,
                "title": str(raw.get("title") or "")[:300],
                "description": str(raw.get("description") or "")[:4_000],
                "inputSchema": input_schema,
                "annotations": raw.get("annotations")
                if isinstance(raw.get("annotations"), dict)
                else {},
                "annotations_trusted": False,
            }
            if isinstance(raw.get("outputSchema"), dict):
                item["outputSchema"] = raw["outputSchema"]
            if isinstance(raw.get("execution"), dict):
                item["execution"] = raw["execution"]
            normalized.append(item)
        return sorted(normalized, key=lambda item: str(item["name"]))

    @staticmethod
    def _normalize_named_items(
        values: list[dict[str, Any]], item_type: str
    ) -> list[dict[str, Any]]:
        normalized = [_redact_sensitive_keys(dict(item)) for item in values]
        key = "uri" if item_type == "resource" else "name"
        return sorted(normalized, key=lambda item: str(item.get(key) or ""))

    @staticmethod
    def _validate_skill_manifest(manifest: SkillManifest) -> dict[str, Any]:
        _validate_schema(manifest.input_schema, "Skill input_schema")
        for step in manifest.steps:
            if step.tool not in BUILTIN_TOOL_PERMISSIONS:
                raise AppError(
                    422,
                    "skill_tool_not_declarative_builtin",
                    "Skills may invoke only registered declarative LearnGraph built-in tools",
                )
            if _contains_sensitive_key(step.arguments):
                raise AppError(
                    422,
                    "extension_secret_argument_forbidden",
                    "Skill step templates cannot contain credentials or secrets",
                )
            if len(_canonical_bytes(step.arguments)) > 16 * 1024:
                raise AppError(
                    422,
                    "skill_step_too_large",
                    "A declarative Skill step exceeds 16 KiB",
                )
        expected_permissions = sorted(
            {
                permission
                for tool in manifest.required_tools
                for permission in BUILTIN_TOOL_PERMISSIONS[tool]
            }
        )
        if sorted(set(manifest.permissions)) != expected_permissions:
            raise AppError(
                422,
                "skill_permission_mismatch",
                "Skill permissions must exactly match the selected built-in tools",
                {"expected_permissions": expected_permissions},
            )
        return {
            "valid": True,
            "declarative_only": True,
            "host_execution": False,
            "schema_validated": True,
            "required_tools": list(manifest.required_tools),
            "required_permissions": expected_permissions,
            "allowed_components": list(manifest.allowed_components),
        }

    @staticmethod
    def _resolve_step_arguments(template: Any, input_json: dict[str, Any]) -> Any:
        if isinstance(template, str) and template.startswith("$input."):
            value: Any = input_json
            path = template[len("$input.") :].split(".")
            for key in path:
                if not isinstance(value, dict) or key not in value:
                    raise AppError(
                        422,
                        "skill_input_reference_missing",
                        f"Skill input does not provide {template}",
                    )
                value = value[key]
            return value
        if isinstance(template, dict):
            return {
                key: MCPAndSkillService._resolve_step_arguments(value, input_json)
                for key, value in template.items()
            }
        if isinstance(template, list):
            return [
                MCPAndSkillService._resolve_step_arguments(value, input_json)
                for value in template
            ]
        return template

    def _run_builtin_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a bounded first-party operation inside the current scope.

        All mutation routes below delegate to the application service that is
        also used by HTTP.  In particular, graph publication remains
        immutable, roadmap re-planning stays a draft, evidence stays pending,
        and historical usage events are never editable.
        """

        spec = BUILTIN_TOOL_SPECS.get(tool_name)
        if spec is None:
            raise AppError(
                422,
                "skill_tool_not_declarative_builtin",
                "Skill requested an unavailable built-in tool",
            )
        _validate_instance(spec["parameters"], arguments, "Built-in tool input")

        if tool_name == "builtin.review.list_due":
            limit = int(arguments.get("limit", 20))
            # Preserve compatibility with the original review-only declarative
            # Skill while filtering by the live ACL whenever this is an Agent
            # invocation from the authenticated HTTP boundary.  In that case
            # do not apply SQL LIMIT first: a private early row must not crowd
            # out an accessible due node later in the schedule.
            authz = None
            workspace = None
            if self.workspace is not None and self.principal is not None:
                workspace, _, authz = self._runtime_context()
            statement = (
                select(MasterySchedule, GraphNode)
                .join(GraphNode, GraphNode.id == MasterySchedule.node_id)
                .where(
                    MasterySchedule.workspace_id == self.workspace_id,
                    GraphNode.workspace_id == self.workspace_id,
                    MasterySchedule.next_review_at.is_not(None),
                    MasterySchedule.next_review_at <= utc_now(),
                )
                .order_by(MasterySchedule.next_review_at, MasterySchedule.node_id)
            )
            if authz is None:
                statement = statement.limit(limit)
            due_nodes: list[dict[str, Any]] = []
            for schedule, node in self.db.execute(statement):
                if authz is not None and not authz.can_access_bindings(
                    workspace, "read", node_id=node.id
                ):
                    continue
                due_nodes.append(
                    {
                        "node_id": schedule.node_id,
                        "label": node.label,
                        "next_review_at": schedule.next_review_at.isoformat()
                        if schedule.next_review_at
                        else None,
                        "retrieval_state": node.retrieval_state,
                        "evidence_state": node.evidence_state,
                    }
                )
                if len(due_nodes) >= limit:
                    break
            return {
                "due_nodes": due_nodes,
                "count": len(due_nodes),
                "workspace_id": self.workspace_id,
            }

        workspace, principal, authz = self._runtime_context()

        if tool_name == "builtin.graph.read":
            graph_id = str(arguments["graph_id"])
            graph = self.db.scalar(
                select(Graph).where(
                    Graph.workspace_id == self.workspace_id,
                    Graph.id == graph_id,
                )
            )
            if graph is None or not authz.can_access_resource(
                workspace, "graph", graph.id, "read"
            ):
                raise AppError(404, "graph_not_found", "Graph was not found")
            query = str(arguments.get("query") or "").strip().casefold()
            limit = int(arguments.get("limit", 40))
            edge_limit = int(arguments.get("edge_limit", min(limit * 3, 240)))
            nodes = list(
                self.db.scalars(
                    select(GraphNode)
                    .where(
                        GraphNode.workspace_id == self.workspace_id,
                        GraphNode.graph_id == graph.id,
                    )
                    .order_by(GraphNode.label, GraphNode.id)
                )
            )
            if query:
                nodes = [
                    node
                    for node in nodes
                    if query in node.label.casefold()
                    or query in node.description.casefold()
                ]
            edge_rows = list(
                self.db.scalars(
                    select(GraphEdge)
                    .where(
                        GraphEdge.workspace_id == self.workspace_id,
                        GraphEdge.graph_id == graph.id,
                    )
                    .order_by(GraphEdge.id)
                    .limit(edge_limit + 1)
                )
            )
            edges_truncated = len(edge_rows) > edge_limit
            from app.domain.schemas.graphs import GraphNodeView

            return {
                "graph": {
                    "id": graph.id,
                    "goal_id": graph.goal_id,
                    "title": graph.title,
                    "status": graph.status,
                    "revision": graph.revision,
                },
                "nodes": [
                    GraphNodeView.model_validate(node).model_dump(mode="json")
                    for node in nodes[:limit]
                ],
                "edges": [
                    {
                        "id": edge.id,
                        "source_node_id": edge.source_node_id,
                        "target_node_id": edge.target_node_id,
                        "relation": edge.relation,
                    }
                    for edge in edge_rows[:edge_limit]
                ],
                "query": query or None,
                "matched_count": len(nodes),
                "nodes_truncated": len(nodes) > limit,
                "edges_truncated": edges_truncated,
            }

        if tool_name == "builtin.graph.update_candidate_node":
            from app.domain.schemas.graphs import GraphNodeView, UpdateNodeRequest
            from app.providers.factory import model_provider_for_workspace
            from app.services.graphs import GraphService

            graph_id = str(arguments["graph_id"])
            graph = self.db.scalar(
                select(Graph).where(
                    Graph.workspace_id == self.workspace_id,
                    Graph.id == graph_id,
                )
            )
            if graph is None or not authz.can_access_resource(
                workspace, "graph", graph_id, "write"
            ):
                raise AppError(404, "graph_not_found", "Graph was not found")
            if graph.status != "candidate":
                raise AppError(
                    409,
                    "published_graph_immutable",
                    "Agent graph mutations require a candidate revision and user review",
                )
            payload = UpdateNodeRequest.model_validate(
                {
                    key: value
                    for key, value in arguments.items()
                    if key not in {"graph_id", "node_id"}
                }
            )
            manager = GraphService(
                self.db,
                self.workspace_id,
                principal.user_id,
                model_provider_for_workspace(self.db, self.workspace_id, self.settings),
                graph_access_checker=lambda resource_id, permission: authz.can_access_bindings(
                    workspace, permission, graph_id=resource_id
                ),
            )
            node = manager.update_node(graph_id, str(arguments["node_id"]), payload)
            return {
                "node": GraphNodeView.model_validate(node).model_dump(mode="json"),
                "graph_id": graph_id,
                "review_required_for_publication": True,
            }

        workflow = WorkflowService(self.db, workspace, principal)
        if tool_name == "builtin.roadmap.read":
            from app.domain.schemas.workflow import RoadmapView

            roadmap = (
                workflow.roadmap_by_id(str(arguments["roadmap_id"]))
                if "roadmap_id" in arguments
                else workflow.roadmap(str(arguments["goal_id"]))
            )
            return RoadmapView.model_validate(roadmap).model_dump(mode="json")

        if tool_name == "builtin.roadmap.replan":
            from app.domain.schemas.workflow import RoadmapView

            roadmap = workflow.replan_roadmap(str(arguments["goal_id"]))
            return {
                "roadmap": RoadmapView.model_validate(
                    workflow.roadmap_by_id(roadmap.id)
                ).model_dump(mode="json"),
                "status": roadmap.status,
                "review_required_for_publication": True,
            }

        if tool_name == "builtin.action.list":
            from app.domain.schemas.workflow import ActionView

            limit = int(arguments.get("limit", 50))
            actions = workflow.actions(arguments.get("status"))
            return {
                "actions": [
                    ActionView.model_validate(item).model_dump(mode="json")
                    for item in actions[:limit]
                ],
                "truncated": len(actions) > limit,
            }

        if tool_name == "builtin.action.create":
            from app.domain.schemas.workflow import ActionView

            action = workflow.create_action(ActionCreate.model_validate(arguments))
            return ActionView.model_validate(action).model_dump(mode="json")

        if tool_name == "builtin.action.update":
            from app.domain.schemas.workflow import ActionView

            action_id = str(arguments["action_id"])
            action = workflow.update_action(
                action_id,
                ActionUpdate.model_validate(
                    {key: value for key, value in arguments.items() if key != "action_id"}
                ),
            )
            return ActionView.model_validate(action).model_dump(mode="json")

        if tool_name == "builtin.learning.mastery.read":
            node_id = arguments.get("node_id")
            if node_id and not authz.can_access_bindings(
                workspace, "read", node_id=str(node_id)
            ):
                raise AppError(404, "graph_node_not_found", "Graph node was not found")
            limit = int(arguments.get("limit", 100))
            mastery = EvidenceService(
                self.db, self.workspace_id, principal.user_id
            ).mastery()
            visible = [
                item
                for item in mastery
                if (not node_id or item.node_id == node_id)
                and authz.can_access_bindings(workspace, "read", node_id=item.node_id)
            ]
            return {
                "mastery": [item.model_dump(mode="json") for item in visible[:limit]],
                "count": len(visible),
            }

        if tool_name == "builtin.learning.evidence.record":
            node_id = str(arguments["node_id"])
            if not authz.can_access_bindings(workspace, "write", node_id=node_id):
                raise AppError(404, "graph_node_not_found", "Graph node was not found")
            source_type = str(arguments["source_type"])
            source_id = str(arguments["source_id"])
            trace: dict[str, Any] = {
                "origin": "agent_builtin_tool",
                "source_type": source_type,
                "source_id": source_id,
            }
            payload: dict[str, Any] = {
                "node_id": node_id,
                "source_type": source_type,
                "summary": str(arguments["summary"]),
                "confidence": arguments.get("confidence", 0.5),
                "metadata": {"agent_trace": trace},
            }
            if source_type == "file":
                file_record = self.db.scalar(
                    select(FileRecord).where(
                        FileRecord.workspace_id == self.workspace_id,
                        FileRecord.id == source_id,
                    )
                )
                if file_record is None or not authz.can_access_resource(
                    workspace, "file", source_id, "read"
                ):
                    raise AppError(404, "evidence_source_not_found", "File source was not found")
                payload["file_id"] = source_id
                payload["locator"] = str(arguments.get("locator") or "")
            elif source_type == "conversation":
                message = self.db.scalar(
                    select(Message).where(
                        Message.workspace_id == self.workspace_id,
                        Message.id == source_id,
                    )
                )
                if message is None or not authz.can_access_resource(
                    workspace, "session", message.session_id, "read"
                ):
                    raise AppError(
                        404,
                        "evidence_source_not_found",
                        "Conversation message source was not found",
                    )
                trace["session_id"] = message.session_id
            elif source_type == "exercise":
                row = self.db.execute(
                    select(AnswerRecord, Exercise)
                    .join(Exercise, Exercise.id == AnswerRecord.exercise_id)
                    .where(
                        AnswerRecord.workspace_id == self.workspace_id,
                        AnswerRecord.id == source_id,
                        Exercise.workspace_id == self.workspace_id,
                    )
                ).one_or_none()
                if row is None:
                    raise AppError(404, "evidence_source_not_found", "Exercise answer was not found")
                answer, exercise = row
                if not answer.is_correct or exercise.node_id != node_id:
                    raise AppError(
                        409,
                        "evidence_source_not_eligible",
                        "Evidence requires a correct answer for the selected graph node",
                    )
                if not authz.can_access_bindings(workspace, "read", node_id=exercise.node_id):
                    raise AppError(404, "evidence_source_not_found", "Exercise answer was not found")
                trace["exercise_id"] = exercise.id
            else:
                raise AppError(422, "invalid_tool_arguments", "Unsupported evidence source type")
            evidence = EvidenceService(
                self.db, self.workspace_id, principal.user_id
            ).create(EvidenceCreateRequest.model_validate(payload))
            return {
                "evidence_id": evidence.id,
                "node_id": evidence.node_id,
                "status": evidence.status,
                "confidence": evidence.confidence,
                "trace": evidence.metadata_json.get("agent_trace", {}),
                "requires_human_decision": True,
            }

        if tool_name == "builtin.usage.summary":
            self._require_workspace_permission(authz, workspace, "workspace.read")
            return UsageService(self.db, self.workspace_id).summary(
                provider_id=arguments.get("provider_id"),
                model_id=arguments.get("model_id"),
                feature=arguments.get("feature"),
            ).model_dump(mode="json")

        if tool_name in {
            "builtin.usage.budget.create",
            "builtin.usage.budget.update",
        }:
            self._require_workspace_permission(authz, workspace, "workspace.manage")
            from app.domain.schemas.management import BudgetPolicyView

            billing = BillingService(self.db, self.workspace_id, principal.user_id)
            if tool_name == "builtin.usage.budget.create":
                policy = billing.create_budget_policy(
                    name=str(arguments["name"]),
                    provider_id=str(arguments["provider_id"]),
                    model_id=str(arguments["model_id"]),
                    feature=str(arguments["feature"]),
                    period=str(arguments["period"]),
                    soft_limit_cny=arguments.get("soft_limit_cny"),
                    hard_limit_cny=arguments.get("hard_limit_cny"),
                    enabled=bool(arguments.get("enabled", True)),
                )
            else:
                policy = billing.update_budget_policy(
                    str(arguments["policy_id"]),
                    name=str(arguments["name"]),
                    soft_limit_cny=arguments.get("soft_limit_cny"),
                    hard_limit_cny=arguments.get("hard_limit_cny"),
                    enabled=bool(arguments["enabled"]),
                )
            return BudgetPolicyView.model_validate(policy).model_dump(mode="json")

        raise AppError(
            422,
            "skill_tool_not_declarative_builtin",
            "Skill requested an unavailable built-in tool",
        )

    @staticmethod
    def _require_workspace_permission(
        authz: AuthorizationService,
        workspace: Workspace,
        permission: str,
    ) -> None:
        if permission not in authz.workspace_permissions(workspace):
            raise AppError(403, "permission_denied", f"Permission '{permission}' is required")
