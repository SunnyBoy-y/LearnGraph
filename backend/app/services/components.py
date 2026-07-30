from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from jsonschema import ValidationError
from jsonschema.validators import validator_for
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import (
    ComponentAuthorization,
    ComponentCheckRecord,
    ComponentManifestVersion,
    PluginRecord,
)
from app.domain.schemas.components import (
    ComponentArtifactRequest,
    ComponentArtifactView,
    ComponentAuthorizationRequest,
    ComponentAuthorizationRevokeRequest,
    ComponentCheckRequest,
    ComponentEventValidationRequest,
    ComponentEventValidationView,
    ComponentManifestImportRequest,
)
from app.repositories.audit import AuditRepository
from app.repositories.components import (
    ComponentAuthorizationRepository,
    ComponentCheckRepository,
    ComponentManifestRepository,
)
from app.repositories.domain import PluginRepository


BUILTIN_COMPONENT_IDS = frozenset(
    {
        "weather_card",
        "metric_card",
        "option_group",
        "single_choice",
        "multiple_choice",
        "fill_blank",
        "short_answer_table",
        "image_frame",
    }
)
MAX_SCHEMA_BYTES = 64 * 1024
MAX_INSTANCE_BYTES = 64 * 1024
MAX_SCHEMA_DEPTH = 16
MAX_SCHEMA_NODES = 1_000
FORBIDDEN_CONTENT_MARKERS = (
    "<script",
    "</script",
    "javascript:",
    "<iframe",
    "srcdoc=",
    "dangerouslysetinnerhtml",
)
FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "html",
        "raw_html",
        "javascript",
        "script",
        "srcdoc",
        "react_code",
        "dangerouslysetinnerhtml",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical_json(value)
    return hashlib.sha256(payload).hexdigest()


def _flatten_permissions(permissions: dict[str, Any]) -> list[str]:
    flattened = [
        f"network:{domain}"
        for domain in permissions.get("network_domains", [])
    ]
    if permissions.get("file_read"):
        flattened.append("file_read")
    if permissions.get("clipboard_write"):
        flattened.append("clipboard_write")
    flattened.extend(
        f"message:{action}" for action in permissions.get("message_actions", [])
    )
    return flattened


def _schema_guard(schema: dict[str, Any], *, label: str) -> None:
    encoded = _canonical_json(schema)
    if len(encoded) > MAX_SCHEMA_BYTES:
        raise AppError(
            422,
            "component_schema_too_large",
            f"{label} exceeds the {MAX_SCHEMA_BYTES}-byte schema limit",
        )
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise AppError(
            422,
            "component_schema_unsafe",
            f"{label} must be a closed top-level object schema",
        )

    seen = 0

    def visit(value: Any, *, path: str, depth: int) -> None:
        nonlocal seen
        seen += 1
        if depth > MAX_SCHEMA_DEPTH or seen > MAX_SCHEMA_NODES:
            raise AppError(
                422,
                "component_schema_too_complex",
                f"{label} exceeds bounded depth or node limits",
            )
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and not reference.startswith("#/"):
                raise AppError(
                    422,
                    "component_schema_external_ref_forbidden",
                    f"{label} cannot resolve remote or file schema references",
                )
            media_type = value.get("contentMediaType")
            if isinstance(media_type, str) and media_type.casefold() in {
                "text/html",
                "application/javascript",
                "text/javascript",
            }:
                raise AppError(
                    422,
                    "component_schema_executable_content_forbidden",
                    f"{label} cannot declare HTML or JavaScript content",
                )
            pattern = value.get("pattern")
            if isinstance(pattern, str):
                raise AppError(
                    422,
                    "component_schema_pattern_forbidden",
                    f"{label} cannot execute caller-supplied regular expressions",
                )
            properties = value.get("properties")
            if isinstance(properties, dict):
                for property_name in properties:
                    if property_name.casefold() in FORBIDDEN_FIELD_NAMES:
                        raise AppError(
                            422,
                            "component_schema_executable_field_forbidden",
                            f"{label} declares forbidden executable-content field {property_name}",
                        )
            for key, nested in value.items():
                visit(nested, path=f"{path}/{key}", depth=depth + 1)
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                visit(nested, path=f"{path}/{index}", depth=depth + 1)

    visit(schema, path="$", depth=0)
    try:
        validator_for(schema).check_schema(schema)
    except Exception as exc:
        raise AppError(
            422,
            "component_schema_invalid",
            f"{label} is not a valid supported JSON Schema",
            {"validation_error": type(exc).__name__},
        ) from exc


