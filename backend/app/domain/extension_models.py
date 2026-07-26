from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domain.models import TimestampMixin, WorkspaceScopedMixin, new_id


class MCPServer(Base, TimestampMixin, WorkspaceScopedMixin):
    """A workspace-owned MCP endpoint and its reviewed execution envelope."""

    __tablename__ = "mcp_servers"
    __table_args__ = (
        UniqueConstraint("workspace_id", "server_key", name="uq_mcp_server_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    server_key: Mapped[str] = mapped_column(String(80), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(80))
    transport: Mapped[str] = mapped_column(String(40), index=True)
    endpoint_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    auth_reference: Mapped[str | None] = mapped_column(String(36), nullable=True)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    manifest_hash: Mapped[str] = mapped_column(String(64), index=True)
    requested_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    required_permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(40), default="registered", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    agent_auto_invoke: Mapped[bool] = mapped_column(Boolean, default=False)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=5_000)
    max_input_bytes: Mapped[int] = mapped_column(Integer, default=64 * 1024)
    max_result_bytes: Mapped[int] = mapped_column(Integer, default=256 * 1024)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    current_snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    authorization_generation: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MCPCapabilitySnapshot(Base, TimestampMixin, WorkspaceScopedMixin):
    """Immutable result of one real MCP capability refresh."""

    __tablename__ = "mcp_capability_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "server_id", "sequence", name="uq_mcp_snapshot_sequence"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    server_id: Mapped[str] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    protocol_version: Mapped[str] = mapped_column(String(40))
    server_identity: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tools: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    resources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    prompts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    required_permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    changed: Mapped[bool] = mapped_column(Boolean, default=True)
    reauthorization_required: Mapped[bool] = mapped_column(Boolean, default=True)


class MCPServerCredential(Base, TimestampMixin, WorkspaceScopedMixin):
    """Encrypted static bearer reference; plaintext is never returned or audited."""

    __tablename__ = "mcp_server_credentials"
    __table_args__ = (
        UniqueConstraint("workspace_id", "server_id", name="uq_mcp_server_credential"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    server_id: Mapped[str] = mapped_column(
        ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True
    )
    ciphertext: Mapped[str] = mapped_column(Text)
    secret_masked: Mapped[str] = mapped_column(String(80))
    secret_fingerprint: Mapped[str] = mapped_column(String(64), index=True)


class SkillRecord(Base, TimestampMixin, WorkspaceScopedMixin):
    """A workspace-local Skill: declarative tools and/or an Agent Skills file package."""

    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("workspace_id", "skill_key", name="uq_skill_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    skill_key: Mapped[str] = mapped_column(String(80), index=True)
    name: Mapped[str] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(80))
    generated_by: Mapped[str] = mapped_column(String(40), default="user_import")
    # declarative_* | agent_skill_package (D-077)
    kind: Mapped[str] = mapped_column(String(40), default="declarative_review", index=True)
    package_format: Mapped[str] = mapped_column(String(40), default="declarative_json")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    origin_type: Mapped[str] = mapped_column(String(40), default="user_import")
    origin_ref: Mapped[str] = mapped_column(String(500), default="")
    origin_hash: Mapped[str] = mapped_column(String(64), default="")
    has_scripts: Mapped[bool] = mapped_column(Boolean, default=False)
    locale_source: Mapped[str] = mapped_column(String(32), default="")
    # First-class flag for official (first-party) workflow skills. Official
    # rows are system-managed: users cannot edit, revoke, or delete them.
    is_official: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    manifest_hash: Mapped[str] = mapped_column(String(64), index=True)
    instructions_markdown: Mapped[str] = mapped_column(Text, default="")
    required_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    required_permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_components: Mapped[list[str]] = mapped_column(JSON, default=list)
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="authorization_required", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    authorization_generation: Mapped[int] = mapped_column(Integer, default=0)


class SkillPackageFile(Base, TimestampMixin, WorkspaceScopedMixin):
    """One file in an agent_skill_package tree; bytes live in ContentBlob by sha256."""

    __tablename__ = "skill_package_files"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "skill_id", "relative_path", name="uq_skill_package_file_path"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), index=True
    )
    relative_path: Mapped[str] = mapped_column(String(500))
    blob_sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str] = mapped_column(String(160), default="text/plain")
    is_directory: Mapped[bool] = mapped_column(Boolean, default=False)


class ExtensionPermissionGrant(Base, TimestampMixin, WorkspaceScopedMixin):
    """One reviewed allow-once/always/deny decision bound to immutable content."""

    __tablename__ = "extension_permission_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    subject_type: Mapped[str] = mapped_column(String(32), index=True)
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    decision: Mapped[str] = mapped_column(String(24), index=True)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    authorization_hash: Mapped[str] = mapped_column(String(64), index=True)
    decided_by: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExtensionInvocation(Base, TimestampMixin, WorkspaceScopedMixin):
    """Bounded, auditable MCP or Skill execution record."""

    __tablename__ = "extension_invocations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    target_type: Mapped[str] = mapped_column(String(32), index=True)
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    skill_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    tool_name: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    grant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    authorization_hash: Mapped[str] = mapped_column(String(64), default="")
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    result_hash: Mapped[str] = mapped_column(String(64), default="")
    timeout_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SkillMarketCacheEntry(Base, TimestampMixin):
    """System-level pre-cache of popular Agent Skills (D-078). Not workspace-authorized."""

    __tablename__ = "skill_market_cache"
    __table_args__ = (UniqueConstraint("market_id", name="uq_skill_market_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    market_id: Mapped[str] = mapped_column(String(200), index=True)
    slug: Mapped[str] = mapped_column(String(160), default="")
    name: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    install_url: Mapped[str] = mapped_column(String(500), default="")
    homepage_url: Mapped[str] = mapped_column(String(500), default="")
    installs: Mapped[int] = mapped_column(Integer, default=0)
    source_type: Mapped[str] = mapped_column(String(40), default="seed")
    origin_hash: Mapped[str] = mapped_column(String(64), default="")
    files_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetch_status: Mapped[str] = mapped_column(String(40), default="seeded")
    fetch_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)


class SkillTranslationCache(Base, TimestampMixin, WorkspaceScopedMixin):
    """View-layer translation cache keyed by content hash + locale + model (D-081)."""

    __tablename__ = "skill_translation_cache"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "content_hash",
            "target_locale",
            "translator_model_id",
            "source_path",
            name="uq_skill_translation_cache",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    skill_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_path: Mapped[str] = mapped_column(String(500), default="SKILL.md")
    source_locale: Mapped[str] = mapped_column(String(32), default="")
    target_locale: Mapped[str] = mapped_column(String(32), index=True)
    translator_model_id: Mapped[str] = mapped_column(String(160), default="")
    translator_provider_id: Mapped[str] = mapped_column(String(36), default="")
    translated_text: Mapped[str] = mapped_column(Text, default="")
    usage_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class SkillLocalProbePolicy(Base, TimestampMixin, WorkspaceScopedMixin):
    """Per-workspace opt-in policy for same-host local skill discovery (D-079)."""

    __tablename__ = "skill_local_probe_policies"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_skill_local_probe_workspace"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_roots_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scan_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
