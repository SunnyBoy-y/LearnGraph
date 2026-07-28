from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from app.core.security import Principal, SecretCipher, mask_secret, verify_password
from app.domain.extension_models import (
    ExtensionInvocation,
    ExtensionPermissionGrant,
    MCPCapabilitySnapshot,
    MCPServer,
    MCPServerCredential,
    SkillPackageFile,
    SkillDeleteConfirmation,
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
    User,
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
from app.services.skill_package import (
    CONTEXTUAL_OFFICIAL_SKILL_KEYS,
    MAX_SKILL_FILE_BYTES,
    assert_skill_identity_not_reserved,
    is_official_skill_record,
    normalize_skill_relative_path,
)
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
    "builtin.skills.announce_usage": [],
    "builtin.skills.list": ["workspace.read"],
    "builtin.skills.read": ["workspace.read"],
    "builtin.skills.install": ["workspace.write"],
    "builtin.skills.create": ["workspace.write"],
    "builtin.skills.write_file": ["workspace.write"],
    "builtin.skills.set_enabled": ["workspace.write"],
    "builtin.skills.delete.request": ["workspace.write"],
    "builtin.mcp.list": ["workspace.read"],
    "builtin.mcp.register": ["workspace.write"],
    "builtin.mcp.update": ["workspace.write"],
    "builtin.mcp.set_enabled": ["workspace.write"],
    "builtin.mcp.delete": ["workspace.write"],
}

