from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import bindparam, delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.errors import AppError
from app.domain.models import (
    DocumentCollection,
    DocumentCollectionItem,
    DocumentJob,
    DocumentJobEvent,
    DocumentRevision,
    FileRecord,
    FileTextChunk,
    Goal,
    Graph,
    Project,
    RetrievalHit,
    RetrievalTrace,
    utc_now,
)
from app.domain.schemas.files import (
    DocumentCollectionCreate,
    DocumentCollectionItemView,
    DocumentCollectionView,
    DocumentJobCreate,
    DocumentQueryHitView,
    DocumentQueryPreviewRequest,
    DocumentQueryPreviewView,
    DocumentSelectionStatus,
)
from app.providers.storage_factory import object_storage_provider
from app.repositories.audit import AuditRepository
from app.repositories.domain import FileRepository, FileTextChunkRepository
from app.services.document_parsers import (
    DocumentParseError,
    ProcessorUnavailable,
    isolated_text_document,
    parse_document,
)


class _DocumentJobExecutionStopped(Exception):
    """The durable job was cancelled or superseded by a newer retry run."""


CHUNKER_VERSION = "structure-char-v1"
CHUNK_TARGET_CHARS = 2_800


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_selection_text(value: str) -> str:
    """Match browser selections against parser output without trusting layout whitespace."""

    return re.sub(r"\s+", " ", value, flags=re.UNICODE).strip()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


def _locator_json(locator: str) -> dict[str, Any]:
    patterns = (
        (r"^page:(\d+)", "page"),
        (r"^slide:(\d+)", "slide"),
        (r"^paragraph:(\d+)", "paragraph"),
        (r"^image:(\d+)", "image"),
    )
    for pattern, key in patterns:
        match = re.match(pattern, locator)
        if match:
            return {key: int(match.group(1)), "display": locator}
    line_match = re.match(r"^lines:(\d+)-(\d+)", locator)
    if line_match:
        return {
            "line_start": int(line_match.group(1)),
            "line_end": int(line_match.group(2)),
            "display": locator,
        }
    sheet_match = re.match(r"^sheet:(\d+)!row:(.+)", locator)
    if sheet_match:
        return {
            "sheet": int(sheet_match.group(1)),
            "row": sheet_match.group(2),
            "display": locator,
        }
    return {"display": locator}


def _split_text(content: str, target: int = CHUNK_TARGET_CHARS) -> list[str]:
    if len(content) <= target:
        return [content]
    parts: list[str] = []
    remaining = content
    while remaining:
        split_at = max(
            remaining.rfind("\n\n", 0, target),
            remaining.rfind("\n", 0, target),
            remaining.rfind("。", 0, target),
            remaining.rfind(". ", 0, target),
        )
        if split_at < target // 2:
            split_at = target
        else:
            split_at += 1
        part = remaining[:split_at].strip()
        if part:
            parts.append(part)
        remaining = remaining[split_at:].lstrip()
    return parts


def _section_path(content: str, current: list[str]) -> list[str]:
    first = content.splitlines()[0].strip() if content.strip() else ""
    match = re.match(r"^(#{1,6})\s+(.+)$", first)
    if not match:
        return current
    level = len(match.group(1))
    title = match.group(2).strip()[:240]
    return [*current[: level - 1], title]


def _fts_query(value: str) -> tuple[str, list[str]]:
    """Build a bounded OR query that works for identifiers, words, and CJK text.

    FTS5 trigram search is useful for Chinese, but an entire natural-language
    question as one quoted phrase is far too strict.  Keep the full phrase as
    one candidate and add bounded lexical/trigram candidates for recall.
    """

    normalized = " ".join(value.strip().split())
    candidates: list[str] = [normalized] if normalized else []
    candidates.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:-]{1,}", normalized))
    for sequence in re.findall(r"[\u3400-\u9fff]{2,}", normalized):
        if len(sequence) <= 4:
            candidates.append(sequence)
            continue
        candidates.extend(sequence[index : index + 3] for index in range(len(sequence) - 2))
    terms = list(dict.fromkeys(term for term in candidates if term))[:24]
    escaped = ['"' + term.replace('"', '""') + '"' for term in terms]
    return " OR ".join(escaped), terms