def _assert_no_executable_content(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.casefold() in FORBIDDEN_FIELD_NAMES:
                raise AppError(
                    422,
                    "trusted_component_executable_content_forbidden",
                    f"Trusted component data contains forbidden field at {path}/{key}",
                )
            _assert_no_executable_content(nested, path=f"{path}/{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_executable_content(nested, path=f"{path}/{index}")
    elif isinstance(value, str):
        lowered = value.casefold()
        if any(marker in lowered for marker in FORBIDDEN_CONTENT_MARKERS):
            raise AppError(
                422,
                "trusted_component_executable_content_forbidden",
                f"Trusted component data contains HTML or script content at {path}",
            )


def _validate_instance(
    schema: dict[str, Any],
    instance: dict[str, Any],
    *,
    label: str,
    trusted_main_dom: bool = False,
) -> int:
    encoded = _canonical_json(instance)
    if len(encoded) > MAX_INSTANCE_BYTES:
        raise AppError(
            422,
            "component_data_too_large",
            f"{label} exceeds the {MAX_INSTANCE_BYTES}-byte data limit",
        )
    if trusted_main_dom:
        _assert_no_executable_content(instance)
    validator = validator_for(schema)(schema)
    try:
        validator.validate(instance)
    except ValidationError as exc:
        path = "/".join(str(part) for part in exc.absolute_path)
        raise AppError(
            422,
            "component_data_schema_mismatch",
            f"{label} does not match the registered schema",
            {"path": path, "validator": exc.validator},
        ) from exc
    return len(encoded)


def _signature_metadata(payload: ComponentManifestImportRequest) -> tuple[str, dict[str, Any]]:
    if payload.signature is None:
        return "unsigned", {}
    try:
        signature = base64.b64decode(
            payload.signature.signature_base64,
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise AppError(
            422,
            "component_signature_invalid",
            "Component signature is not valid base64",
        ) from exc
    return (
        "unverified",
        {
            "algorithm": payload.signature.algorithm,
            "key_id": payload.signature.key_id,
            "signature_sha256": _sha256(signature),
            "reason": "no_component_signature_trust_store_configured",
        },
    )


def _manifest_material(
    payload: ComponentManifestImportRequest,
    *,
    renderer: str,
    permissions: dict[str, Any],
) -> dict[str, Any]:
    return {
        "component_id": payload.component_id,
        "version": payload.version,
        "display_name": payload.display_name,
        "renderer": renderer,
        "source": payload.source,
        "author": payload.author,
        "package_hash": payload.package_hash,
        "compatible_learngraph": payload.compatible_learngraph,
        "uninstall_behavior": payload.uninstall_behavior,
        "data_schema": payload.data_schema,
        "event_schema": payload.event_schema,
        "permissions": permissions,
        "size_limits": payload.size_limits.model_dump(),
        "skill_triggers": payload.skill_triggers,
        "example_data": payload.example_data,
    }


def _event_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "value"],
        "properties": {
            "type": {"type": "string", "enum": ["submit", "change", "select"]},
            "value": {
                "oneOf": [
                    {"type": "string", "maxLength": 10_000},
                    {
                        "type": "array",
                        "maxItems": 100,
                        "items": {"type": "string", "maxLength": 1_000},
                    },
                ]
            },
        },
    }


def _builtin_specs() -> dict[str, dict[str, Any]]:
    option = {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "label"],
        "properties": {
            "id": {"type": "string", "maxLength": 80},
            "label": {"type": "string", "maxLength": 500},
            "description": {"type": "string", "maxLength": 2_000},
            # Optional client-side grading hint. Prefer props.correct_option_ids.
            "is_correct": {"type": "boolean"},
        },
    }
    # Shared optional answer-key fields for interactive question cards.
    # Frontend grades locally when present so the control can show results
    # immediately after the learner confirms, without waiting for a model turn.
    answer_key_props = {
        "correct_option_ids": {
            "type": "array",
            "maxItems": 100,
            "items": {"type": "string", "maxLength": 80},
        },
        "correct_answers": {
            "type": "array",
            "maxItems": 20,
            "items": {"type": "string", "maxLength": 2_000},
        },
        "explanation": {"type": "string", "maxLength": 5_000},
        "feedback_correct": {"type": "string", "maxLength": 2_000},
        "feedback_incorrect": {"type": "string", "maxLength": 2_000},
    }
    question_base = {
        "prompt": {"type": "string", "maxLength": 5_000},
        "options": {"type": "array", "maxItems": 100, "items": option},
        **answer_key_props,
    }
    return {
        "weather_card": {
            "display_name": "Weather Card",
            "data_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["location", "condition", "temperature_c"],
                "properties": {
                    "title": {"type": "string", "maxLength": 500},
                    "location": {"type": "string", "maxLength": 240},
                    "condition": {"type": "string", "maxLength": 240},
                    "temperature_c": {"type": "number", "minimum": -100, "maximum": 100},
                    "high_c": {"type": "number", "minimum": -100, "maximum": 100},
                    "low_c": {"type": "number", "minimum": -100, "maximum": 100},
                    "summary": {"type": "string", "maxLength": 2_000},
                    "unit": {"type": "string", "enum": ["C", "F"]},
                    "actions": {
                        "type": "array",
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["id", "label"],
                            "properties": {
                                "id": {"type": "string", "maxLength": 80},
                                "label": {"type": "string", "maxLength": 80},
                                "event": {"type": "string", "maxLength": 80},
                            },
                        },
                    },
                },
            },
            "example_data": {
                "title": "明日天气",
                "location": "Shanghai",
                "condition": "clear",
                "temperature_c": 26,
                "high_c": 28,
                "low_c": 18,
                "summary": "适合户外轻量复习",
                "unit": "C",
                "actions": [
                    {"id": "create_plan", "label": "生成学习计划", "event": "create_plan"}
                ],
            },
        },
        "metric_card": {
            "display_name": "Metric Card",
            "data_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "metrics"],
                "properties": {
                    "title": {"type": "string", "maxLength": 500},
                    "description": {"type": "string", "maxLength": 2_000},
                    "metrics": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["label", "value"],
                            "properties": {
                                "id": {"type": "string", "maxLength": 80},
                                "label": {"type": "string", "maxLength": 120},
                                "value": {
                                    "oneOf": [
                                        {"type": "string", "maxLength": 120},
                                        {"type": "number"},
                                    ]
                                },
                                "hint": {"type": "string", "maxLength": 240},
                            },
                        },
                    },
                    "actions": {
                        "type": "array",
                        "maxItems": 5,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["id", "label"],
                            "properties": {
                                "id": {"type": "string", "maxLength": 80},
                                "label": {"type": "string", "maxLength": 80},
                                "event": {"type": "string", "maxLength": 80},
                            },
                        },
                    },
                },
            },
            "example_data": {
                "title": "今日学习指标",
                "description": "来自当前目标进度",
                "metrics": [
                    {"id": "mastery", "label": "掌握度", "value": "62%", "hint": "近 7 日"},
                    {"id": "reviews", "label": "待复习", "value": 3},
                ],
                "actions": [
                    {"id": "open_plan", "label": "查看路线", "event": "open_plan"}
                ],
            },
        },
        "option_group": {
            "display_name": "Option Group",
            "data_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["options"],
                "properties": {
                    "title": {"type": "string", "maxLength": 500},
                    "prompt": {"type": "string", "maxLength": 5_000},
                    "description": {"type": "string", "maxLength": 2_000},
                    "options": question_base["options"],
                    "allow_custom": {"type": "boolean"},
                    "allow_skip": {"type": "boolean"},
                    "submit_label": {"type": "string", "maxLength": 80},
                    **answer_key_props,
                },
            },
            "example_data": {
                "title": "请选择",
                "options": [
                    {"id": "a", "label": "Option A", "is_correct": True},
                    {"id": "b", "label": "Option B"},
                ],
                "correct_option_ids": ["a"],
                "explanation": "A 是正确答案。",
                "allow_custom": True,
                "allow_skip": True,
            },
        },
        "single_choice": {
            "display_name": "Single Choice",
            "data_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["options"],
                "properties": {
                    "title": {"type": "string", "maxLength": 500},
                    "prompt": {"type": "string", "maxLength": 5_000},
                    "description": {"type": "string", "maxLength": 2_000},
                    "options": question_base["options"],
                    "allow_custom": {"type": "boolean"},
                    "allow_skip": {"type": "boolean"},
                    "submit_label": {"type": "string", "maxLength": 80},
                    **answer_key_props,
                },
            },
            "example_data": {
                "title": "Choose one",
                "prompt": "Choose one",
                "options": [
                    {"id": "a", "label": "A", "is_correct": True},
                    {"id": "b", "label": "B"},
                ],
                "correct_option_ids": ["a"],
                "explanation": "A is correct.",
            },
        },
        "multiple_choice": {
            "display_name": "Multiple Choice",
            "data_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["options"],
                "properties": {
                    "title": {"type": "string", "maxLength": 500},
                    "prompt": {"type": "string", "maxLength": 5_000},
                    "description": {"type": "string", "maxLength": 2_000},
                    "options": question_base["options"],
                    "allow_custom": {"type": "boolean"},
                    "allow_skip": {"type": "boolean"},
                    "submit_label": {"type": "string", "maxLength": 80},
                    **answer_key_props,
                },
            },
            "example_data": {
                "title": "Choose any",
                "prompt": "Choose any",
                "options": [
                    {"id": "a", "label": "A", "is_correct": True},
                    {"id": "b", "label": "B", "is_correct": True},
                    {"id": "c", "label": "C"},
                ],
                "correct_option_ids": ["a", "b"],
                "explanation": "A and B are both correct.",
            },
        },
        "fill_blank": {
            "display_name": "Fill Blank",
            "data_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": [],
                "properties": {
                    "title": {"type": "string", "maxLength": 500},
                    "prompt": {"type": "string", "maxLength": 5_000},
                    "description": {"type": "string", "maxLength": 2_000},
                    "placeholder": {"type": "string", "maxLength": 500},
                    "multiline": {"type": "boolean"},
                    "submit_label": {"type": "string", "maxLength": 80},
                    "blank_ids": {
                        "type": "array",
                        "maxItems": 100,
                        "items": {"type": "string", "maxLength": 80},
                    },
                    **answer_key_props,
                },
            },
            "example_data": {
                "title": "填空",
                "prompt": "ACID means ____",
                "blank_ids": ["one"],
                "correct_answers": ["Atomicity Consistency Isolation Durability"],
                "explanation": "ACID 四性：原子性、一致性、隔离性、持久性。",
            },
        },
        "short_answer_table": {
            "display_name": "Short Answer Table",
            "data_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["columns", "rows"],
                "properties": {
                    "title": {"type": "string", "maxLength": 500},
                    "description": {"type": "string", "maxLength": 2_000},
                    "placeholder": {"type": "string", "maxLength": 500},
                    "multiline": {"type": "boolean"},
                    "submit_label": {"type": "string", "maxLength": 80},
                    "columns": {
                        "type": "array",
                        "maxItems": 32,
                        "items": {"type": "string", "maxLength": 240},
                    },
                    "rows": {
                        "type": "array",
                        "maxItems": 100,
                        "items": {
                            "type": "array",
                            "maxItems": 32,
                            "items": {"type": "string", "maxLength": 5_000},
                        },
                    },
                    **answer_key_props,
                },
            },
            "example_data": {
                "title": "简答题表",
                "columns": ["Question", "Answer"],
                "rows": [["Why?", ""]],
                "explanation": "回答应覆盖因果链中的关键节点。",
            },
        },
        "image_frame": {
            "display_name": "Image Frame",
            "data_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["status", "alt"],
                "properties": {
                    "title": {"type": "string", "maxLength": 500},
                    "status": {
                        "type": "string",
                        "enum": [
                            "placeholder",
                            "ready",
                            "failed",
                            "queued",
                            "running",
                            "completed",
                            "cancelled",
                        ],
                    },
                    "file_id": {"type": ["string", "null"], "maxLength": 160},
                    "src": {"type": "string", "maxLength": 2_000},
                    "alt": {"type": "string", "maxLength": 1_000},
                    "aspect_ratio": {"type": "string", "maxLength": 32},
                },
            },
            "example_data": {
                "title": "图片",
                "status": "placeholder",
                "file_id": None,
                "alt": "Pending image",
            },
        },
    }