# Extension self-service tools: the Agent can manage the same skill/MCP surface
# the user configures by clicking, gated by settings and workspace permission.
MANAGEMENT_TOOL_NAMES = {
    "builtin.skills.list",
    "builtin.skills.read",
    "builtin.skills.install",
    "builtin.skills.create",
    "builtin.skills.write_file",
    "builtin.skills.set_enabled",
    "builtin.skills.delete.request",
    "builtin.mcp.list",
    "builtin.mcp.register",
    "builtin.mcp.update",
    "builtin.mcp.set_enabled",
    "builtin.mcp.delete",
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
    "builtin.skills.announce_usage": {
        "function_name": "lg_skill_used",
        "description": (
            "Announce that you are now following an installed Agent Skill package. "
            "Call this FIRST, before applying a skill's instructions, so the user "
            "sees which skill was triggered in the conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_key": {"type": "string", "minLength": 2, "maxLength": 80},
            },
            "required": ["skill_key"],
            "additionalProperties": False,
        },
    },
    "builtin.skills.list": {
        "function_name": "lg_skills_list",
        "description": "List every installed workspace Skill with its key, kind, enabled state, and origin.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    "builtin.skills.install": {
        "function_name": "lg_skill_install",
        "description": (
            "Install Agent Skills from GitHub or skills.sh — the server-side equivalent of "
            "`npx skills add <source> --skill <name>`. Accepts owner/repo, a github.com URL, "
            "a skills.sh URL, or a full `npx skills add …` command string. Content is fetched "
            "commit-pinned; no npx or shell runs. Installed skills start unauthorized until "
            "enabled via lg_skill_set_enabled or by the user."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "minLength": 3, "maxLength": 1000},
                "skill": {"type": "string", "maxLength": 120},
                "skill_key": {"type": "string", "maxLength": 80},
            },
            "required": ["source"],
            "additionalProperties": False,
        },
    },
    "builtin.skills.create": {
        "function_name": "lg_skill_create",
        "description": (
            "Author a new Agent Skill package from SKILL.md content you write. The package "
            "is stored as workspace files only; scripts never run on the host. The new skill "
            "starts unauthorized until enabled."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_key": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._-]{1,79}$"},
                "name": {"type": "string", "maxLength": 160},
                "skill_md": {"type": "string", "minLength": 20, "maxLength": 100000},
                "files": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "minLength": 1, "maxLength": 500},
                            "contents": {"type": "string", "maxLength": 200000},
                        },
                        "required": ["path", "contents"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["skill_key", "skill_md"],
            "additionalProperties": False,
        },
    },
    "builtin.skills.read": {
        "function_name": "lg_skill_manage_read",
        "description": (
            "Read a Skill package file, including disabled non-official Skills. "
            "Omit path to list the package file tree."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_key": {"type": "string", "minLength": 2, "maxLength": 80},
                "path": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            "required": ["skill_key"],
            "additionalProperties": False,
        },
    },
    "builtin.skills.write_file": {
        "function_name": "lg_skill_write_file",
        "description": (
            "Create or overwrite one file inside an existing non-official Skill package "
            "(e.g. edit SKILL.md). Content changes invalidate the skill's authorization."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_key": {"type": "string", "minLength": 2, "maxLength": 80},
                "path": {"type": "string", "minLength": 1, "maxLength": 500},
                "contents": {"type": "string", "maxLength": 500000},
            },
            "required": ["skill_key", "path", "contents"],
            "additionalProperties": False,
        },
    },
    "builtin.skills.set_enabled": {
        "function_name": "lg_skill_set_enabled",
        "description": (
            "Enable (grant durable authorization) or disable (revoke) an installed Skill. "
            "Enabling injects the skill into future Agent turns; the action is audited."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_key": {"type": "string", "minLength": 2, "maxLength": 80},
                "enabled": {"type": "boolean"},
                "reason": {"type": "string", "maxLength": 500},
            },
            "required": ["skill_key", "enabled"],
            "additionalProperties": False,
        },
    },
    "builtin.skills.delete.request": {
        "function_name": "lg_skill_delete_request",
        "description": (
            "Request permanent deletion of a non-official workspace Skill. "
            "This never deletes by itself: the user must complete the hard-coded "
            "second confirmation and password check in the trusted UI."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_key": {"type": "string", "minLength": 2, "maxLength": 80},
                "reason": {"type": "string", "maxLength": 500},
            },
            "required": ["skill_key"],
            "additionalProperties": False,
        },
    },
    "builtin.mcp.list": {
        "function_name": "lg_mcp_list",
        "description": "List registered MCP servers with transport, endpoint, enabled state, and discovered tools.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    "builtin.mcp.register": {
        "function_name": "lg_mcp_register",
        "description": (
            "Register a remote Streamable HTTP MCP server and probe its capabilities. "
            "Servers needing credentials must be configured by the user in the Extension "
            "Center — never pass secrets here. The server starts unauthorized until enabled."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 160},
                "endpoint_url": {"type": "string", "minLength": 8, "maxLength": 1000},
                "server_key": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._-]{1,79}$"},
                "requested_tools": {
                    "type": "array",
                    "maxItems": 40,
                    "items": {"type": "string", "minLength": 1, "maxLength": 128},
                },
            },
            "required": ["name", "endpoint_url"],
            "additionalProperties": False,
        },
    },
    "builtin.mcp.update": {
        "function_name": "lg_mcp_update",
        "description": (
            "Update a registered MCP server's display name, endpoint, requested tools, "
            "or execution limits. Credentials cannot be read or supplied through this tool. "
            "A security-relevant change revokes the current authorization until re-enabled."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "minLength": 2,
                    "maxLength": 80,
                    "description": "server_key or server id",
                },
                "name": {"type": "string", "minLength": 1, "maxLength": 160},
                "endpoint_url": {"type": "string", "minLength": 8, "maxLength": 1000},
                "requested_tools": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 100,
                    "items": {"type": "string", "minLength": 1, "maxLength": 128},
                },
                "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 30000},
                "max_input_bytes": {
                    "type": "integer",
                    "minimum": 1024,
                    "maximum": 262144,
                },
                "max_result_bytes": {
                    "type": "integer",
                    "minimum": 1024,
                    "maximum": 1048576,
                },
                "max_concurrency": {"type": "integer", "minimum": 1, "maximum": 8},
            },
            "required": ["server"],
            "additionalProperties": False,
        },
    },
    "builtin.mcp.set_enabled": {
        "function_name": "lg_mcp_set_enabled",
        "description": (
            "Enable (durable authorization + agent auto-invoke) or disable (revoke) a "
            "registered MCP server. Enabling refreshes its capability snapshot first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "minLength": 2, "maxLength": 80,
                            "description": "server_key or server id"},
                "enabled": {"type": "boolean"},
                "reason": {"type": "string", "maxLength": 500},
            },
            "required": ["server", "enabled"],
            "additionalProperties": False,
        },
    },
    "builtin.mcp.delete": {
        "function_name": "lg_mcp_delete",
        "description": "Permanently delete a registered MCP server, its credentials, snapshots, and grants.",
        "parameters": {
            "type": "object",
            "properties": {
                "server": {"type": "string", "minLength": 2, "maxLength": 80},
                "reason": {"type": "string", "maxLength": 500},
            },
            "required": ["server"],
            "additionalProperties": False,
        },
    },
}
SKILL_MAX_INPUT_BYTES = 64 * 1024
SKILL_MAX_RESULT_BYTES = 256 * 1024
# Progressive-disclosure reader for Agent Skill packages (fixed name — hashed
# per-skill names are always exactly ``lg_skill_`` + 20 hex chars).
SKILL_READ_FUNCTION_NAME = "lg_skill_read"
SKILL_READ_MAX_CONTENT_CHARS = 48_000


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

    def _authorized_packages(self) -> list[SkillRecord]:
        """Enabled file-package skills holding a durable ``always`` grant."""

        result: list[SkillRecord] = []
        for skill in self.list_skills():
            if not skill.enabled or skill.status != "enabled":
                continue
            if not self._is_agent_skill_package(skill):
                continue
            grant = self._usable_grant(
                "skill", skill.id, self._skill_authorization_hash(skill)
            )
            if grant is None or grant.decision != "always":
                continue
            result.append(skill)
        return result

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

        self_service_enabled = bool(
            getattr(self.settings, "agent_extension_self_service_enabled", True)
        )
        definitions = [
            {
                "type": "function",
                "function": {
                    "name": spec["function_name"],
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                },
            }
            for tool, spec in BUILTIN_TOOL_SPECS.items()
            if self_service_enabled or tool not in MANAGEMENT_TOOL_NAMES
        ]
        if self._authorized_packages():
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": SKILL_READ_FUNCTION_NAME,
                        "description": (
                            "Read the full SKILL.md instructions or a bundled file of an "
                            "authorized LearnGraph Agent Skill package. Use this when a "
                            "skill listed in the skill catalog matches the current task "
                            "and you need its complete instructions before applying it, "
                            "or to load its references/ files on demand."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "skill_key": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 80,
                                    "description": "The skill_key shown in the skill catalog.",
                                },
                                "path": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 500,
                                    "description": "Package-relative file path; defaults to SKILL.md.",
                                },
                            },
                            "required": ["skill_key"],
                            "additionalProperties": False,
                        },
                    },
                }
            )
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

        activated = activated_skill_keys or set()
        inline_limit = max(500, int(getattr(self.settings, "skill_prompt_inline_char_limit", 4_000)))
        total_budget = max(2_000, int(getattr(self.settings, "skill_prompt_total_char_budget", 16_000)))
        catalog_max = max(1, int(getattr(self.settings, "skill_prompt_catalog_max_entries", 24)))
        sections: list[str] = []
        catalog_lines: list[str] = []
        used_budget = 0
        skills = sorted(
            self._authorized_packages(),
            key=lambda skill: (skill.skill_key not in activated, skill.created_at, skill.id),
        )
        for skill in skills:
            # Contextual official skills are installed durably but injected only
            # when the user explicitly activates the matching composer mode.
            if (
                is_official_skill_record(skill)
                and skill.skill_key in CONTEXTUAL_OFFICIAL_SKILL_KEYS
                and skill.skill_key not in activated
            ):
                continue
            instructions = (skill.instructions_markdown or "").strip()
            description = ""
            if isinstance(skill.manifest_json, dict):
                raw = skill.manifest_json.get("description")
                if isinstance(raw, str):
                    description = raw.strip()
            if not instructions:
                # Fall back to the package description when the body is empty.
                instructions = description
            if not instructions:
                continue
            is_activated = skill.skill_key in activated
            # Progressive disclosure: inline small bodies within budget, list
            # the rest as catalog entries the model expands via lg_skill_read.
            inline = is_activated or (
                len(instructions) <= inline_limit
                and used_budget + len(instructions) <= total_budget
            )
            if inline:
                body = instructions[:8_000]
                if len(instructions) > 8_000:
                    body = f"{body}\n…(truncated)"
                used_budget += len(body)
                sections.append(
                    f"### Skill: {skill.name} (`{skill.skill_key}`)\n"
                    f"skill_id: {skill.id}\n"
                    f"source: {skill.source}\n"
                    f"version: {skill.version}\n\n"
                    f"{body}"
                )
            elif len(catalog_lines) < catalog_max:
                summary = " ".join((description or instructions).split())[:200]
                scripts_note = " · bundled scripts (sandbox-only)" if skill.has_scripts else ""
                catalog_lines.append(
                    f"- `{skill.skill_key}` · {skill.name}: {summary}{scripts_note}"
                )
        if not sections and not catalog_lines:
            return ""
        parts: list[str] = [
            "Authorized LearnGraph Agent Skill packages for this turn. "
            "Treat each block as optional skill instructions when the user's "
            "request matches its scope. When you decide to follow one of these "
            "skills, FIRST call the `lg_skill_used` function with its `skill_key` "
            "so the user sees which skill was triggered, then apply its "
            "instructions. Follow the instructions; do not invent "
            "host-side scripts or claim you executed package scripts unless a "
            "sandbox tool result confirms it. Package scripts are never automatic "
            "tools."
        ]
        if sections:
            parts.append("\n\n".join(sections))
        if catalog_lines:
            parts.append(
                "### Skill catalog (load on demand)\n"
                "These additional authorized skills are available but not "
                "inlined. When the user's request matches one, call the "
                "`lg_skill_read` tool with its `skill_key` to load the full "
                "SKILL.md instructions (and `path` for bundled reference "
                "files) before applying it. Do not guess a skill's content "
                "from its one-line summary.\n"
                + "\n".join(catalog_lines)
            )
        return "\n\n".join(parts)

    def read_skill_package_file(self, arguments: dict[str, Any]) -> ExtensionInvocation:
        """Serve one authorized package file to the Agent (``lg_skill_read``)."""

        skill_key = str(arguments.get("skill_key") or "").strip()
        rel_path = str(arguments.get("path") or "SKILL.md").strip() or "SKILL.md"
        input_json = {"skill_key": skill_key, "path": rel_path}
        invocation = self._create_invocation(
            target_type="skill",
            target_id=skill_key or "unknown",
            tool_name="skill.read",
            input_json=input_json,
            input_size=len(_canonical_bytes(input_json)),
            timeout_ms=0,
        )
        invocation.status = "running"
        invocation.started_at = utc_now()
        try:
            skill = next(
                (s for s in self._authorized_packages() if s.skill_key == skill_key),
                None,
            )
            if skill is None:
                raise AppError(
                    404,
                    "skill_not_found",
                    "No authorized Agent Skill package matches this skill_key",
                )
            invocation.skill_id = skill.id
            invocation.target_id = skill.id
            from app.services.skill_package import SkillPackageService

            package = SkillPackageService(
                self.db, self.workspace_id, self.actor_id, self.settings
            )
            file_view = package.read_file(skill.id, rel_path)
            content = file_view.content
            if len(content) > 48_000:
                content = f"{content[:48_000]}\n…(truncated)"
            result = {
                "skill_key": skill.skill_key,
                "skill_name": skill.name,
                "skill_id": skill.id,
                "path": file_view.relative_path,
                "mime_type": file_view.mime_type,
                "content": content,
            }
            result_bytes = _canonical_bytes(result)
            invocation.status = "succeeded"
            invocation.result_json = result
            invocation.result_size_bytes = len(result_bytes)
            invocation.result_hash = hashlib.sha256(result_bytes).hexdigest()
            invocation.finished_at = utc_now()
            self.audit.record(
                actor_id=self.actor_id,
                action="skill.package.read",
                resource_type="skill",
                resource_id=skill.id,
                details={
                    "path": file_view.relative_path,
                    "invocation_id": invocation.id,
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

    @staticmethod
    def _skill_trigger_payload(skill: SkillRecord, origin: str) -> dict[str, Any]:
        return {
            "skill_key": skill.skill_key,
            "skill_name": skill.name,
            "skill_id": skill.id,
            "origin": origin,
        }

    def invoke_agent_function(
        self,
        function_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch a function exposed by :meth:`agent_tool_definitions`."""

        if function_name == SKILL_READ_FUNCTION_NAME:
            data = self._invocation_data(self.read_skill_package_file(arguments))
            result = data.get("result") or {}
            if data.get("status") == "succeeded" and result.get("skill_key"):
                data["skill_trigger"] = {
                    "skill_key": result.get("skill_key"),
                    "skill_name": result.get("skill_name") or result.get("skill_key"),
                    "skill_id": result.get("skill_id"),
                    "origin": "catalog_read",
                }
            return data
        for tool_name, spec in BUILTIN_TOOL_SPECS.items():
            if spec["function_name"] == function_name:
                data = self._invocation_data(
                    self.invoke_builtin_tool(tool_name, arguments)
                )
                if (
                    tool_name == "builtin.skills.announce_usage"
                    and data.get("status") == "succeeded"
                ):
                    result = data.get("result") or {}
                    if result.get("skill_key"):
                        data["skill_trigger"] = {
                            "skill_key": result.get("skill_key"),
                            "skill_name": result.get("skill_name")
                            or result.get("skill_key"),
                            "skill_id": result.get("skill_id"),
                            "origin": "package_instruction",
                        }
                return data
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
                data = self._invocation_data(
                    self.invoke_skill(skill.id, SkillInvokeRequest(input=arguments))
                )
                data["skill_trigger"] = self._skill_trigger_payload(
                    skill, "declarative_invoke"
                )
                return data
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

    def read_skill_package_file(self, arguments: dict[str, Any]) -> ExtensionInvocation:
        """Progressive disclosure: return package file text for an authorized skill.

        Read-only counterpart to the prompt catalog — the model calls this to
        expand a catalog entry into full SKILL.md instructions (or bundled
        reference files).  Requires the same durable ``always`` grant as prompt
        injection; every read is recorded as an ExtensionInvocation.
        """

        skill_key = str(arguments.get("skill_key") or "").strip()
        raw_path = str(arguments.get("path") or "SKILL.md").strip() or "SKILL.md"
        input_json = {"skill_key": skill_key, "path": raw_path}
        invocation = self._create_invocation(
            target_type="skill",
            target_id="",
            tool_name="skill.read",
            input_json=input_json,
            input_size=len(_canonical_bytes(input_json)),
            timeout_ms=0,
        )
        invocation.started_at = utc_now()
        if not skill_key:
            self._fail_invocation(
                invocation,
                "skill_key_required",
                "skill_key is required",
                status="failed",
                http_status=400,
            )
        skill = next(
            (item for item in self.list_skills() if item.skill_key == skill_key),
            None,
        )
        if skill is None or not self._is_agent_skill_package(skill):
            self._fail_invocation(
                invocation,
                "skill_not_found",
                f"No authorized Agent Skill package with key `{skill_key}`",
                status="failed",
                http_status=404,
            )
        invocation.target_id = skill.id
        invocation.skill_id = skill.id
        auth_hash = self._skill_authorization_hash(skill)
        grant = self._usable_grant("skill", skill.id, auth_hash)
        if (
            not skill.enabled
            or skill.status != "enabled"
            or grant is None
            or grant.decision != "always"
        ):
            self._fail_invocation(
                invocation,
                "agent_tool_not_authorized",
                "The requested skill is not enabled with durable authorization",
                status="denied",
                http_status=403,
            )
        try:
            path = normalize_skill_relative_path(raw_path)
        except AppError as exc:
            self._fail_invocation(
                invocation,
                exc.code,
                exc.message,
                status="failed",
                http_status=exc.status_code,
            )
        row = self.db.scalar(
            select(SkillPackageFile).where(
                SkillPackageFile.workspace_id == self.workspace_id,
                SkillPackageFile.skill_id == skill.id,
                SkillPackageFile.relative_path == path,
                SkillPackageFile.is_directory.is_(False),
            )
        )
        if row is None:
            self._fail_invocation(
                invocation,
                "skill_file_not_found",
                f"File `{path}` does not exist in this skill package",
                status="failed",
                http_status=404,
            )
        from app.services.session_workspace import BlobStore

        try:
            data = BlobStore(self.db, self.workspace_id, self.settings).read_bytes(
                row.blob_sha256, limit_bytes=MAX_SKILL_FILE_BYTES
            )
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            self._fail_invocation(
                invocation,
                "skill_file_not_text",
                "Only UTF-8 text files can be read by the agent",
                status="failed",
                http_status=415,
            )
        except AppError as exc:
            self._fail_invocation(
                invocation,
                exc.code,
                exc.message,
                status="failed",
                http_status=exc.status_code,
            )
        truncated = len(content) > SKILL_READ_MAX_CONTENT_CHARS
        if truncated:
            content = content[:SKILL_READ_MAX_CONTENT_CHARS]
        files = [
            item.relative_path
            for item in self.db.scalars(
                select(SkillPackageFile).where(
                    SkillPackageFile.workspace_id == self.workspace_id,
                    SkillPackageFile.skill_id == skill.id,
                    SkillPackageFile.is_directory.is_(False),
                ).order_by(SkillPackageFile.relative_path)
            ).all()
        ]
        result = {
            "skill_key": skill.skill_key,
            "name": skill.name,
            "path": path,
            "content": content,
            "truncated": truncated,
            "package_files": files[:64],
        }
        invocation.status = "succeeded"
        invocation.result_json = result
        invocation.result_size_bytes = len(_canonical_bytes(result))
        invocation.result_hash = _hash(result)
        invocation.grant_id = grant.id
        invocation.authorization_hash = auth_hash
        invocation.finished_at = utc_now()
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.read",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "skill_key": skill.skill_key,
                "path": path,
                "truncated": truncated,
                "content_chars": len(content),
            },
        )
        self.db.commit()
        self.db.refresh(invocation)
        return invocation

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

    def delete_server(self, server_id: str, reason: str = "workspace_user_deleted") -> None:
        """Permanently remove an MCP server, its credentials, snapshots, and grants."""

        server = self.require_server(server_id)
        server_key = server.server_key
        display_name = server.display_name
        self._supersede_grants("mcp_server", server.id, revoked=True)
        # Break the current-snapshot reference before deleting snapshot rows.
        server.current_snapshot_id = None
        self.db.flush()
        for credential in self.db.scalars(
            self.credentials.query().where(MCPServerCredential.server_id == server.id)
        ).all():
            self.db.delete(credential)
        snapshot_count = 0
        for snapshot in self.db.scalars(
            self.snapshots.query().where(MCPCapabilitySnapshot.server_id == server.id)
        ).all():
            self.db.delete(snapshot)
            snapshot_count += 1
        self.audit.record(
            actor_id=self.actor_id,
            action="mcp.server_delete",
            resource_type="mcp_server",
            resource_id=server.id,
            details={
                "reason": (reason or "")[:1000],
                "server_key": server_key,
                "display_name": display_name,
                "snapshot_count": snapshot_count,
            },
        )
        self.servers.delete(server)
        self.db.commit()

    def list_skills(self) -> list[SkillRecord]:
        return list(
            self.db.scalars(
                self.skills.query().order_by(SkillRecord.created_at, SkillRecord.id)
            ).all()
        )

    def require_skill(self, skill_id: str) -> SkillRecord:
        return self.skills.require(skill_id, "Skill")

    def _resolve_skill_by_key(self, reference: str) -> SkillRecord:
        value = (reference or "").strip()
        if value:
            for skill in self.list_skills():
                if skill.skill_key == value or skill.id == value:
                    return skill
        raise AppError(404, "skill_not_found", f"No skill matches '{value[:80]}'")

    def _resolve_server_reference(self, reference: str) -> MCPServer:
        value = (reference or "").strip()
        if value:
            for server in self.list_servers():
                if server.server_key == value or server.id == value:
                    return server
        raise AppError(404, "mcp_server_not_found", f"No MCP server matches '{value[:80]}'")

    @staticmethod
    def _require_not_official_skill(skill: SkillRecord) -> None:
        if is_official_skill_record(skill):
            raise AppError(
                403,
                "official_skill_protected",
                "Official LearnGraph skills are managed by the system and cannot be modified or removed",
            )

    def create_skill(self, payload: SkillCreateRequest) -> SkillRecord:
        assert_skill_identity_not_reserved(payload.skill_key, payload.source)
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
        self._require_not_official_skill(skill)
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
        self._require_not_official_skill(skill)
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

    def request_skill_deletion(
        self,
        skill_id: str,
        reason: str = "workspace_user_requested",
    ) -> SkillDeleteConfirmation:
        """Create an inert request. No Agent-callable path can confirm it."""

        skill = self.require_skill(skill_id)
        self._require_not_official_skill(skill)
        now = utc_now()
        for pending in self.db.scalars(
            select(SkillDeleteConfirmation).where(
                SkillDeleteConfirmation.workspace_id == self.workspace_id,
                SkillDeleteConfirmation.skill_id == skill.id,
                SkillDeleteConfirmation.status == "pending",
            )
        ).all():
            pending.status = "superseded"
        confirmation = SkillDeleteConfirmation(
            workspace_id=self.workspace_id,
            skill_id=skill.id,
            skill_key=skill.skill_key,
            skill_name=skill.name,
            requested_by=self.actor_id,
            required_user_id=self.actor_id,
            reason=(reason or "")[:1000],
            status="pending",
            expires_at=now + timedelta(minutes=10),
        )
        self.db.add(confirmation)
        self.audit.record(
            actor_id=self.actor_id,
            action="skill.delete.requested",
            resource_type="skill",
            resource_id=skill.id,
            details={
                "skill_key": skill.skill_key,
                "confirmation_required": True,
                "expires_at": confirmation.expires_at.isoformat(),
            },
        )
        self.db.commit()
        self.db.refresh(confirmation)
        return confirmation

    def confirm_skill_deletion(
        self,
        confirmation_id: str,
        *,
        confirmation_text: str,
        current_password: str,
        principal: Principal,
    ) -> SkillDeleteConfirmation:
        """User-only hard gate: typed name + current-password reauthentication."""

        confirmation = self.db.scalar(
            select(SkillDeleteConfirmation).where(
                SkillDeleteConfirmation.id == confirmation_id,
                SkillDeleteConfirmation.workspace_id == self.workspace_id,
            )
        )
        if confirmation is None:
            raise AppError(
                404,
                "skill_delete_confirmation_not_found",
                "Skill deletion confirmation was not found",
            )
        if confirmation.required_user_id != principal.user_id:
            raise AppError(
                403,
                "skill_delete_confirmation_user_mismatch",
                "Only the user who received the second confirmation may complete it",
            )
        now = utc_now()
        expires_at = confirmation.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if confirmation.status != "pending" or expires_at <= now:
            if confirmation.status == "pending":
                confirmation.status = "expired"
                self.db.commit()
            raise AppError(
                409,
                "skill_delete_confirmation_expired",
                "Skill deletion confirmation is no longer active",
            )
        if confirmation_text.strip() != confirmation.skill_name:
            raise AppError(
                409,
                "skill_delete_confirmation_mismatch",
                "Confirmation text must exactly match the Skill name",
            )
        user = self.db.scalar(
            select(User).where(
                User.id == principal.user_id,
                User.tenant_id == principal.tenant_id,
                User.status == "active",
            )
        )
        if user is None or not verify_password(current_password, user.password_hash):
            raise AppError(
                401,
                "invalid_credentials",
                "Current password is incorrect",
            )
        # Re-resolve immediately before deletion so a stale request cannot target
        # a replacement record with the same display name.
        skill = self.require_skill(confirmation.skill_id)
        if (
            skill.skill_key != confirmation.skill_key
            or skill.name != confirmation.skill_name
        ):
            raise AppError(
                409,
                "skill_delete_target_changed",
                "The Skill changed after confirmation was requested",
            )
        confirmation.status = "confirmed"
        confirmation.confirmed_at = now
        self.audit.record(
            actor_id=principal.user_id,
            action="skill.delete.confirmed_by_user",
            resource_type="skill",
            resource_id=skill.id,
            details={"confirmation_id": confirmation.id, "skill_key": skill.skill_key},
        )
        self.delete_skill(skill.id, confirmation.reason or "confirmed_by_user")
        self.db.refresh(confirmation)
        return confirmation

    def delete_skill(self, skill_id: str, reason: str = "workspace_user_deleted") -> None:
        """Permanently remove a skill package/record and its workspace-local files."""

        skill = self.require_skill(skill_id)
        self._require_not_official_skill(skill)
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

        if tool_name in MANAGEMENT_TOOL_NAMES and not bool(
            getattr(self.settings, "agent_extension_self_service_enabled", True)
        ):
            raise AppError(
                403,
                "agent_self_service_disabled",
                "Agent extension self-service is disabled by workspace settings",
            )

        if tool_name == "builtin.skills.announce_usage":
            skill = self._resolve_skill_by_key(str(arguments["skill_key"]))
            if not skill.enabled or skill.status != "enabled":
                raise AppError(403, "skill_not_enabled", "该 Skill 未启用或未授权")
            return {
                "skill_key": skill.skill_key,
                "skill_name": skill.name,
                "skill_id": skill.id,
                "kind": skill.kind,
                "acknowledged": True,
            }

        if tool_name == "builtin.skills.list":
            self._require_workspace_permission(authz, workspace, "workspace.read")
            skills = self.list_skills()
            return {
                "skills": [
                    {
                        "skill_key": item.skill_key,
                        "name": item.name,
                        "kind": item.kind,
                        "package_format": item.package_format,
                        "enabled": item.enabled,
                        "status": item.status,
                        "version": item.version,
                        "source": item.source,
                        "origin_type": item.origin_type,
                        "is_official": bool(item.is_official),
                        "has_scripts": bool(item.has_scripts),
                    }
                    for item in skills
                ],
                "count": len(skills),
            }

        if tool_name == "builtin.skills.read":
            self._require_workspace_permission(authz, workspace, "workspace.read")
            from app.services.skill_package import SkillPackageService

            skill = self._resolve_skill_by_key(str(arguments["skill_key"]))
            package = SkillPackageService(
                self.db,
                self.workspace_id,
                self.actor_id,
                self.settings,
            )
            path = str(arguments.get("path") or "").strip()
            if path:
                return package.read_file(skill.id, path).model_dump(mode="json")
            return package.list_files(skill.id).model_dump(mode="json")

        if tool_name == "builtin.skills.install":
            self._require_workspace_permission(authz, workspace, "workspace.write")
            from app.domain.schemas.extensions import SkillNpxImportRequest
            from app.services.skill_github_import import SkillGitHubImportService

            raw_key = str(arguments.get("skill_key") or "").strip() or None
            if raw_key and not re.match(r"^[a-z0-9][a-z0-9._-]{1,79}$", raw_key):
                raise AppError(422, "invalid_tool_arguments", "skill_key format is invalid")
            skill_filter = str(arguments.get("skill") or "").strip()
            importer = SkillGitHubImportService(
                self.db, self.workspace_id, self.actor_id, self.settings
            )
            response = importer.install_from_command(
                SkillNpxImportRequest(
                    command=str(arguments["source"]).strip(), skill_key=raw_key
                ),
                extra_skills=[skill_filter] if skill_filter else [],
            )
            return {
                "reference": response.reference,
                "owner": response.owner,
                "repo": response.repo,
                "commit": response.commit,
                "requested_skills": response.requested_skills,
                "installed": [
                    {
                        "skill_id": view.id,
                        "skill_key": view.skill_key,
                        "name": view.name,
                        "status": view.status,
                        "enabled": view.enabled,
                    }
                    for view in response.installed
                ],
                "skipped": [item.model_dump() for item in response.skipped],
                "note": "新安装的 Skill 需授权后才会注入 Agent；可调用 lg_skill_set_enabled 启用。",
            }

        if tool_name == "builtin.skills.create":
            self._require_workspace_permission(authz, workspace, "workspace.write")
            from pydantic import ValidationError as PydanticValidationError

            from app.domain.schemas.extensions import (
                SkillManualImportFile,
                SkillManualImportRequest,
            )
            from app.services.skill_market import SkillMarketService

            files = [
                SkillManualImportFile(path="SKILL.md", contents=str(arguments["skill_md"]))
            ]
            for item in arguments.get("files") or []:
                files.append(
                    SkillManualImportFile(
                        path=str(item.get("path") or ""),
                        contents=str(item.get("contents") or ""),
                    )
                )
            try:
                request = SkillManualImportRequest(
                    skill_key=str(arguments["skill_key"]),
                    name=str(arguments.get("name") or "").strip() or None,
                    source="agent_authored",
                    version="1.0.0",
                    files=files,
                )
            except PydanticValidationError as exc:
                raise AppError(422, "invalid_tool_arguments", str(exc)[:500]) from exc
            skill = SkillMarketService(
                self.db, self.workspace_id, self.actor_id, self.settings
            ).import_manual(request, origin_type="agent_authored", origin_ref="agent_tool")
            return {
                "skill_id": skill.id,
                "skill_key": skill.skill_key,
                "name": skill.name,
                "status": skill.status,
                "enabled": skill.enabled,
                "note": "Skill 已创建但未授权；调用 lg_skill_set_enabled 或让用户在扩展中心启用。",
            }

        if tool_name == "builtin.skills.write_file":
            self._require_workspace_permission(authz, workspace, "workspace.write")
            from app.domain.schemas.extensions import SkillFileWriteRequest
            from app.services.skill_package import SkillPackageService

            skill = self._resolve_skill_by_key(str(arguments["skill_key"]))
            package = SkillPackageService(
                self.db, self.workspace_id, self.actor_id, self.settings
            )
            updated, file_view, reauth = package.write_file(
                skill.id,
                str(arguments["path"]),
                SkillFileWriteRequest(content=str(arguments["contents"])),
            )
            return {
                "skill_key": updated.skill_key,
                "path": file_view.relative_path,
                "size_bytes": file_view.size_bytes,
                "content_hash": updated.content_hash,
                "reauthorization_required": reauth,
                "note": (
                    "内容已更新；授权已失效，请调用 lg_skill_set_enabled 重新启用。"
                    if reauth
                    else "内容已更新。"
                ),
            }

        if tool_name == "builtin.skills.set_enabled":
            self._require_workspace_permission(authz, workspace, "workspace.write")
            skill = self._resolve_skill_by_key(str(arguments["skill_key"]))
            reason = str(arguments.get("reason") or "agent_self_service")[:500]
            if bool(arguments["enabled"]):
                grant = self.authorize_skill(
                    skill.id,
                    PermissionDecisionRequest(decision="always", reason=reason),
                )
                self.db.refresh(skill)
                return {
                    "skill_key": skill.skill_key,
                    "enabled": skill.enabled,
                    "status": skill.status,
                    "grant_id": grant.id,
                }
            updated = self.revoke_skill(skill.id, reason)
            return {
                "skill_key": updated.skill_key,
                "enabled": updated.enabled,
                "status": updated.status,
            }

        if tool_name == "builtin.skills.delete.request":
            self._require_workspace_permission(authz, workspace, "workspace.write")
            skill = self._resolve_skill_by_key(str(arguments["skill_key"]))
            confirmation = self.request_skill_deletion(
                skill.id,
                str(arguments.get("reason") or "agent_self_service"),
            )
            return {
                "deleted": False,
                "confirmation_required": True,
                "confirmation_id": confirmation.id,
                "skill_id": confirmation.skill_id,
                "skill_key": confirmation.skill_key,
                "skill_name": confirmation.skill_name,
                "expires_at": confirmation.expires_at.isoformat(),
                "message": (
                    "The Skill has not been deleted. The user must personally "
                    "complete the second confirmation in the trusted UI."
                ),
            }

        if tool_name == "builtin.mcp.list":
            self._require_workspace_permission(authz, workspace, "workspace.read")
            servers = []
            for server in self.list_servers():
                snapshot = self._current_snapshot(server)
                servers.append(
                    {
                        "server_key": server.server_key,
                        "display_name": server.display_name,
                        "transport": server.transport,
                        "endpoint_url": server.endpoint_url,
                        "enabled": server.enabled,
                        "status": server.status,
                        "agent_auto_invoke": server.agent_auto_invoke,
                        "requested_tools": list(server.requested_tools or []),
                        "discovered_tools": [
                            str(tool.get("name") or "")
                            for tool in (snapshot.tools if snapshot else [])
                        ][:40],
                        "last_error": server.last_error,
                    }
                )
            return {"servers": servers, "count": len(servers)}

        if tool_name == "builtin.mcp.register":
            self._require_workspace_permission(authz, workspace, "workspace.write")
            from pydantic import ValidationError as PydanticValidationError

            from app.domain.schemas.extensions import MCPServerManifest

            name = str(arguments["name"]).strip()
            endpoint = str(arguments["endpoint_url"]).strip()
            requested = [
                str(item).strip()
                for item in (arguments.get("requested_tools") or [])
                if str(item).strip()
            ]
            server_key = str(arguments.get("server_key") or "").strip()
            if not server_key:
                slug = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-")[:60]
                server_key = (
                    slug
                    if re.match(r"^[a-z0-9][a-z0-9._-]{1,79}$", slug)
                    else f"mcp-{hashlib.sha256(endpoint.encode('utf-8')).hexdigest()[:10]}"
                )
            try:
                create_payload = MCPServerCreateRequest(
                    server_key=server_key,
                    display_name=name,
                    source="agent_registered",
                    version="1.0.0",
                    transport="streamable_http",
                    endpoint_url=endpoint,
                    manifest=MCPServerManifest(
                        identity=name,
                        requested_tools=requested or ["pending-discovery"],
                        permissions=["network"],
                    ),
                    agent_auto_invoke=True,
                )
            except PydanticValidationError as exc:
                raise AppError(422, "invalid_tool_arguments", str(exc)[:500]) from exc
            server = self.create_server(create_payload)
            discovered: list[str] = []
            probe_error: str | None = None
            try:
                snapshot = self.refresh_server(server.id)
                discovered = [
                    str(tool.get("name") or "")
                    for tool in snapshot.tools
                    if tool.get("name")
                ][:40]
                if not requested and discovered:
                    server = self.require_server(server.id)
                    server.requested_tools = discovered
                    manifest_json = dict(server.manifest_json or {})
                    manifest_json["requested_tools"] = discovered
                    server.manifest_json = manifest_json
                    self.db.commit()
            except AppError as exc:
                probe_error = f"{exc.code}: {exc.message}"
            return {
                "server_key": server_key,
                "server_id": server.id,
                "endpoint_url": endpoint,
                "discovered_tools": discovered,
                "probe_error": probe_error,
                "note": "已登记；调用 lg_mcp_set_enabled 授权启用，或由用户在扩展中心确认。",
            }

        if tool_name == "builtin.mcp.update":
            self._require_workspace_permission(authz, workspace, "workspace.write")
            from pydantic import ValidationError as PydanticValidationError

            from app.domain.schemas.extensions import MCPServerManifest

            server = self._resolve_server_reference(str(arguments["server"]))
            current_manifest = dict(server.manifest_json or {})
            requested_tools = arguments.get("requested_tools")
            if requested_tools is None:
                requested_tools = list(server.requested_tools or [])
            normalized_tools = [
                str(item).strip()
                for item in requested_tools
                if str(item).strip()
            ]
            try:
                manifest = MCPServerManifest(
                    schema_version="1.0",
                    identity=str(
                        arguments.get("name")
                        or current_manifest.get("identity")
                        or server.display_name
                    ),
                    requested_tools=normalized_tools,
                    permissions=list(current_manifest.get("permissions") or []),
                    requested_resources=list(
                        current_manifest.get("requested_resources") or []
                    ),
                    requested_prompts=list(
                        current_manifest.get("requested_prompts") or []
                    ),
                )
                payload = MCPServerUpdateRequest(
                    display_name=(
                        str(arguments["name"]).strip()
                        if arguments.get("name")
                        else None
                    ),
                    source=server.source,
                    version=server.version,
                    endpoint_url=(
                        str(arguments["endpoint_url"]).strip()
                        if arguments.get("endpoint_url")
                        else None
                    ),
                    manifest=manifest,
                    agent_auto_invoke=server.agent_auto_invoke,
                    timeout_ms=arguments.get("timeout_ms"),
                    max_input_bytes=arguments.get("max_input_bytes"),
                    max_result_bytes=arguments.get("max_result_bytes"),
                    max_concurrency=arguments.get("max_concurrency"),
                )
            except PydanticValidationError as exc:
                raise AppError(
                    422, "invalid_tool_arguments", str(exc)[:500]
                ) from exc
            updated = self.update_server(server.id, payload)
            return {
                "server_key": updated.server_key,
                "display_name": updated.display_name,
                "endpoint_url": updated.endpoint_url,
                "requested_tools": list(updated.requested_tools or []),
                "enabled": updated.enabled,
                "status": updated.status,
                "authorization_invalidated": not updated.enabled,
                "credentials_changed": False,
            }

        if tool_name == "builtin.mcp.set_enabled":
            self._require_workspace_permission(authz, workspace, "workspace.write")
            server = self._resolve_server_reference(str(arguments["server"]))
            reason = str(arguments.get("reason") or "agent_self_service")[:500]
            if bool(arguments["enabled"]):
                if self._current_snapshot(server) is None:
                    self.refresh_server(server.id)
                    server = self.require_server(server.id)
                if not server.agent_auto_invoke:
                    server.agent_auto_invoke = True
                grant = self.authorize_server(
                    server.id,
                    PermissionDecisionRequest(decision="always", reason=reason),
                )
                self.db.refresh(server)
                return {
                    "server_key": server.server_key,
                    "enabled": server.enabled,
                    "status": server.status,
                    "agent_auto_invoke": server.agent_auto_invoke,
                    "grant_id": grant.id,
                }
            updated = self.revoke_server(server.id, reason)
            return {
                "server_key": updated.server_key,
                "enabled": updated.enabled,
                "status": updated.status,
            }

        if tool_name == "builtin.mcp.delete":
            self._require_workspace_permission(authz, workspace, "workspace.write")
            server = self._resolve_server_reference(str(arguments["server"]))
            deleted_key = server.server_key
            self.delete_server(
                server.id, str(arguments.get("reason") or "agent_self_service")
            )
            return {"deleted": True, "server_key": deleted_key}

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