class DocumentLearningService:
    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.files = FileRepository(db, workspace_id)
        self.chunks = FileTextChunkRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)
        self.storage = object_storage_provider(db, workspace_id, settings)

    def list_collections(self) -> list[DocumentCollectionView]:
        collections = list(
            self.db.scalars(
                select(DocumentCollection)
                .where(DocumentCollection.workspace_id == self.workspace_id)
                .order_by(DocumentCollection.updated_at.desc(), DocumentCollection.id)
            ).all()
        )
        return [self._collection_view(item) for item in collections]

    def get_collection(self, collection_id: str) -> DocumentCollection:
        collection = self.db.scalar(
            select(DocumentCollection).where(
                DocumentCollection.workspace_id == self.workspace_id,
                DocumentCollection.id == collection_id,
            )
        )
        if collection is None:
            raise AppError(404, "document_collection_not_found", "Document collection not found")
        return collection

    def create_collection(
        self,
        payload: DocumentCollectionCreate,
    ) -> DocumentCollectionView:
        for resource_id, model, code in (
            (payload.project_id, Project, "project_not_found"),
            (payload.goal_id, Goal, "goal_not_found"),
            (payload.graph_id, Graph, "graph_not_found"),
        ):
            if resource_id and not self.db.scalar(
                select(model.id).where(
                    model.workspace_id == self.workspace_id,
                    model.id == resource_id,
                )
            ):
                raise AppError(404, code, "Collection binding was not found in this workspace")
        collection = DocumentCollection(
            workspace_id=self.workspace_id,
            name=payload.name.strip(),
            description=payload.description.strip(),
            project_id=payload.project_id,
            goal_id=payload.goal_id,
            graph_id=payload.graph_id,
            created_by=self.actor_id,
        )
        self.db.add(collection)
        self.db.flush()
        if payload.file_ids:
            self._add_collection_items(collection, payload.file_ids)
        self.audit.record(
            actor_id=self.actor_id,
            action="document.collection_created",
            resource_type="document_collection",
            resource_id=collection.id,
            details={"file_count": len(set(payload.file_ids))},
        )
        self.db.commit()
        self.db.refresh(collection)
        return self._collection_view(collection)

    def add_collection_items(
        self,
        collection_id: str,
        file_ids: list[str],
    ) -> DocumentCollectionView:
        collection = self.get_collection(collection_id)
        self._add_collection_items(collection, file_ids)
        self.audit.record(
            actor_id=self.actor_id,
            action="document.collection_items_added",
            resource_type="document_collection",
            resource_id=collection.id,
            details={"file_ids": list(dict.fromkeys(file_ids))},
        )
        self.db.commit()
        self.db.refresh(collection)
        return self._collection_view(collection)

    def remove_collection_item(
        self,
        collection_id: str,
        file_id: str,
    ) -> DocumentCollectionView:
        collection = self.get_collection(collection_id)
        item = self.db.scalar(
            select(DocumentCollectionItem).where(
                DocumentCollectionItem.workspace_id == self.workspace_id,
                DocumentCollectionItem.collection_id == collection.id,
                DocumentCollectionItem.file_id == file_id,
            )
        )
        if item is None:
            raise AppError(404, "document_collection_item_not_found", "Collection item not found")
        self.db.delete(item)
        self.audit.record(
            actor_id=self.actor_id,
            action="document.collection_item_removed",
            resource_type="document_collection",
            resource_id=collection.id,
            details={"file_id": file_id},
        )
        self.db.commit()
        self.db.refresh(collection)
        return self._collection_view(collection)

    def resolve_query_file_ids(
        self,
        file_ids: list[str],
        collection_ids: list[str],
    ) -> list[str]:
        resolved = list(dict.fromkeys(file_ids))
        for collection_id in dict.fromkeys(collection_ids):
            collection = self.get_collection(collection_id)
            member_ids = self.db.scalars(
                select(DocumentCollectionItem.file_id)
                .where(
                    DocumentCollectionItem.workspace_id == self.workspace_id,
                    DocumentCollectionItem.collection_id == collection.id,
                )
                .order_by(DocumentCollectionItem.created_at, DocumentCollectionItem.id)
            ).all()
            resolved.extend(member_ids)
        return list(dict.fromkeys(resolved))

    def _add_collection_items(
        self,
        collection: DocumentCollection,
        file_ids: list[str],
    ) -> None:
        normalized = list(dict.fromkeys(file_ids))
        records = list(
            self.db.scalars(self.files.query().where(FileRecord.id.in_(normalized))).all()
        )
        if len(records) != len(normalized):
            raise AppError(404, "file_not_found", "A collection file was not found in this workspace")
        existing = set(
            self.db.scalars(
                select(DocumentCollectionItem.file_id).where(
                    DocumentCollectionItem.workspace_id == self.workspace_id,
                    DocumentCollectionItem.collection_id == collection.id,
                    DocumentCollectionItem.file_id.in_(normalized),
                )
            ).all()
        )
        for record in records:
            if record.id in existing:
                continue
            revision_id = self.db.scalar(
                select(DocumentRevision.id)
                .where(
                    DocumentRevision.workspace_id == self.workspace_id,
                    DocumentRevision.file_id == record.id,
                    DocumentRevision.status == "succeeded",
                )
                .order_by(DocumentRevision.revision_no.desc())
            )
            self.db.add(
                DocumentCollectionItem(
                    workspace_id=self.workspace_id,
                    collection_id=collection.id,
                    file_id=record.id,
                    document_revision_id=revision_id,
                    added_by=self.actor_id,
                )
            )

    def _collection_view(self, collection: DocumentCollection) -> DocumentCollectionView:
        items = list(
            self.db.scalars(
                select(DocumentCollectionItem)
                .where(
                    DocumentCollectionItem.workspace_id == self.workspace_id,
                    DocumentCollectionItem.collection_id == collection.id,
                )
                .order_by(DocumentCollectionItem.created_at, DocumentCollectionItem.id)
            ).all()
        )
        return DocumentCollectionView(
            id=collection.id,
            workspace_id=collection.workspace_id,
            name=collection.name,
            description=collection.description,
            project_id=collection.project_id,
            goal_id=collection.goal_id,
            graph_id=collection.graph_id,
            created_by=collection.created_by,
            items=[DocumentCollectionItemView.model_validate(item) for item in items],
            created_at=collection.created_at,
            updated_at=collection.updated_at,
        )

    def create_job(
        self,
        file_id: str,
        payload: DocumentJobCreate,
        idempotency_key: str | None,
    ) -> tuple[DocumentJob, bool]:
        record = self.files.require(file_id, "file")
        if record.parse_capability == "attachment_only":
            raise AppError(
                422,
                "format_not_parseable",
                "The file is safely stored as an attachment only",
            )
        normalized_key = (idempotency_key or "").strip()
        if len(normalized_key) > 128:
            raise AppError(422, "invalid_idempotency_key", "Idempotency-Key is too long")
        request_hash = _hash(
            json.dumps(
                {"file_id": file_id, **payload.model_dump(mode="json")},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        key_hash = _hash(normalized_key) if normalized_key else None
        if key_hash:
            existing = self.db.scalar(
                select(DocumentJob).where(
                    DocumentJob.workspace_id == self.workspace_id,
                    DocumentJob.idempotency_key_hash == key_hash,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise AppError(
                        409,
                        "idempotency_key_reused",
                        "The Idempotency-Key was already used with different parameters",
                    )
                return existing, False
        job = DocumentJob(
            workspace_id=self.workspace_id,
            file_id=file_id,
            job_type=payload.job_type,
            status="queued",
            stage="validate",
            progress=0,
            idempotency_key_hash=key_hash,
            request_hash=request_hash,
            parameters=payload.model_dump(mode="json"),
            execution_token=str(uuid4()),
            created_by=self.actor_id,
        )
        self.db.add(job)
        self.db.flush()
        self._event(job, "job.queued", {"stage": "validate", "progress": 0})
        self.audit.record(
            actor_id=self.actor_id,
            action="document.job_created",
            resource_type="document_job",
            resource_id=job.id,
            details={"file_id": file_id, "job_type": payload.job_type},
        )
        self.db.commit()
        self.db.refresh(job)
        return job, True

    @staticmethod
    def execution_token(job: DocumentJob) -> str:
        return job.execution_token or ""

    def get_job(self, job_id: str) -> DocumentJob:
        job = self.db.scalar(
            select(DocumentJob).where(
                DocumentJob.workspace_id == self.workspace_id,
                DocumentJob.id == job_id,
            )
        )
        if job is None:
            raise AppError(404, "document_job_not_found", "Document job not found")
        return job

    def job_events(self, job_id: str) -> list[DocumentJobEvent]:
        self.get_job(job_id)
        return list(
            self.db.scalars(
                select(DocumentJobEvent)
                .where(
                    DocumentJobEvent.workspace_id == self.workspace_id,
                    DocumentJobEvent.job_id == job_id,
                )
                .order_by(DocumentJobEvent.sequence)
            ).all()
        )

    def revisions(self, file_id: str) -> list[DocumentRevision]:
        self.files.require(file_id, "file")
        return list(
            self.db.scalars(
                select(DocumentRevision)
                .where(
                    DocumentRevision.workspace_id == self.workspace_id,
                    DocumentRevision.file_id == file_id,
                )
                .order_by(DocumentRevision.revision_no.desc())
            ).all()
        )

    def retry(self, job_id: str) -> DocumentJob:
        job = self.get_job(job_id)
        if job.status not in {"failed", "interrupted", "cancelled"}:
            raise AppError(
                409,
                "document_job_not_retryable",
                "Only failed, interrupted, or cancelled jobs can be retried",
            )
        job.status = "queued"
        job.stage = "validate"
        job.progress = 0
        job.error_code = None
        job.error_message = None
        job.started_at = None
        job.completed_at = None
        job.execution_token = str(uuid4())
        self._event(job, "job.retried", {"progress": 0})
        self.db.commit()
        self.db.refresh(job)
        return job

    def cancel(self, job_id: str) -> DocumentJob:
        job = self.get_job(job_id)
        if job.status in {"completed", "failed", "cancelled"}:
            return job
        job.status = "cancelled"
        job.completed_at = utc_now()
        self._event(job, "job.cancelled", {"stage": job.stage, "progress": job.progress})
        self.audit.record(
            actor_id=self.actor_id,
            action="document.job_cancelled",
            resource_type="document_job",
            resource_id=job.id,
            details={"file_id": job.file_id, "stage": job.stage},
        )
        self.db.commit()
        self.db.refresh(job)
        return job

    def execute_job(
        self,
        job_id: str,
        *,
        raise_errors: bool = False,
        expected_execution_token: str | None = None,
    ) -> DocumentJob:
        job = self.get_job(job_id)
        if job.status == "completed":
            return job
        if job.status == "cancelled":
            return job
        execution_token = expected_execution_token or self.execution_token(job)
        if expected_execution_token and self.execution_token(job) != expected_execution_token:
            return job
        job = self._claim_execution(job, execution_token)
        if job is None:
            self.db.rollback()
            return self.get_job(job_id)
        try:
            self._set_stage(job, "validate", 5, execution_token)
            record = self.files.require(job.file_id, "file")
            reusable = self.db.scalar(
                select(DocumentRevision)
                .where(
                    DocumentRevision.workspace_id == self.workspace_id,
                    DocumentRevision.file_id == record.id,
                    DocumentRevision.source_sha256 == record.sha256,
                    DocumentRevision.config_hash == _hash(CHUNKER_VERSION),
                    DocumentRevision.status == "succeeded",
                )
                .order_by(DocumentRevision.revision_no.desc())
            )
            if reusable is not None:
                job.document_revision_id = reusable.id
                self._sync_fts(record.id)
                self._complete(job, reusable, reused=True, execution_token=execution_token)
                return job

            revision_no = int(
                self.db.scalar(
                    select(func.max(DocumentRevision.revision_no)).where(
                        DocumentRevision.workspace_id == self.workspace_id,
                        DocumentRevision.file_id == record.id,
                    )
                )
                or 0
            ) + 1
            revision = DocumentRevision(
                workspace_id=self.workspace_id,
                file_id=record.id,
                revision_no=revision_no,
                source_sha256=record.sha256,
                size_bytes=record.size_bytes,
                mime_detected=record.mime_type,
                config_hash=_hash(CHUNKER_VERSION),
                status="running",
                created_by=self.actor_id,
            )
            self.db.add(revision)
            self.db.flush()
            job.document_revision_id = revision.id
            self.db.commit()

            self._set_stage(job, "parse", 20, execution_token)
            if Path(record.original_name).suffix.casefold() == ".doc":
                from app.providers.remote.sandbox import (
                    SandboxBackendError,
                    SandboxBackendUnavailable,
                )
                from app.services.sandbox import SandboxService

                try:
                    artifact = SandboxService(
                        self.db,
                        self.workspace_id,
                        self.actor_id,
                        self.settings,
                    ).extract_legacy_doc(record)
                except (SandboxBackendError, SandboxBackendUnavailable) as exc:
                    raise ProcessorUnavailable(str(exc)) from exc
                parsed = isolated_text_document(
                    artifact["text"],
                    parser_name=str(artifact.get("parser_name") or "antiword"),
                    parser_version=str(artifact.get("parser_version") or "0.37"),
                )
            else:
                payload = self.storage.read_bytes(
                    record.object_key,
                    limit_bytes=self.settings.max_document_parse_bytes,
                )
                parsed = parse_document(record.original_name, payload)
            revision.processor_id = parsed.parser_name
            revision.processor_version = parsed.parser_version

            self._set_stage(job, "chunk", 50, execution_token)
            # Keep immutable revision chunks for provenance. Only this revision is
            # replaced on an idempotent retry; activation below stales prior rows.
            self.db.execute(
                text(
                    "DELETE FROM document_chunks_fts WHERE chunk_id IN ("
                    "SELECT id FROM file_text_chunks WHERE workspace_id = :workspace_id "
                    "AND file_id = :file_id AND document_revision_id = :revision_id)"
                ),
                {
                    "workspace_id": self.workspace_id,
                    "file_id": record.id,
                    "revision_id": revision.id,
                },
            )
            self.db.execute(
                delete(FileTextChunk).where(
                    FileTextChunk.workspace_id == self.workspace_id,
                    FileTextChunk.file_id == record.id,
                    FileTextChunk.document_revision_id == revision.id,
                )
            )
            ordinal = 0
            headings: list[str] = []
            for parsed_chunk in parsed.chunks:
                headings = _section_path(parsed_chunk.content, headings)
                parts = _split_text(parsed_chunk.content)
                for part_index, content in enumerate(parts, start=1):
                    ordinal += 1
                    locator = parsed_chunk.locator
                    locator_data = _locator_json(locator)
                    if len(parts) > 1:
                        locator = f"{locator}#part:{part_index}"
                        locator_data["part"] = part_index
                    chunk = FileTextChunk(
                        workspace_id=self.workspace_id,
                        file_id=record.id,
                        document_revision_id=revision.id,
                        ordinal=ordinal,
                        locator=locator,
                        locator_json=locator_data,
                        section_path=list(headings),
                        token_count=max(1, (len(content) + 3) // 4),
                        content=content,
                        content_hash=_hash(content),
                    )
                    self.db.add(chunk)
                    self.db.flush()
                    self.db.execute(
                        text(
                            "INSERT INTO document_chunks_fts"
                            "(chunk_id, workspace_id, file_id, content) "
                            "VALUES (:chunk_id, :workspace_id, :file_id, :content)"
                        ),
                        {
                            "chunk_id": chunk.id,
                            "workspace_id": self.workspace_id,
                            "file_id": record.id,
                            "content": "\n".join([*headings, content]),
                        },
                    )
            if ordinal == 0:
                raise DocumentParseError("The document contains no extractable text")

            self._set_stage(job, "sparse_index", 85, execution_token)
            revision.status = "succeeded"
            revision.completed_at = utc_now()
            revision.quality_report = {
                "text_complete": True,
                "layout": "basic_locator_only",
                "chunk_count": ordinal,
                "warnings": [],
            }
            revision.artifact_manifest = {
                "chunker": CHUNKER_VERSION,
                "chunk_count": ordinal,
                "sparse_index": "fts5",
            }
            record.parse_status = "indexed"
            record.parser_name = parsed.parser_name
            record.parser_version = parsed.parser_version
            record.error_message = None
            from app.domain.memory_event_models import MemoryScopeContext
            from app.services.memory_event_ingestor import event_cipher_from_settings
            from app.services.memory_event_store import MemoryEventStore
            from app.services.memory_file_invalidation import MemoryFileInvalidationService

            MemoryFileInvalidationService(
                self.db,
                MemoryEventStore(self.db, event_cipher_from_settings(self.settings)),
            ).activate_revision(
                MemoryScopeContext(
                    tenant_id="local-tenant",
                    principal_user_id=self.actor_id,
                    workspace_id=self.workspace_id,
                ),
                file_id=record.id,
                revision_id=revision.id,
                actor_id=self.actor_id,
            )
            self._complete(job, revision, reused=False, execution_token=execution_token)
            return job
        except _DocumentJobExecutionStopped:
            self.db.rollback()
            return self.get_job(job_id)
        except ProcessorUnavailable as exc:
            self._fail(
                job,
                "file_parse_capability_unavailable",
                str(exc),
                "processor_required",
                execution_token=execution_token,
            )
            if raise_errors:
                raise AppError(409, "file_parse_capability_unavailable", str(exc)) from exc
        except DocumentParseError as exc:
            self._fail(
                job,
                "document_parse_failed",
                str(exc),
                "failed",
                execution_token=execution_token,
            )
            if raise_errors:
                raise AppError(
                    422,
                    "document_parse_failed",
                    "Document parser could not extract safe text",
                ) from exc
        except Exception as exc:
            self._fail(
                job,
                "document_job_failed",
                str(exc),
                "failed",
                execution_token=execution_token,
            )
            if raise_errors:
                raise
        return job

    def preview(self, payload: DocumentQueryPreviewRequest) -> DocumentQueryPreviewView:
        file_ids = self.resolve_query_file_ids(payload.file_ids, payload.collection_ids)
        if not file_ids:
            raise AppError(409, "document_scope_empty", "The document scope contains no files")
        records = list(
            self.db.scalars(
                self.files.query().where(FileRecord.id.in_(file_ids))
            ).all()
        )
        if len(records) != len(file_ids):
            raise AppError(
                404,
                "document_scope_empty",
                "At least one file is outside this workspace",
            )
        unavailable = [item.id for item in records if item.parse_status != "indexed"]
        if unavailable:
            raise AppError(
                409,
                "index_not_ready",
                "Every file in the query scope must have a completed sparse index",
                {"file_ids": unavailable},
            )
        selected_chunk: FileTextChunk | None = None
        if payload.selected_text:
            selected_chunk = self._verify_selection(payload, file_ids)
        scoped_chunk_ids = self._scoped_chunk_ids(
            payload,
            file_ids,
        )
        repaired_index_file_ids = self._repair_sparse_index_if_needed(file_ids)
        full_document = payload.scope == "full_document"
        if full_document:
            rows = self._all_rows(
                file_ids,
                scoped_chunk_ids=scoped_chunk_ids,
            )
        else:
            rows = self._fts_rows(
                payload.query,
                file_ids,
                payload.max_results,
                scoped_chunk_ids=scoped_chunk_ids,
            )
        used_head_context_fallback = False
        # A query such as "summarize this file" often has no lexical overlap
        # with the uploaded document.  Returning an empty result in that case
        # made an indexed file look unavailable to ChatService.  The fallback
        # remains scoped to the files explicitly authorized by the caller and
        # is recorded on the retrieval trace; it is not a synthetic answer.
        if not full_document and not rows and payload.scope in {"file", "files", "selection"}:
            rows = self._head_rows(
                file_ids,
                payload.max_results,
                scoped_chunk_ids=scoped_chunk_ids,
            )
            used_head_context_fallback = bool(rows)
        if selected_chunk is not None and all(row["chunk_id"] != selected_chunk.id for row in rows):
            selected_record = next(item for item in records if item.id == selected_chunk.file_id)
            rows.insert(
                0,
                {
                    "chunk_id": selected_chunk.id,
                    "file_id": selected_chunk.file_id,
                    "document_revision_id": selected_chunk.document_revision_id,
                    "filename": selected_record.original_name,
                    "locator": selected_chunk.locator,
                    "locator_json": selected_chunk.locator_json or {},
                    "section_path": selected_chunk.section_path or [],
                    "content": selected_chunk.content,
                    "content_hash": selected_chunk.content_hash,
                    "raw_score": -1.0,
                },
            )
        if not full_document:
            rows = rows[: payload.max_results]
        if selected_chunk is not None:
            selection_status: DocumentSelectionStatus = "verified"
        elif payload.selected_text:
            selection_status = "unverified_degraded"
        else:
            selection_status = "none"
        trace = RetrievalTrace(
            workspace_id=self.workspace_id,
            actor_id=self.actor_id,
            query_hash=_hash(payload.query),
            file_ids=file_ids,
            strategy= (
                "fts5_full_document"
                if full_document
                else "fts5_bm25+head_context"
                if used_head_context_fallback
                else "fts5_bm25"
            ),
            status="completed",
            result_count=len(rows),
            diagnostics={
                "scope": payload.scope,
                "locator": payload.locator,
                "collection_ids": payload.collection_ids,
                "selected_text_verified": selected_chunk is not None,
                "selection_status": selection_status,
                "dense_retrieval": False,
                "rerank": False,
                "sparse_index_repaired_file_ids": repaired_index_file_ids,
                "head_context_fallback": used_head_context_fallback,
            },
        )
        self.db.add(trace)
        self.db.flush()
        hits: list[DocumentQueryHitView] = []
        for index, row in enumerate(rows, start=1):
            score = 1.0 / (1.0 + abs(float(row["raw_score"])))
            self.db.add(
                RetrievalHit(
                    workspace_id=self.workspace_id,
                    trace_id=trace.id,
                    chunk_id=row["chunk_id"],
                    rank=index,
                    score=score,
                    used_in_context=False,
                )
            )
            hits.append(
                DocumentQueryHitView(
                    rank=index,
                    score=score,
                    chunk_id=row["chunk_id"],
                    file_id=row["file_id"],
                    document_revision_id=row["document_revision_id"],
                    filename=row["filename"],
                    locator=row["locator"],
                    locator_json=_json_object(row["locator_json"]),
                    section_path=[str(item) for item in _json_list(row["section_path"])],
                    quote=(
                        str(row["content"])
                        if full_document
                        else str(row["content"])[:800]
                    ),
                    content_hash=row["content_hash"],
                )
            )
        self.audit.record(
            actor_id=self.actor_id,
            action="document.query_preview",
            resource_type="retrieval_trace",
            resource_id=trace.id,
            details={
                "file_ids": file_ids,
                "query_hash": trace.query_hash,
                "result_count": len(hits),
                "scope": payload.scope,
            },
        )
        self.db.commit()
        return DocumentQueryPreviewView(
            trace_id=trace.id,
            strategy=trace.strategy,
            scope=payload.scope,
            hits=hits,
            warnings=[
                "当前使用 SQLite FTS5 关键词检索；尚未配置语义召回或重排。",
                *(
                    ["本次查询未命中关键词，已使用授权文件的开头片段作为显式降级上下文。"]
                    if used_head_context_fallback
                    else []
                ),
            ],
            selection_status=selection_status,
        )

    def _repair_sparse_index_if_needed(self, file_ids: list[str]) -> list[str]:
        """Repair a durable FTS mirror when a prior parse was interrupted.

        ``file_text_chunks`` is the authoritative parsed-document record;
        SQLite FTS5 is a derived index.  Older interrupted jobs could leave a
        FileRecord marked ``indexed`` while its FTS rows were missing.  Repair
        only the requested, workspace-scoped records and keep the work inside
        the same transaction as the retrieval trace.
        """

        if not file_ids:
            return []
        chunk_counts = {
            str(file_id): int(count)
            for file_id, count in self.db.execute(
                select(FileTextChunk.file_id, func.count(FileTextChunk.id))
                .where(
                    FileTextChunk.workspace_id == self.workspace_id,
                    FileTextChunk.file_id.in_(file_ids),
                )
                .group_by(FileTextChunk.file_id)
            ).all()
        }
        try:
            fts_counts = {
                str(file_id): int(count)
                for file_id, count in self.db.execute(
                    text(
                        """
                        SELECT file_id, COUNT(*)
                          FROM document_chunks_fts
                         WHERE workspace_id = :workspace_id
                           AND file_id IN :file_ids
                         GROUP BY file_id
                        """
                    ).bindparams(bindparam("file_ids", expanding=True)),
                    {"workspace_id": self.workspace_id, "file_ids": file_ids},
                ).all()
            }
        except Exception:
            # FTS availability is still handled by the ordinary SQL fallback
            # below. Do not turn a recoverable derived-index problem into an
            # unavailable document.
            return []

        repaired: list[str] = []
        for file_id in file_ids:
            if chunk_counts.get(file_id, 0) == fts_counts.get(file_id, 0):
                continue
            chunks = list(
                self.db.scalars(
                    self.chunks.query()
                    .where(FileTextChunk.file_id == file_id)
                    .order_by(FileTextChunk.ordinal)
                ).all()
            )
            try:
                self.db.execute(
                    text(
                        "DELETE FROM document_chunks_fts "
                        "WHERE workspace_id = :workspace_id AND file_id = :file_id"
                    ),
                    {"workspace_id": self.workspace_id, "file_id": file_id},
                )
                for chunk in chunks:
                    self.db.execute(
                        text(
                            "INSERT INTO document_chunks_fts "
                            "(chunk_id, workspace_id, file_id, content) "
                            "VALUES (:chunk_id, :workspace_id, :file_id, :content)"
                        ),
                        {
                            "chunk_id": chunk.id,
                            "workspace_id": self.workspace_id,
                            "file_id": file_id,
                            "content": chunk.content,
                        },
                    )
            except Exception:
                # Preserve the authoritative chunks; a later preview can use
                # its SQL fallback even if this SQLite FTS repair is rejected.
                continue
            repaired.append(file_id)
        return repaired

    def _all_rows(
        self,
        file_ids: list[str],
        *,
        scoped_chunk_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return every authorized chunk in document order for full coverage."""

        if scoped_chunk_ids == []:
            return []
        statement = (
            select(FileTextChunk, FileRecord.original_name)
            .join(FileRecord, FileRecord.id == FileTextChunk.file_id)
            .where(
                FileTextChunk.workspace_id == self.workspace_id,
                FileTextChunk.file_id.in_(file_ids),
                FileTextChunk.lifecycle_status == "active",
            )
            .order_by(FileTextChunk.file_id, FileTextChunk.ordinal)
        )
        if scoped_chunk_ids is not None:
            statement = statement.where(FileTextChunk.id.in_(scoped_chunk_ids))
        return [
            {
                "chunk_id": chunk.id,
                "file_id": chunk.file_id,
                "document_revision_id": chunk.document_revision_id,
                "filename": filename,
                "locator": chunk.locator,
                "locator_json": chunk.locator_json,
                "section_path": chunk.section_path,
                "content": chunk.content,
                "content_hash": chunk.content_hash,
                "raw_score": float(index),
            }
            for index, (chunk, filename) in enumerate(self.db.execute(statement).all(), start=1)
        ]

    def _head_rows(
        self,
        file_ids: list[str],
        limit: int,
        *,
        scoped_chunk_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return real, authorized initial chunks for a non-lexical request."""

        if scoped_chunk_ids == []:
            return []
        statement = (
            select(FileTextChunk, FileRecord.original_name)
            .join(FileRecord, FileRecord.id == FileTextChunk.file_id)
            .where(
                FileTextChunk.workspace_id == self.workspace_id,
                FileTextChunk.file_id.in_(file_ids),
                FileTextChunk.lifecycle_status == "active",
            )
            .order_by(FileTextChunk.file_id, FileTextChunk.ordinal)
            .limit(limit)
        )
        if scoped_chunk_ids is not None:
            statement = statement.where(FileTextChunk.id.in_(scoped_chunk_ids))
        rows = self.db.execute(statement).all()
        return [
            {
                "chunk_id": chunk.id,
                "file_id": chunk.file_id,
                "document_revision_id": chunk.document_revision_id,
                "filename": filename,
                "locator": chunk.locator,
                "locator_json": chunk.locator_json,
                "section_path": chunk.section_path,
                "content": chunk.content,
                "content_hash": chunk.content_hash,
                "raw_score": float(index),
            }
            for index, (chunk, filename) in enumerate(rows, start=1)
        ]

    def _fts_rows(
        self,
        query: str,
        file_ids: list[str],
        limit: int,
        *,
        scoped_chunk_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if scoped_chunk_ids == []:
            return []
        match_query, fallback_terms = _fts_query(query)
        scope_clause = " AND c.id IN :chunk_ids" if scoped_chunk_ids is not None else ""
        statement = text(
            f"""
            SELECT c.id AS chunk_id, c.file_id, c.document_revision_id,
                   f.original_name AS filename, c.locator, c.locator_json,
                   c.section_path, c.content, c.content_hash,
                   bm25(document_chunks_fts) AS raw_score
              FROM document_chunks_fts
              JOIN file_text_chunks AS c ON c.id = document_chunks_fts.chunk_id
              JOIN files AS f ON f.id = c.file_id
             WHERE document_chunks_fts MATCH :query
               AND c.workspace_id = :workspace_id
               AND c.file_id IN :file_ids
               AND c.lifecycle_status = 'active'
               {scope_clause}
             ORDER BY raw_score ASC, c.ordinal ASC
             LIMIT :limit
            """
        ).bindparams(bindparam("file_ids", expanding=True))
        if scoped_chunk_ids is not None:
            statement = statement.bindparams(bindparam("chunk_ids", expanding=True))
        parameters: dict[str, Any] = {
            "query": match_query,
            "workspace_id": self.workspace_id,
            "file_ids": file_ids,
            "limit": limit,
        }
        if scoped_chunk_ids is not None:
            parameters["chunk_ids"] = scoped_chunk_ids
        try:
            rows = self.db.execute(statement, parameters).mappings().all()
        except Exception:
            rows = []
        if rows:
            return [dict(row) for row in rows]
        like_terms = [term for term in fallback_terms if len(term) >= 2][:12]
        filters = [FileTextChunk.content.like(f"%{term}%") for term in like_terms]
        if not filters:
            return []
        fallback_statement = (
            select(FileTextChunk, FileRecord.original_name)
            .join(FileRecord, FileRecord.id == FileTextChunk.file_id)
            .where(
                FileTextChunk.workspace_id == self.workspace_id,
                FileTextChunk.file_id.in_(file_ids),
                FileTextChunk.lifecycle_status == "active",
                or_(*filters),
            )
            .order_by(FileTextChunk.ordinal)
            .limit(limit)
        )
        if scoped_chunk_ids is not None:
            fallback_statement = fallback_statement.where(
                FileTextChunk.id.in_(scoped_chunk_ids)
            )
        fallback = self.db.execute(
            fallback_statement
        ).all()
        return [
            {
                "chunk_id": chunk.id,
                "file_id": chunk.file_id,
                "document_revision_id": chunk.document_revision_id,
                "filename": filename,
                "locator": chunk.locator,
                "locator_json": chunk.locator_json,
                "section_path": chunk.section_path,
                "content": chunk.content,
                "content_hash": chunk.content_hash,
                "raw_score": float(index),
            }
            for index, (chunk, filename) in enumerate(fallback, start=1)
        ]

    def _scoped_chunk_ids(
        self,
        payload: DocumentQueryPreviewRequest,
        file_ids: list[str],
    ) -> list[str] | None:
        if payload.scope in {"file", "files"}:
            return None
        if payload.scope == "selection":
            # The selection is a hint, not a retrieval hard-boundary.  Return
            # None so FTS retrieves the whole file; the verified chunk (when
            # present) is force-inserted into rows by the caller.
            return None
        chunks = list(
            self.db.scalars(
                self.chunks.query()
                .where(FileTextChunk.file_id.in_(file_ids))
                .order_by(FileTextChunk.file_id, FileTextChunk.ordinal)
            ).all()
        )
        if payload.scope == "page":
            page = payload.locator.get("page")
            return [
                chunk.id
                for chunk in chunks
                if (chunk.locator_json or {}).get("page") == page
            ]
        requested_path = payload.locator.get("section_path")
        if not isinstance(requested_path, list):
            anchor_id = str(payload.locator.get("chunk_id") or "")
            anchor = next((chunk for chunk in chunks if chunk.id == anchor_id), None)
            requested_path = list(anchor.section_path or []) if anchor else []
        normalized_path = [str(item) for item in requested_path]
        if not normalized_path:
            return []
        return [
            chunk.id
            for chunk in chunks
            if list(chunk.section_path or [])[: len(normalized_path)] == normalized_path
        ]

    def _verify_selection(
        self, payload: DocumentQueryPreviewRequest, file_ids: list[str]
    ) -> FileTextChunk | None:
        """Best-effort selection verification.

        Returns the chunk anchor when the selection still matches the current
        index, and ``None`` when it does not (stale revision, cross-chunk
        selection, hash mismatch).  Callers must treat ``None`` as a signal to
        degrade to whole-file context with the selection attached as an
        unverified hint, rather than rejecting the request.
        """
        chunk_id = str(payload.locator.get("chunk_id") or "")
        if not chunk_id:
            return None
        chunk = self.db.scalar(
            self.chunks.query().where(
                FileTextChunk.id == chunk_id,
                FileTextChunk.file_id.in_(file_ids),
            )
        )
        requested_revision_id = str(
            payload.locator.get("document_revision_id") or ""
        )
        normalized_selection = _normalize_selection_text(payload.selected_text)
        if (
            chunk is None
            or (
                requested_revision_id
                and chunk.document_revision_id != requested_revision_id
            )
            or not normalized_selection
            or normalized_selection
            not in _normalize_selection_text(chunk.content)
        ):
            return None
        if payload.selected_text_hash and _hash(payload.selected_text) != payload.selected_text_hash:
            return None
        return chunk

    def _sync_fts(self, file_id: str) -> None:
        self.db.execute(
            text(
                "DELETE FROM document_chunks_fts "
                "WHERE workspace_id = :workspace_id AND file_id = :file_id"
            ),
            {"workspace_id": self.workspace_id, "file_id": file_id},
        )
        for chunk in self.db.scalars(
            self.chunks.query()
            .where(FileTextChunk.file_id == file_id)
            .order_by(FileTextChunk.ordinal)
        ).all():
            self.db.execute(
                text(
                    "INSERT INTO document_chunks_fts"
                    "(chunk_id, workspace_id, file_id, content) "
                    "VALUES (:chunk_id, :workspace_id, :file_id, :content)"
                ),
                {
                    "chunk_id": chunk.id,
                    "workspace_id": self.workspace_id,
                    "file_id": file_id,
                    "content": "\n".join([*(chunk.section_path or []), chunk.content]),
                },
            )

    def _event(self, job: DocumentJob, event_type: str, payload: dict[str, Any]) -> None:
        sequence = int(
            self.db.scalar(
                select(func.max(DocumentJobEvent.sequence)).where(
                    DocumentJobEvent.workspace_id == self.workspace_id,
                    DocumentJobEvent.job_id == job.id,
                )
            )
            or 0
        ) + 1
        self.db.add(
            DocumentJobEvent(
                workspace_id=self.workspace_id,
                job_id=job.id,
                sequence=sequence,
                event_type=event_type,
                payload=payload,
            )
        )

    def _execution_is_current(self, job: DocumentJob, execution_token: str) -> bool:
        self.db.refresh(job)
        return (
            job.status in {"queued", "running"}
            and (
                not execution_token
                or self.execution_token(job) == execution_token
            )
        )

    def _claim_execution(
        self,
        job: DocumentJob,
        execution_token: str,
    ) -> DocumentJob | None:
        statement = update(DocumentJob).where(
            DocumentJob.workspace_id == self.workspace_id,
            DocumentJob.id == job.id,
            DocumentJob.status == "queued",
        )
        if execution_token:
            statement = statement.where(DocumentJob.execution_token == execution_token)
        result = self.db.execute(
            statement.values(status="running", started_at=utc_now())
        )
        self.db.commit()
        if result.rowcount != 1:
            return None
        return self.get_job(job.id)

    def _set_stage(
        self,
        job: DocumentJob,
        stage: str,
        progress: int,
        execution_token: str,
    ) -> None:
        if not self._execution_is_current(job, execution_token):
            raise _DocumentJobExecutionStopped
        job.status = "running"
        job.stage = stage
        job.progress = progress
        if job.started_at is None:
            job.started_at = utc_now()
        self._event(job, "job.stage", {"stage": stage, "progress": progress})
        self.db.commit()

    def _complete(
        self,
        job: DocumentJob,
        revision: DocumentRevision,
        *,
        reused: bool,
        execution_token: str,
    ) -> None:
        if not self._execution_is_current(job, execution_token):
            raise _DocumentJobExecutionStopped
        job.status = "completed"
        job.stage = "ready"
        job.progress = 100
        job.completed_at = utc_now()
        self._event(
            job,
            "job.completed",
            {
                "stage": "ready",
                "progress": 100,
                "revision_id": revision.id,
                "reused": reused,
            },
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="document.job_completed",
            resource_type="document_job",
            resource_id=job.id,
            details={
                "file_id": job.file_id,
                "revision_id": revision.id,
                "reused": reused,
            },
        )
        self.db.commit()
        self.db.refresh(job)

    def _fail(
        self,
        job: DocumentJob,
        error_code: str,
        error_message: str,
        file_status: str,
        *,
        execution_token: str,
    ) -> None:
        if not self._execution_is_current(job, execution_token):
            self.db.rollback()
            return
        job.status = "failed"
        job.error_code = error_code
        job.error_message = error_message[:2_000]
        job.completed_at = utc_now()
        record = self.files.get(job.file_id)
        if record is not None:
            record.parse_status = file_status
            record.error_message = error_message[:2_000]
        if job.document_revision_id:
            revision = self.db.scalar(
                select(DocumentRevision).where(
                    DocumentRevision.workspace_id == self.workspace_id,
                    DocumentRevision.id == job.document_revision_id,
                )
            )
            if revision is not None:
                revision.status = "failed"
                revision.error_code = error_code
                revision.completed_at = utc_now()
        self._event(
            job,
            "job.failed",
            {"stage": job.stage, "error_code": error_code},
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="document.job_failed",
            resource_type="document_job",
            resource_id=job.id,
            outcome="failure",
            details={"file_id": job.file_id, "error_code": error_code},
        )
        self.db.commit()
        self.db.refresh(job)


def run_document_job(job_id: str, expected_execution_token: str | None = None) -> None:
    """Background entry point that owns its own transaction/session."""

    settings = get_settings()
    with SessionLocal() as db:
        job = db.scalar(select(DocumentJob).where(DocumentJob.id == job_id))
        if job is None:
            return
        DocumentLearningService(
            db,
            job.workspace_id,
            job.created_by,
            settings,
        ).execute_job(
            job.id,
            expected_execution_token=expected_execution_token,
        )


def mark_interrupted_document_jobs() -> int:
    """Make process interruption visible instead of silently losing work."""

    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(DocumentJob).where(DocumentJob.status == "running")
            ).all()
        )
        now = utc_now()
        for job in jobs:
            job.status = "interrupted"
            job.error_code = "process_interrupted"
            job.error_message = "The process stopped while this document job was running"
            job.completed_at = now
        if jobs:
            db.commit()
        return len(jobs)
