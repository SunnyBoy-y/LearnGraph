"""Immutable multi-file subapp bundle publication and preview access."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import mimetypes
from pathlib import PurePosixPath
import secrets
from typing import Any

from jsonschema import ValidationError
from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import (
    ComponentManifestVersion,
    ContentBlob,
    PluginRecord,
    SubAppBundle,
    SubAppBundleFile,
    SubAppBundlePreviewGrant,
    SubAppBundleValidation,
    utc_now,
)
from app.providers.remote.sandbox import validate_agent_workspace_path
from app.repositories.audit import AuditRepository
from app.services.session_workspace import SessionWorkspaceService

MAX_BUNDLE_FILES = 256
MAX_BUNDLE_FILE_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_CONTRACT_FILE_BYTES = 128 * 1024
VALIDATION_TTL_SECONDS = 900
PREVIEW_GRANT_TTL_SECONDS = 300

# Executable/text and passive resource formats needed by Vite/React/Vue teaching
# applications. Unknown content is never served from the preview gateway.
ALLOWED_MIME_TYPES = frozenset(
    {
        "text/html",
        "text/css",
        "text/javascript",
        "application/javascript",
        "application/json",
        "application/wasm",
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "image/avif",
        "image/svg+xml",
        "image/x-icon",
        "font/woff",
        "font/woff2",
        "font/ttf",
        "font/otf",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "video/mp4",
        "video/webm",
        "model/gltf+json",
        "model/gltf-binary",
        "application/octet-stream",
    }
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes for ``DateTime(timezone=True)`` columns;
    normalize before comparing with a tz-aware ``now``."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _sha256(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _normalize_bundle_path(value: str) -> str:
    try:
        safe = validate_agent_workspace_path(value)
    except Exception as exc:
        raise AppError(422, "subapp_bundle_path_invalid", "Bundle path is not a safe relative path") from exc
    path = PurePosixPath(safe)
    if path.name in {"", ".", ".."}:
        raise AppError(422, "subapp_bundle_path_invalid", "Bundle path must name a file")
    return str(path)


def _mime_type(path: str, stored: str | None = None) -> str:
    guessed, _ = mimetypes.guess_type(path)
    mime = (stored or guessed or "application/octet-stream").split(";", 1)[0].strip().casefold()
    if mime == "text/javascript":
        return mime
    if mime not in ALLOWED_MIME_TYPES:
        raise AppError(422, "subapp_bundle_mime_blocked", f"Unsupported preview resource type: {mime}")
    return mime


def _validate_entry_html(data: bytes, *, entry_path: str, known_paths: set[str]) -> list[str]:
    try:
        html = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AppError(422, "subapp_bundle_entry_not_utf8", "Bundle entry HTML must be UTF-8") from exc
    folded = html.casefold()
    # Navigation / embedding / form / base markup is always rejected. External
    # http(s) references are allowed: frontend-sandbox networking is
    # approval-free (static assets load directly under the gateway CSP, JS
    # network goes through the sandbox-net relay), so only structural markup
    # that could navigate or embed is forbidden here.
    forbidden = ("<base", "<iframe", "<frame", "<object", "<embed", "<form")
    found = [item for item in forbidden if item in folded]
    if found:
        raise AppError(
            422,
            "subapp_bundle_entry_policy_rejected",
            "Bundle entry contains unsupported external or navigational markup",
            {"matches": found},
        )
    # A quick, deterministic reference check catches the common Vite failure:
    # emitting an index.html that points at assets omitted from the publication.
    # External http(s) references are skipped (they resolve over the network).
    import re

    missing: list[str] = []
    parent = PurePosixPath(entry_path).parent
    for raw in re.findall(r"(?:src|href)\s*=\s*[\"']([^\"'#?]+)", html, flags=re.IGNORECASE):
        if raw.startswith(("data:", "blob:")) or re.match(r"^(https?:)?//", raw, flags=re.IGNORECASE):
            continue
        candidate = str((parent / raw).as_posix())
        try:
            normalized = _normalize_bundle_path(candidate)
        except AppError:
            missing.append(raw)
            continue
        if normalized not in known_paths:
            missing.append(raw)
    return missing


class SubAppBundleService:
    """Snapshot validated workspace output into immutable, preview-safe bundles."""

    def __init__(self, db: Session, workspace_id: str, actor_id: str, settings) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.workspace_files = SessionWorkspaceService(db, workspace_id, actor_id, settings)
        self.audit = AuditRepository(db, workspace_id)

    def _entries_for_root(
        self, chat_session_id: str, output_root: str, sandbox_session_id: str | None
    ) -> list[Any]:
        prefix = output_root.rstrip("/") + "/"
        entries = [
            entry
            for entry in self.workspace_files.list_entries(chat_session_id)
            if entry.path.startswith(prefix)
        ]
        if entries:
            return entries
        if sandbox_session_id is None:
            raise AppError(422, "subapp_bundle_output_missing", "Bundle output directory contains no durable files")
        from app.services.sandbox import _sandbox_workspace_path

        host_root = _sandbox_workspace_path(self.settings, f"{self.actor_id}/{sandbox_session_id}")
        output_dir = (host_root / output_root).resolve()
        if host_root not in output_dir.parents or not output_dir.is_dir():
            raise AppError(422, "subapp_bundle_output_missing", "Bundle output directory contains no files")
        discovered = [path for path in output_dir.rglob("*") if path.is_file()]
        if len(discovered) > MAX_BUNDLE_FILES:
            raise AppError(422, "subapp_bundle_file_limit", "Bundle contains too many files")
        for candidate in discovered:
            relative = candidate.relative_to(host_root).as_posix()
            data = candidate.read_bytes()
            self.workspace_files.put_bytes(
                chat_session_id=chat_session_id,
                path=relative,
                data=data,
                role="output" if relative.startswith("outputs/") else "work",
                sandbox_session_id=sandbox_session_id,
                source="bundle_validation",
                publish_file=False,
            )
        return [
            entry
            for entry in self.workspace_files.list_entries(chat_session_id)
            if entry.path.startswith(prefix)
        ]

    def validate(
        self,
        *,
        chat_session_id: str,
        sandbox_session_id: str | None,
        output_root: str,
        entry_path: str,
    ) -> dict[str, Any]:
        root = _normalize_bundle_path(output_root).rstrip("/")
        entry = _normalize_bundle_path(entry_path)
        if not entry.startswith(root + "/"):
            raise AppError(422, "subapp_bundle_entry_outside_root", "Entry path must stay inside output_root")
        entries = self._entries_for_root(chat_session_id, root, sandbox_session_id)
        manifest_files: list[dict[str, Any]] = []
        data_by_path: dict[str, bytes] = {}
        total = 0
        # Files discovered above are already mirrored into the durable workspace.
        for workspace_entry in entries:
            path = _normalize_bundle_path(workspace_entry.path)
            data = self.workspace_files.materialize_bytes(chat_session_id, path)
            if len(data) > MAX_BUNDLE_FILE_BYTES:
                raise AppError(422, "subapp_bundle_file_too_large", "A bundle file exceeds the preview limit")
            total += len(data)
            if total > MAX_BUNDLE_BYTES:
                raise AppError(422, "subapp_bundle_too_large", "Bundle exceeds the aggregate preview limit")
            data_by_path[path] = data
            manifest_files.append(
                {
                    "path": path,
                    "sha256": _sha256(data),
                    "mime_type": _mime_type(path, workspace_entry.mime_type),
                    "size_bytes": len(data),
                }
            )
        if entry not in data_by_path:
            raise AppError(422, "subapp_bundle_entry_missing", "Bundle entry file is not in output_root")
        manifest_files.sort(key=lambda item: item["path"])
        paths = {item["path"] for item in manifest_files}
        if _mime_type(entry) != "text/html":
            raise AppError(422, "subapp_bundle_entry_not_html", "Bundle entry must be an HTML document")
        missing = _validate_entry_html(data_by_path[entry], entry_path=entry, known_paths=paths)
        if missing:
            raise AppError(
                422,
                "subapp_bundle_asset_missing",
                "Bundle entry references resources absent from the durable output",
                {"paths": missing[:20]},
            )
        manifest = {"version": 1, "entry_path": entry, "files": manifest_files}
        manifest_sha256 = _sha256(_canonical_json(manifest))
        validation = SubAppBundleValidation(
            workspace_id=self.workspace_id,
            owner_user_id=self.actor_id,
            chat_session_id=chat_session_id,
            sandbox_session_id=sandbox_session_id,
            output_root=root,
            entry_path=entry,
            manifest_json=manifest,
            manifest_sha256=manifest_sha256,
            report={"status": "passed", "files": len(manifest_files), "bytes": total, "missing_assets": []},
            status="passed",
            expires_at=utc_now() + timedelta(seconds=VALIDATION_TTL_SECONDS),
        )
        self.db.add(validation)
        self.db.flush()
        self.audit.record(
            actor_id=self.actor_id,
            action="subapp.bundle_validated",
            resource_type="subapp_bundle_validation",
            resource_id=validation.id,
            details={"manifest_sha256": manifest_sha256, "entry_path": entry, "file_count": len(manifest_files)},
        )
        self.db.commit()
        return {
            "validation_id": validation.id,
            "status": validation.status,
            "manifest_sha256": manifest_sha256,
            "entry_path": entry,
            "file_count": len(manifest_files),
            "size_bytes": total,
            "report": validation.report,
        }

    def validate_interaction_contract(
        self,
        *,
        chat_session_id: str,
        path: str,
    ) -> dict[str, Any]:
        """Load and validate a ``learngraph.subapp.json`` contract from the
        sandbox workspace, returning a stable checksum the publish step reuses.

        Unlike inline tool arguments, file-backed contracts survive JSON
        escaping and truncation failures; errors carry the JSON Pointer of the
        offending field.
        """
        from app.services.components import _interaction_contract_guard

        contract = self._load_contract_from_workspace(chat_session_id, path)
        try:
            _interaction_contract_guard(contract)
        except AppError as exc:
            raise AppError(
                422,
                "subapp_interaction_contract_invalid",
                f"Invalid interaction contract: {exc.message}",
                exc.details,
            ) from exc
        checksum = _sha256(_canonical_json(contract))
        self.audit.record(
            actor_id=self.actor_id,
            action="subapp.contract_validated",
            resource_type="subapp_bundle_contract",
            resource_id=path,
            details={"checksum": checksum, "path": path},
        )
        self.db.commit()
        return {
            "status": "passed",
            "path": path,
            "contract_checksum": checksum,
            "agent_triggers": [
                item.get("event_type")
                for item in (contract.get("agent_triggers") or [])
                if isinstance(item, dict)
            ],
            "analytics_enabled": bool(
                isinstance(contract.get("analytics"), dict)
                and contract.get("analytics", {}).get("enabled", True)
            ),
        }

    def _load_contract_from_workspace(self, chat_session_id: str, path: str) -> dict[str, Any]:
        from app.services.components import _interaction_contract_guard

        normalized = _normalize_bundle_path(path)
        data = self.workspace_files.materialize_bytes(chat_session_id, normalized)
        if len(data) > MAX_CONTRACT_FILE_BYTES:
            raise AppError(
                422,
                "subapp_interaction_contract_too_large",
                "Interaction contract file exceeds the size limit",
            )
        try:
            contract = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError(
                422,
                "subapp_interaction_contract_invalid_json",
                "Interaction contract file is not valid JSON",
                {"path": normalized, "error": str(exc)},
            ) from exc
        if not isinstance(contract, dict):
            raise AppError(
                422,
                "subapp_interaction_contract_invalid",
                "Interaction contract must be a JSON object",
            )
        _interaction_contract_guard(contract)
        return contract

    def publish(
        self,
        *,
        validation_id: str,
        chat_session_id: str,
        sandbox_session_id: str | None,
        title: str,
        preferred_height: int | None = None,
        interaction_contract: dict[str, Any] | None = None,
        contract_path: str | None = None,
    ) -> dict[str, Any]:
        validation = self.db.scalar(
            select(SubAppBundleValidation).where(
                SubAppBundleValidation.id == validation_id,
                SubAppBundleValidation.workspace_id == self.workspace_id,
                SubAppBundleValidation.owner_user_id == self.actor_id,
                SubAppBundleValidation.chat_session_id == chat_session_id,
            )
        )
        if validation is None:
            raise AppError(404, "subapp_bundle_validation_not_found", "Bundle validation was not found")
        if validation.status != "passed" or validation.consumed_at is not None or _as_utc(validation.expires_at) < utc_now():
            raise AppError(409, "subapp_bundle_validation_unusable", "Bundle validation is failed, expired, or already published")
        if sandbox_session_id and validation.sandbox_session_id and sandbox_session_id != validation.sandbox_session_id:
            raise AppError(409, "subapp_bundle_validation_session_mismatch", "Validation belongs to a different sandbox session")
        if contract_path is not None:
            if interaction_contract is not None:
                raise AppError(
                    409,
                    "subapp_bundle_contract_conflict",
                    "Pass either interaction_contract or contract_path, not both",
                )
            interaction_contract = self._load_contract_from_workspace(
                chat_session_id, contract_path
            )
        bundle = SubAppBundle(
            workspace_id=self.workspace_id,
            owner_user_id=self.actor_id,
            chat_session_id=chat_session_id,
            sandbox_session_id=validation.sandbox_session_id,
            validation_id=validation.id,
            title=" ".join(title.split())[:255] or "交互式教学应用",
            entry_path=validation.entry_path,
            manifest_json=validation.manifest_json,
            manifest_sha256=validation.manifest_sha256,
            preferred_height=max(160, min(900, int(preferred_height or 420))),
            interaction_contract=interaction_contract,
        )
        self.db.add(bundle)
        self.db.flush()
        for item in validation.manifest_json.get("files", []):
            self.db.add(
                SubAppBundleFile(
                    workspace_id=self.workspace_id,
                    bundle_id=bundle.id,
                    path=item["path"],
                    blob_sha256=item["sha256"],
                    mime_type=item["mime_type"],
                    size_bytes=item["size_bytes"],
                )
            )
        validation.consumed_at = utc_now()
        component_manifest_id: str | None = None
        contract_smoke: dict[str, Any] | None = None
        if interaction_contract is not None:
            component_manifest_id, contract_smoke = self._create_interactive_manifest(
                bundle=bundle,
                interaction_contract=interaction_contract,
                chat_session_id=chat_session_id,
            )
            bundle.component_manifest_id = component_manifest_id
        self.audit.record(
            actor_id=self.actor_id,
            action="subapp.bundle_published",
            resource_type="subapp_bundle",
            resource_id=bundle.id,
            details={
                "validation_id": validation.id,
                "manifest_sha256": bundle.manifest_sha256,
                "subapp_mode": component_manifest_id is not None,
                "contract_smoke": contract_smoke,
            },
        )
        self.db.commit()
        return {
            "bundle_id": bundle.id,
            "title": bundle.title,
            "entry_path": bundle.entry_path,
            "manifest_sha256": bundle.manifest_sha256,
            "status": bundle.status,
            "subapp_mode": component_manifest_id is not None,
            "artifact_version_id": component_manifest_id,
            "contract_smoke": contract_smoke,
            "part": {
                "type": "subapp_artifact",
                "status": "completed",
                "data": {
                    "bundle_id": bundle.id,
                    "title": bundle.title,
                    "runtime": "opaque-origin-subapp-v1",
                    "preferred_height": bundle.preferred_height,
                    "chat_session_id": chat_session_id,
                    "sandbox_session_id": bundle.sandbox_session_id,
                    "validation_status": "passed",
                    "subapp_mode": component_manifest_id is not None,
                    "artifact_version_id": component_manifest_id,
                    "contract_smoke": contract_smoke,
                },
            },
        }

    def _create_interactive_manifest(
        self,
        *,
        bundle: SubAppBundle,
        interaction_contract: dict[str, Any],
        chat_session_id: str,
    ) -> str:
        """Create a lightweight contract-bearing manifest for a bidirectional subapp.

        The bundle is the durable source of truth; this manifest exists only so
        ``SubAppService.create_session`` can snapshot ``event_schema`` /
        ``state_schema`` and instantiate a T2.6 interactive session. It deliberately
        bypasses the third-party component registration flow (no health/render
        checks, no signature metadata) because the Agent is already the authorized
        publisher of this chat session's sandbox output, but still owns a minimal
        internal ``PluginRecord`` so the manifest foreign key is satisfied.
        Contract validation reuses the same closed-schema guards as component
        registration. Returns ``(manifest_id, smoke_report)``.
        """
        from app.services.components import _interaction_contract_guard

        if not isinstance(interaction_contract, dict):
            raise AppError(
                422,
                "subapp_interaction_contract_invalid",
                "interaction_contract must be an object",
            )
        event_schema = interaction_contract.get("event_schema")
        state_schema = interaction_contract.get("state_schema")
        if not isinstance(event_schema, dict) or not isinstance(state_schema, dict):
            raise AppError(
                422,
                "subapp_interaction_contract_incomplete",
                "interaction_contract requires both event_schema and state_schema",
            )
        try:
            _interaction_contract_guard(interaction_contract)
        except AppError as exc:
            raise AppError(
                422,
                "subapp_interaction_contract_invalid",
                f"Invalid interaction contract: {exc.message}",
                exc.details,
            ) from exc

        try:
            validator_for(event_schema).check_schema(event_schema)
            validator_for(state_schema).check_schema(state_schema)
        except (ValidationError, SchemaError) as exc:
            raise AppError(
                422,
                "subapp_interaction_contract_invalid",
                "Interaction contract contains an invalid JSON Schema",
                {"validation_error": type(exc).__name__},
            ) from None

        # Protocol smoke check: prove the event contract is instantiable so the
        # host can persist at least one event against it. Skipped (not failed)
        # when the schema cannot be heuristically synthesized.
        smoke: dict[str, Any] = {"status": "skipped"}
        try:
            from app.services.components import build_minimal_schema_instance

            sample, ok = build_minimal_schema_instance(event_schema)
            if ok:
                validator_for(event_schema)(event_schema).validate(sample)
                smoke = {"status": "passed", "sample_payload": sample}
        except Exception as exc:  # noqa: BLE001 — smoke must never block publish
            smoke = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }

        from app.domain.schemas.components import COMPONENT_ID_PATTERN
        import re

        component_id = f"bundle_{bundle.id[:24]}"
        if not re.fullmatch(COMPONENT_ID_PATTERN, component_id):
            raise AppError(
                422,
                "subapp_component_id_invalid",
                "Derived sub-application component id is not a valid component id",
            )
        # `component_manifest_versions.plugin_id` is a foreign key to
        # `plugins.id`. Agent-published bundles do not go through the third-party
        # component registration flow, so create a lightweight internal plugin
        # record as the manifest owner instead of writing an empty plugin id.
        plugin_key = f"agent-subapp-{bundle.id[:24]}"
        plugin = PluginRecord(
            workspace_id=self.workspace_id,
            plugin_key=plugin_key,
            name=bundle.title,
            version="1.0.0",
            plugin_type="agent_subapp",
            status="configured",
            enabled=False,
            permissions=[],
            capabilities=[],
        )
        self.db.add(plugin)
        self.db.flush()
        data_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        }
        schema_hash = _sha256(
            _canonical_json(
                {
                    "data_schema": data_schema,
                    "event_schema": event_schema,
                    "interaction_contract": interaction_contract,
                }
            )
        )
        permissions: dict[str, Any] = {
            "network_domains": [],
            "file_read": False,
            "clipboard_write": False,
            "message_actions": ["submit"],
        }
        permissions_hash = _sha256(_canonical_json(permissions))
        material = {
            "component_id": component_id,
            "version": "1.0.0",
            "display_name": bundle.title,
            "renderer": "sandbox",
            "source": "agent_subapp",
            "author": "LearnGraph Agent",
            "package_hash": bundle.manifest_sha256,
            "compatible_learngraph": {"minimum": "0.1.0"},
            "data_schema": data_schema,
            "event_schema": event_schema,
            "interaction_contract": interaction_contract,
            "permissions": permissions,
            "size_limits": {
                "min_height": 80,
                "max_height": 720,
            },
        }
        manifest_hash = _sha256(_canonical_json(material))
        manifest = ComponentManifestVersion(
            workspace_id=self.workspace_id,
            plugin_id=plugin.id,
            component_id=component_id,
            version="1.0.0",
            display_name=bundle.title,
            renderer="sandbox",
            source="agent_subapp",
            author="LearnGraph Agent",
            package_hash=bundle.manifest_sha256,
            package_hash_status="declared_unverified",
            signature_status="unsigned",
            signature_info={},
            compatible_learngraph={"minimum": "0.1.0"},
            uninstall_behavior="retain_data",
            data_schema=data_schema,
            event_schema=event_schema,
            interaction_contract=interaction_contract,
            permissions=permissions,
            size_limits={"min_height": 80, "max_height": 720},
            skill_triggers=[],
            example_data={},
            schema_hash=schema_hash,
            permissions_hash=permissions_hash,
            manifest_hash=manifest_hash,
            issuer_id=None,
            trusted_bundle_eligible=False,
        )
        self.db.add(manifest)
        self.db.flush()
        self.audit.record(
            actor_id=self.actor_id,
            action="subapp.interactive_manifest_created",
            resource_type="component_manifest_version",
            resource_id=manifest.id,
            details={
                "bundle_id": bundle.id,
                "component_id": component_id,
                "chat_session_id": chat_session_id,
                "smoke": smoke,
            },
        )
        return manifest.id, smoke

    def read_file(self, bundle_id: str, path: str) -> tuple[SubAppBundleFile, ContentBlob]:
        """Read one bundle file through the main API (host-bridge VFS channel).

        Authorization is the authenticated workspace+owner of the bundle itself
        (``_bundle``); no capability token is needed because the caller is the
        host bridge acting on the user's own session. Used by the frontend
        sandbox ``vfs.read`` relay for multi-file projects.
        """
        bundle = self._bundle(bundle_id)
        if bundle.status != "published" or bundle.revoked_at is not None:
            raise AppError(409, "subapp_bundle_revoked", "Bundle is no longer available")
        normalized = _normalize_bundle_path(path)
        item = self.db.scalar(
            select(SubAppBundleFile).where(
                SubAppBundleFile.bundle_id == bundle.id,
                SubAppBundleFile.workspace_id == bundle.workspace_id,
                SubAppBundleFile.path == normalized,
            )
        )
        if item is None:
            raise AppError(404, "subapp_bundle_asset_not_found", "Bundle file is not part of this bundle")
        blob = self.db.scalar(
            select(ContentBlob).where(
                ContentBlob.workspace_id == bundle.workspace_id,
                ContentBlob.sha256 == item.blob_sha256,
            )
        )
        if blob is None:
            raise AppError(404, "subapp_bundle_asset_not_found", "Bundle file bytes are unavailable")
        return item, blob

    def mint_preview(self, bundle_id: str) -> dict[str, Any]:
        bundle = self._bundle(bundle_id)
        if bundle.status != "published" or bundle.revoked_at is not None:
            raise AppError(409, "subapp_bundle_revoked", "Bundle preview is no longer available")
        from app.services.sandbox_preview_config import (
            effective_subapp_preview_origin,
            validate_preview_origin,
        )

        origin = effective_subapp_preview_origin(self.settings)
        if not origin:
            raise AppError(
                503,
                "subapp_preview_origin_unconfigured",
                "Independent subapp preview origin is not configured",
            )
        try:
            origin = validate_preview_origin(origin)
        except ValueError as exc:
            raise AppError(
                503,
                "subapp_preview_origin_invalid",
                f"Subapp preview origin is invalid: {exc}",
            ) from exc
        raw = secrets.token_urlsafe(32)
        grant = SubAppBundlePreviewGrant(
            workspace_id=self.workspace_id,
            bundle_id=bundle.id,
            owner_user_id=self.actor_id,
            token_hash=_sha256(raw),
            expires_at=utc_now() + timedelta(seconds=PREVIEW_GRANT_TTL_SECONDS),
        )
        self.db.add(grant)
        self.db.commit()
        return {
            "bundle_id": bundle.id,
            "expires_at": grant.expires_at,
            "url": f"{origin}/api/v1/subapps/preview/{raw}/{bundle.id}/{bundle.entry_path}",
        }

    def resolve_preview(self, raw_token: str, bundle_id: str, path: str) -> tuple[SubAppBundle, SubAppBundleFile, ContentBlob]:
        token_hash = _sha256(raw_token)
        grant = self.db.scalar(
            select(SubAppBundlePreviewGrant).where(SubAppBundlePreviewGrant.token_hash == token_hash)
        )
        if grant is None or grant.bundle_id != bundle_id or grant.revoked_at is not None or _as_utc(grant.expires_at) < utc_now():
            raise AppError(404, "subapp_preview_not_found", "Preview capability is invalid or expired")
        bundle = self.db.scalar(
            select(SubAppBundle).where(
                SubAppBundle.id == bundle_id,
                SubAppBundle.workspace_id == grant.workspace_id,
                SubAppBundle.owner_user_id == grant.owner_user_id,
            )
        )
        if bundle is None or bundle.status != "published" or bundle.revoked_at is not None:
            raise AppError(404, "subapp_preview_not_found", "Preview bundle is unavailable")
        normalized = _normalize_bundle_path(path)
        item = self.db.scalar(
            select(SubAppBundleFile).where(
                SubAppBundleFile.bundle_id == bundle.id,
                SubAppBundleFile.workspace_id == bundle.workspace_id,
                SubAppBundleFile.path == normalized,
            )
        )
        if item is None:
            raise AppError(404, "subapp_preview_asset_not_found", "Preview asset is not part of this bundle")
        blob = self.db.scalar(
            select(ContentBlob).where(
                ContentBlob.workspace_id == bundle.workspace_id,
                ContentBlob.sha256 == item.blob_sha256,
            )
        )
        if blob is None:
            raise AppError(404, "subapp_preview_asset_not_found", "Preview asset bytes are unavailable")
        return bundle, item, blob

    def revoke(self, bundle_id: str) -> None:
        bundle = self._bundle(bundle_id)
        bundle.status = "revoked"
        bundle.revoked_at = utc_now()
        self.audit.record(actor_id=self.actor_id, action="subapp.bundle_revoked", resource_type="subapp_bundle", resource_id=bundle.id)
        self.db.commit()

    def _bundle(self, bundle_id: str) -> SubAppBundle:
        bundle = self.db.scalar(
            select(SubAppBundle).where(
                SubAppBundle.id == bundle_id,
                SubAppBundle.workspace_id == self.workspace_id,
                SubAppBundle.owner_user_id == self.actor_id,
            )
        )
        if bundle is None:
            raise AppError(404, "subapp_bundle_not_found", "Subapp bundle was not found")
        return bundle
