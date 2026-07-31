from __future__ import annotations

import hashlib
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.domain.memory_event_models import (
    MemoryAccessLog,
    MemoryContextPackage,
    MemoryScopeContext,
)
from app.domain.schemas.context_builds import ContextBuildRequest, ContextBuildView
from app.services.context_builder import ContextBuilder


class ContextTelemetryWriter:
    """Best-effort post-build telemetry; failures must not break chat."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def write(
        self,
        scope: MemoryScopeContext,
        request: ContextBuildRequest,
        view: ContextBuildView,
    ) -> None:
        try:
            row = MemoryContextPackage(
                id=view.context_build_id,
                workspace_id=scope.workspace_id,
                request_hash=hashlib.sha256(
                    f"{request.conversation_id}\0{request.task_id}\0{request.query}".encode("utf-8")
                ).hexdigest(),
                query_hash=hashlib.sha256(request.query.encode("utf-8")).hexdigest(),
                scope_hash=hashlib.sha256(
                    f"{scope.tenant_id}\0{scope.principal_user_id}\0{scope.workspace_id}\0{scope.task_id}".encode("utf-8")
                ).hexdigest(),
                policy_version=ContextBuilder.POLICY_VERSION,
                builder_version=ContextBuilder.VERSION,
                agent_id=request.agent_id,
                provider_id=request.provider_id,
                model_id=request.model_id,
                candidate_ids_json=[item.target_id for item in view.memories],
                selected_ids_json=[item.target_id for item in view.memories],
                excluded_counts_json=view.excluded,
                dropped_reasons_json={},
                section_token_usage_json=view.section_tokens,
                total_tokens=view.total_tokens,
                package_hash=view.package_hash,
            )
            self.db.merge(row)
            for item in view.memories:
                self.db.add(
                    MemoryAccessLog(
                        workspace_id=scope.workspace_id,
                        context_build_id=view.context_build_id,
                        memory_id=item.target_id,
                        source_event_id=item.source_event_id,
                        agent_id=request.agent_id,
                        retrieval_reason=item.retrieval_reason,
                        component_scores_json=item.component_scores,
                        injected_tokens=0,
                        used_as="context",
                    )
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