class ComponentService:
    def __init__(self, db: Session, workspace_id: str, actor_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.plugins = PluginRepository(db, workspace_id)
        self.manifests = ComponentManifestRepository(db, workspace_id)
        self.authorizations = ComponentAuthorizationRepository(db, workspace_id)
        self.checks = ComponentCheckRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)

    def _require_component(self, plugin_id: str) -> PluginRecord:
        plugin = self.plugins.require(plugin_id, "component plugin")
        if plugin.plugin_type != "trusted_component":
            raise AppError(
                409,
                "plugin_is_not_component",
                "The requested plugin does not expose a component manifest",
            )
        return plugin

    def _require_current_manifest(
        self,
        plugin: PluginRecord,
        manifest_version_id: str,
    ) -> ComponentManifestVersion:
        manifest = self.manifests.require_for_plugin(plugin.id, manifest_version_id)
        if manifest.version != plugin.version:
            raise AppError(
                409,
                "component_manifest_version_stale",
                "Only the current component manifest version may be authorized or invoked",
                {"current_version": plugin.version},
            )
        return manifest

    def register(
        self,
        payload: ComponentManifestImportRequest,
    ) -> tuple[PluginRecord, ComponentManifestVersion, bool, list[str], list[ComponentCheckRecord]]:
        try:
            if payload.renderer != "sandbox":
                raise AppError(
                    422,
                    "third_party_renderer_forbidden",
                    "Imported components cannot request trusted main-DOM rendering",
                )
            if payload.component_id in BUILTIN_COMPONENT_IDS or payload.source == "builtin":
                raise AppError(
                    422,
                    "builtin_component_identity_reserved",
                    "Built-in component identities cannot be imported through the workspace API",
                )
            _schema_guard(payload.data_schema, label="data_schema")
            _schema_guard(payload.event_schema, label="event_schema")
            _validate_instance(
                payload.data_schema,
                payload.example_data,
                label="example_data",
                trusted_main_dom=True,
            )
            signature_status, signature_info = _signature_metadata(payload)
        except AppError as exc:
            self.audit.record(
                actor_id=self.actor_id,
                action="component.manifest_rejected",
                resource_type="component",
                resource_id=payload.component_id,
                outcome="rejected",
                details={"error_code": exc.code, "version": payload.version},
            )
            self.db.commit()
            raise

        permissions = payload.permissions.model_dump()
        material = _manifest_material(payload, renderer="sandbox", permissions=permissions)
        schema_hash = _sha256(
            {"data_schema": payload.data_schema, "event_schema": payload.event_schema}
        )
        permissions_hash = _sha256(permissions)
        manifest_hash = _sha256(material)
        plugin = self.db.scalar(
            self.plugins.query().where(PluginRecord.plugin_key == payload.component_id)
        )
        previous_manifest: ComponentManifestVersion | None = None
        reauthorization_reasons: list[str] = []
        if plugin is None:
            plugin = self.plugins.add(
                PluginRecord(
                    workspace_id=self.workspace_id,
                    plugin_key=payload.component_id,
                    name=payload.display_name,
                    version=payload.version,
                    plugin_type="trusted_component",
                    status="configured",
                    enabled=False,
                    permissions=_flatten_permissions(permissions),
                    capabilities=["component_manifest_v1", "sandbox_artifact"],
                )
            )
        else:
            if plugin.plugin_type != "trusted_component":
                raise AppError(
                    409,
                    "plugin_key_conflict",
                    "The component ID is already used by a non-component plugin",
                )
            duplicate = self.db.scalar(
                self.manifests.query().where(
                    ComponentManifestVersion.plugin_id == plugin.id,
                    ComponentManifestVersion.version == payload.version,
                )
            )
            if duplicate is not None:
                raise AppError(
                    409,
                    "component_manifest_version_immutable",
                    "A published component manifest version cannot be replaced",
                )
            previous_manifest = self.manifests.current(plugin)
            reauthorization_reasons.append("version_changed")
            if previous_manifest is not None:
                if previous_manifest.package_hash != payload.package_hash:
                    reauthorization_reasons.append("package_hash_changed")
                if previous_manifest.schema_hash != schema_hash:
                    reauthorization_reasons.append("schema_changed")
                if previous_manifest.permissions_hash != permissions_hash:
                    reauthorization_reasons.append("permissions_changed")
            active = self.authorizations.active_for_plugin(plugin.id)
            if active is not None:
                active.status = "superseded"
                active.revoked_by = self.actor_id
                active.revoked_at = _utc_now()
                active.revoke_reason = "manifest_upgrade"
            plugin.name = payload.display_name
            plugin.version = payload.version
            plugin.permissions = _flatten_permissions(permissions)
            plugin.enabled = False
            plugin.status = "configured"

        manifest = self.manifests.add(
            ComponentManifestVersion(
                workspace_id=self.workspace_id,
                plugin_id=plugin.id,
                component_id=payload.component_id,
                version=payload.version,
                display_name=payload.display_name,
                renderer="sandbox",
                source=payload.source,
                author=payload.author,
                package_hash=payload.package_hash,
                package_hash_status="declared_unverified",
                signature_status=signature_status,
                signature_info=signature_info,
                compatible_learngraph=payload.compatible_learngraph,
                uninstall_behavior=payload.uninstall_behavior,
                data_schema=payload.data_schema,
                event_schema=payload.event_schema,
                permissions=permissions,
                size_limits=payload.size_limits.model_dump(),
                skill_triggers=payload.skill_triggers,
                example_data=payload.example_data,
                schema_hash=schema_hash,
                permissions_hash=permissions_hash,
                manifest_hash=manifest_hash,
            )
        )
        health = self.checks.add(
            ComponentCheckRecord(
                workspace_id=self.workspace_id,
                plugin_id=plugin.id,
                manifest_version_id=manifest.id,
                check_type="health",
                status="passed",
                executor="server_jsonschema_validator",
                runtime_executed=False,
                details={
                    "manifest_static_check": "passed",
                    "example_data_validation": "passed",
                    "package_hash_status": "declared_unverified",
                    "signature_status": signature_status,
                },
                artifact_metadata={},
                checked_by=self.actor_id,
            )
        )
        render = self.checks.add(
            ComponentCheckRecord(
                workspace_id=self.workspace_id,
                plugin_id=plugin.id,
                manifest_version_id=manifest.id,
                check_type="render",
                status="unavailable",
                executor="browser_sandbox_unconfigured",
                runtime_executed=False,
                details={
                    "reason": "isolated_browser_renderer_not_configured",
                    "constraints_checked": False,
                },
                artifact_metadata={
                    "runtime_available": False,
                    "sandbox_origin": None,
                    "screenshot_available": False,
                },
                checked_by=self.actor_id,
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action=(
                "component.manifest_upgraded"
                if previous_manifest is not None
                else "component.manifest_registered"
            ),
            resource_type="component",
            resource_id=plugin.id,
            details={
                "component_id": payload.component_id,
                "version": payload.version,
                "manifest_hash": manifest_hash,
                "package_hash_status": "declared_unverified",
                "signature_status": signature_status,
                "reauthorization_required": previous_manifest is not None,
                "reauthorization_reasons": reauthorization_reasons,
            },
        )
        self.db.commit()
        for record in (plugin, manifest, health, render):
            self.db.refresh(record)
        return (
            plugin,
            manifest,
            previous_manifest is not None,
            reauthorization_reasons,
            [health, render],
        )

    def list_manifests(self, plugin_id: str) -> list[ComponentManifestVersion]:
        self._require_component(plugin_id)
        return self.manifests.list_for_plugin(plugin_id)

    def list_authorizations(self, plugin_id: str) -> list[ComponentAuthorization]:
        self._require_component(plugin_id)
        return self.authorizations.list_for_plugin(plugin_id)

    def authorize(
        self,
        plugin_id: str,
        payload: ComponentAuthorizationRequest,
    ) -> ComponentAuthorization:
        plugin = self._require_component(plugin_id)
        manifest = self._require_current_manifest(plugin, payload.manifest_version_id)
        if manifest.source == "builtin":
            raise AppError(
                409,
                "builtin_authorization_managed_by_system",
                "Built-in component authorization is fixed by the server whitelist",
            )
        health = self.checks.latest(plugin.id, manifest.id, "health")
        if health is None or health.status != "passed":
            raise AppError(
                409,
                "component_health_check_required",
                "The current manifest must pass its server health check before authorization",
            )
        existing = self.authorizations.active_for_plugin(plugin.id)
        if existing is not None:
            existing.status = "superseded"
            existing.revoked_by = self.actor_id
            existing.revoked_at = _utc_now()
            existing.revoke_reason = "authorization_replaced"
        authorization = self.authorizations.add(
            ComponentAuthorization(
                workspace_id=self.workspace_id,
                plugin_id=plugin.id,
                manifest_version_id=manifest.id,
                scope=payload.scope,
                status="authorized",
                manifest_hash=manifest.manifest_hash,
                permissions_hash=manifest.permissions_hash,
                authorized_by=self.actor_id,
            )
        )
        plugin.status = "configured"
        self.audit.record(
            actor_id=self.actor_id,
            action="component.authorize",
            resource_type="component",
            resource_id=plugin.id,
            details={
                "manifest_version_id": manifest.id,
                "version": manifest.version,
                "scope": payload.scope,
                "permissions": manifest.permissions,
                "render_check_status": (
                    self.checks.latest(plugin.id, manifest.id, "render").status
                    if self.checks.latest(plugin.id, manifest.id, "render")
                    else "missing"
                ),
            },
        )
        self.db.commit()
        self.db.refresh(authorization)
        return authorization

    def revoke(
        self,
        plugin_id: str,
        payload: ComponentAuthorizationRevokeRequest,
    ) -> ComponentAuthorization:
        plugin = self._require_component(plugin_id)
        authorization = self.authorizations.active_for_plugin(plugin.id)
        if authorization is None:
            raise AppError(
                409,
                "component_authorization_not_active",
                "No active workspace component authorization exists",
            )
        if authorization.scope == "system_builtin":
            raise AppError(
                409,
                "builtin_authorization_managed_by_system",
                "Built-in component authorization is fixed by the server whitelist",
            )
        authorization.status = "revoked"
        authorization.revoked_by = self.actor_id
        authorization.revoked_at = _utc_now()
        authorization.revoke_reason = payload.reason
        plugin.enabled = False
        plugin.status = "disabled"
        self.audit.record(
            actor_id=self.actor_id,
            action="component.authorization_revoked",
            resource_type="component",
            resource_id=plugin.id,
            details={
                "authorization_id": authorization.id,
                "manifest_version_id": authorization.manifest_version_id,
                "reason": payload.reason,
            },
        )
        self.db.commit()
        self.db.refresh(authorization)
        return authorization

    def list_checks(self, plugin_id: str) -> list[ComponentCheckRecord]:
        self._require_component(plugin_id)
        return self.checks.list_for_plugin(plugin_id)

    def run_check(
        self,
        plugin_id: str,
        payload: ComponentCheckRequest,
    ) -> ComponentCheckRecord:
        plugin = self._require_component(plugin_id)
        manifest = self._require_current_manifest(plugin, payload.manifest_version_id)
        if payload.check_type == "render":
            record = self.checks.add(
                ComponentCheckRecord(
                    workspace_id=self.workspace_id,
                    plugin_id=plugin.id,
                    manifest_version_id=manifest.id,
                    check_type="render",
                    status="unavailable",
                    executor="browser_sandbox_unconfigured",
                    runtime_executed=False,
                    details={
                        "reason": "isolated_browser_renderer_not_configured",
                        "requested_by": self.actor_id,
                    },
                    artifact_metadata={
                        "runtime_available": False,
                        "sandbox_origin": None,
                        "screenshot_available": False,
                    },
                    checked_by=self.actor_id,
                )
            )
        else:
            status = "passed"
            details: dict[str, Any] = {
                "manifest_static_check": "passed",
                "sample_data_validation": "passed",
            }
            try:
                _schema_guard(manifest.data_schema, label="data_schema")
                _schema_guard(manifest.event_schema, label="event_schema")
                _validate_instance(
                    manifest.data_schema,
                    payload.sample_data
                    if payload.sample_data is not None
                    else manifest.example_data,
                    label="sample_data",
                    trusted_main_dom=manifest.source == "builtin",
                )
            except AppError as exc:
                status = "failed"
                details = {"error_code": exc.code, "message": exc.message}
            record = self.checks.add(
                ComponentCheckRecord(
                    workspace_id=self.workspace_id,
                    plugin_id=plugin.id,
                    manifest_version_id=manifest.id,
                    check_type="health",
                    status=status,
                    executor="server_jsonschema_validator",
                    runtime_executed=False,
                    details=details,
                    artifact_metadata={},
                    checked_by=self.actor_id,
                )
            )
        self.audit.record(
            actor_id=self.actor_id,
            action="component.check",
            resource_type="component",
            resource_id=plugin.id,
            outcome="success" if record.status == "passed" else record.status,
            details={
                "check_type": record.check_type,
                "status": record.status,
                "runtime_executed": record.runtime_executed,
                "manifest_version_id": manifest.id,
            },
        )
        self.db.commit()
        self.db.refresh(record)
        return record

    def assert_can_enable(self, plugin: PluginRecord) -> str:
        manifest = self.manifests.current(plugin)
        if manifest is None:
            raise AppError(
                409,
                "component_manifest_required",
                "A component plugin cannot be enabled without a current manifest",
            )
        authorization = self.authorizations.active_for_plugin(plugin.id)
        if (
            authorization is None
            or authorization.manifest_version_id != manifest.id
            or authorization.manifest_hash != manifest.manifest_hash
            or authorization.permissions_hash != manifest.permissions_hash
        ):
            raise AppError(
                409,
                "component_reauthorization_required",
                "The current component version and permissions require workspace authorization",
                {"manifest_version_id": manifest.id, "version": manifest.version},
            )
        health = self.checks.latest(plugin.id, manifest.id, "health")
        if health is None or health.status != "passed":
            raise AppError(
                409,
                "component_health_check_required",
                "The current component manifest has no passing health check",
            )
        return "enabled" if manifest.source == "builtin" else "degraded"

    def _authorized_invocation(
        self,
        plugin_id: str,
        manifest_version_id: str,
    ) -> tuple[PluginRecord, ComponentManifestVersion, ComponentAuthorization]:
        plugin = self._require_component(plugin_id)
        manifest = self._require_current_manifest(plugin, manifest_version_id)
        if not plugin.enabled:
            raise AppError(
                409,
                "component_disabled",
                "The component is disabled in this workspace",
            )
        authorization = self.authorizations.active_for_plugin(plugin.id)
        if (
            authorization is None
            or authorization.manifest_version_id != manifest.id
            or authorization.manifest_hash != manifest.manifest_hash
            or authorization.permissions_hash != manifest.permissions_hash
        ):
            raise AppError(
                409,
                "component_reauthorization_required",
                "The component authorization is missing, revoked, or stale",
            )
        return plugin, manifest, authorization

    def create_artifact(
        self,
        plugin_id: str,
        payload: ComponentArtifactRequest,
    ) -> ComponentArtifactView:
        plugin, manifest, authorization = self._authorized_invocation(
            plugin_id,
            payload.manifest_version_id,
        )
        is_builtin = (
            manifest.source == "builtin"
            and manifest.component_id in BUILTIN_COMPONENT_IDS
            and manifest.renderer == "trusted-bundle"
        )
        data_size = _validate_instance(
            manifest.data_schema,
            payload.data,
            label="component_data",
            trusted_main_dom=is_builtin,
        )
        data_hash = _sha256(payload.data)
        if is_builtin:
            result = ComponentArtifactView(
                delivery_mode="trusted_component",
                component_id=manifest.component_id,
                version=manifest.version,
                manifest_version_id=manifest.id,
                authorization_id=authorization.id,
                runtime_status="builtin_registry_validated",
                sandbox_executed=False,
                trusted_component={
                    "component_id": manifest.component_id,
                    "version": manifest.version,
                    "data": payload.data,
                    "data_sha256": data_hash,
                },
                sandbox_artifact=None,
            )
        else:
            artifact_id = _sha256(
                {
                    "manifest_hash": manifest.manifest_hash,
                    "data_sha256": data_hash,
                }
            )
            result = ComponentArtifactView(
                delivery_mode="sandbox_artifact",
                component_id=manifest.component_id,
                version=manifest.version,
                manifest_version_id=manifest.id,
                authorization_id=authorization.id,
                runtime_status="unavailable",
                sandbox_executed=False,
                trusted_component=None,
                sandbox_artifact={
                    "artifact_id": artifact_id,
                    "data_sha256": data_hash,
                    "data_size_bytes": data_size,
                    "package_hash": manifest.package_hash,
                    "renderer": "sandbox",
                    "runtime_available": False,
                    "sandbox_origin": None,
                    "reason": "isolated_browser_renderer_not_configured",
                },
            )
        self.audit.record(
            actor_id=self.actor_id,
            action="component.artifact_prepared",
            resource_type="component",
            resource_id=plugin.id,
            details={
                "manifest_version_id": manifest.id,
                "delivery_mode": result.delivery_mode,
                "data_sha256": data_hash,
                "data_size_bytes": data_size,
                "sandbox_executed": False,
                "runtime_status": result.runtime_status,
            },
        )
        self.db.commit()
        return result

    def validate_event(
        self,
        plugin_id: str,
        payload: ComponentEventValidationRequest,
    ) -> ComponentEventValidationView:
        plugin, manifest, _ = self._authorized_invocation(
            plugin_id,
            payload.manifest_version_id,
        )
        _validate_instance(
            manifest.event_schema,
            payload.event,
            label="component_event",
        )
        event_hash = _sha256(payload.event)
        self.audit.record(
            actor_id=self.actor_id,
            action="component.event_validated",
            resource_type="component",
            resource_id=plugin.id,
            details={
                "manifest_version_id": manifest.id,
                "event_hash": event_hash,
                "executed": False,
            },
        )
        self.db.commit()
        return ComponentEventValidationView(
            accepted=True,
            component_id=manifest.component_id,
            version=manifest.version,
            event_hash=event_hash,
            executed=False,
        )


def ensure_builtin_components(db: Session, workspace_id: str) -> None:
    """Register the fixed server whitelist without trusting workspace input."""

    plugins = PluginRepository(db, workspace_id)
    manifests = ComponentManifestRepository(db, workspace_id)
    authorizations = ComponentAuthorizationRepository(db, workspace_id)
    checks = ComponentCheckRepository(db, workspace_id)
    permissions = {
        "network_domains": [],
        "file_read": False,
        "clipboard_write": False,
        "message_actions": ["submit"],
    }
    event_schema = _event_schema()
    for component_id, spec in _builtin_specs().items():
        plugin = db.scalar(
            plugins.query().where(PluginRecord.plugin_key == component_id)
        )
        if plugin is not None:
            # Never upgrade an existing workspace-controlled row into a built-in trust role.
            continue
        package_hash = _sha256(f"learngraph-builtin:{component_id}:1.0.0".encode())
        material = {
            "component_id": component_id,
            "version": "1.0.0",
            "display_name": spec["display_name"],
            "renderer": "trusted-bundle",
            "source": "builtin",
            "author": "LearnGraph",
            "package_hash": package_hash,
            "compatible_learngraph": {"minimum": "0.1.0"},
            "uninstall_behavior": "retain_data",
            "data_schema": spec["data_schema"],
            "event_schema": event_schema,
            "permissions": permissions,
            "size_limits": {"min_height": 80, "max_height": 720},
            "skill_triggers": [],
            "example_data": spec["example_data"],
        }
        _schema_guard(spec["data_schema"], label=f"{component_id}.data_schema")
        _schema_guard(event_schema, label=f"{component_id}.event_schema")
        _validate_instance(
            spec["data_schema"],
            spec["example_data"],
            label=f"{component_id}.example_data",
            trusted_main_dom=True,
        )
        plugin = plugins.add(
            PluginRecord(
                workspace_id=workspace_id,
                plugin_key=component_id,
                name=spec["display_name"],
                version="1.0.0",
                plugin_type="trusted_component",
                status="enabled",
                enabled=True,
                permissions=_flatten_permissions(permissions),
                capabilities=["component_manifest_v1", "trusted_main_dom"],
            )
        )
        manifest = manifests.add(
            ComponentManifestVersion(
                workspace_id=workspace_id,
                plugin_id=plugin.id,
                component_id=component_id,
                version="1.0.0",
                display_name=spec["display_name"],
                renderer="trusted-bundle",
                source="builtin",
                author="LearnGraph",
                package_hash=package_hash,
                package_hash_status="verified_builtin",
                signature_status="verified_builtin",
                signature_info={"authority": "learngraph_builtin_registry"},
                compatible_learngraph={"minimum": "0.1.0"},
                uninstall_behavior="retain_data",
                data_schema=spec["data_schema"],
                event_schema=event_schema,
                permissions=permissions,
                size_limits={"min_height": 80, "max_height": 720},
                skill_triggers=[],
                example_data=spec["example_data"],
                schema_hash=_sha256(
                    {"data_schema": spec["data_schema"], "event_schema": event_schema}
                ),
                permissions_hash=_sha256(permissions),
                manifest_hash=_sha256(material),
            )
        )
        authorizations.add(
            ComponentAuthorization(
                workspace_id=workspace_id,
                plugin_id=plugin.id,
                manifest_version_id=manifest.id,
                scope="system_builtin",
                status="authorized",
                manifest_hash=manifest.manifest_hash,
                permissions_hash=manifest.permissions_hash,
                authorized_by="system",
            )
        )
        checks.add(
            ComponentCheckRecord(
                workspace_id=workspace_id,
                plugin_id=plugin.id,
                manifest_version_id=manifest.id,
                check_type="health",
                status="passed",
                executor="builtin_registry_validator",
                runtime_executed=False,
                details={"schema_validation": "passed", "registry_entry": "present"},
                artifact_metadata={},
                checked_by="system",
            )
        )
        checks.add(
            ComponentCheckRecord(
                workspace_id=workspace_id,
                plugin_id=plugin.id,
                manifest_version_id=manifest.id,
                check_type="render",
                status="unavailable",
                executor="browser_render_check_unconfigured",
                runtime_executed=False,
                details={
                    "reason": "browser_render_check_not_executed",
                    "trust_basis": "compiled_builtin_registry",
                },
                artifact_metadata={
                    "runtime_available": False,
                    "screenshot_available": False,
                },
                checked_by="system",
            )
        )
    db.commit()
