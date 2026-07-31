from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.errors import AppError
from app.domain.models import AudioTranscription, AuditEvent, FileRecord, UsageEvent
from app.domain.schemas.files import AudioTranscriptionCreate
from app.providers.ports.transcription import TranscriptionResult
from app.providers.remote.transcription import TranscriptionProviderError
from app.services.files import FileService


WORKSPACE_ID = "workspace-test"
ACTOR_ID = "user-test"
PROVIDER_ID = "provider-test"
FILE_ID = "file-test"


class FakeStorage:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.read_calls = 0

    def read_bytes(self, _object_key: str, *, limit_bytes: int) -> bytes:
        assert limit_bytes > 0
        self.read_calls += 1
        if self.error is not None:
            raise self.error
        return b"RIFF-test-audio"


class FakeProvider:
    available = True
    remote_capability = True

    def __init__(self, model_id: str = "qwen3-asr-flash", error: Exception | None = None) -> None:
        self.provider_id = PROVIDER_ID
        self.model_id = model_id
        self.error = error
        self.calls = 0

    def transcribe(self, **_kwargs) -> TranscriptionResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return TranscriptionResult(
            text="verified transcript",
            language="zh",
            duration_seconds=1.5,
            request_id="request-test",
            usage={"input_tokens": 2, "output_tokens": 3},
        )


class FakeBilling:
    instances: list["FakeBilling"] = []

    def __init__(self, db: Session, *_args) -> None:
        self.db = db
        self.preflight_calls = 0
        self.record_calls = 0
        self.__class__.instances.append(self)

    def preflight_model_call(self, **_kwargs) -> object:
        self.preflight_calls += 1
        return object()

    def record_usage(self, _quote: object, **_kwargs) -> UsageEvent:
        self.record_calls += 1
        event = UsageEvent(
            workspace_id=WORKSPACE_ID,
            provider_id=PROVIDER_ID,
            model_id="qwen3-asr-flash",
            feature="audio_transcription",
        )
        self.db.add(event)
        return event


def build_service(
    db: Session,
    *,
    provider: FakeProvider,
    storage: FakeStorage | None = None,
) -> FileService:
    settings = SimpleNamespace(max_upload_bytes=20 * 1024 * 1024)
    with patch("app.services.files.object_storage_provider", return_value=storage or FakeStorage()):
        service = FileService(db, WORKSPACE_ID, ACTOR_ID, settings)
    service._test_provider = provider  # type: ignore[attr-defined]
    return service


def add_audio_file(db: Session) -> None:
    db.add(
        FileRecord(
            id=FILE_ID,
            workspace_id=WORKSPACE_ID,
            original_name="lecture.wav",
            object_key="objects/lecture.wav",
            mime_type="audio/wav",
            size_bytes=16,
            sha256="a" * 64,
            storage_status="stored",
            parse_capability="attachment_only",
            parse_status="not_requested",
        )
    )
    db.commit()


def run_transcription(
    service: FileService,
    provider: FakeProvider,
    *,
    key: str,
) -> AudioTranscription:
    with (
        patch("app.services.files.transcription_provider_for_workspace", return_value=provider),
        patch("app.services.files.BillingService", FakeBilling),
    ):
        return service.transcribe(FILE_ID, AudioTranscriptionCreate(), key)


def assert_failure_row(db: Session, *, code: str) -> AudioTranscription:
    row = db.scalar(select(AudioTranscription))
    assert row is not None
    assert row.status == "failed"
    assert row.error_code == code
    assert row.completed_at is not None
    failure = db.scalar(
        select(AuditEvent).where(AuditEvent.action == "file.transcription.failed")
    )
    assert failure is not None
    assert failure.resource_id == row.id
    return row


def verify_success() -> None:
    with isolated_database() as db:
        add_audio_file(db)
        provider = FakeProvider()
        service = build_service(db, provider=provider)
        result = run_transcription(service, provider, key="success")
        assert result.id
        assert result.status == "completed"
        assert result.provider_trace["usage_event_id"]
        db.expire_all()
        persisted = db.scalar(select(AudioTranscription))
        assert persisted is not None
        assert persisted.provider_trace["usage_event_id"] == result.provider_trace["usage_event_id"]
        audits = list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.action.like("file.transcription.%"))
                .order_by(AuditEvent.created_at)
            )
        )
        assert [event.action for event in audits] == [
            "file.transcription.started",
            "file.transcription.completed",
        ]
        assert all(event.resource_id == result.id for event in audits)


def verify_provider_failure() -> None:
    with isolated_database() as db:
        add_audio_file(db)
        provider = FakeProvider(error=TranscriptionProviderError("provider unavailable"))
        service = build_service(db, provider=provider)
        try:
            run_transcription(service, provider, key="provider-failure")
        except AppError as exc:
            assert exc.status_code == 502
            assert exc.code == "transcription_provider_failed"
            row = assert_failure_row(db, code=exc.code)
            assert exc.details["transcription_id"] == row.id
        else:
            raise AssertionError("Expected provider failure")


def verify_unexpected_failure() -> None:
    logging.disable(logging.CRITICAL)
    try:
        with isolated_database() as db:
            add_audio_file(db)
            provider = FakeProvider()
            service = build_service(
                db,
                provider=provider,
                storage=FakeStorage(RuntimeError("missing object")),
            )
            try:
                run_transcription(service, provider, key="storage-failure")
            except AppError as exc:
                assert exc.status_code == 500
                assert exc.code == "transcription_failed"
                row = assert_failure_row(db, code=exc.code)
                assert exc.details["transcription_id"] == row.id
            else:
                raise AssertionError("Expected unexpected failure")
    finally:
        logging.disable(logging.NOTSET)


def verify_realtime_rejection() -> None:
    with isolated_database() as db:
        add_audio_file(db)
        provider = FakeProvider(model_id="paraformer-realtime-v2")
        storage = FakeStorage()
        service = build_service(db, provider=provider, storage=storage)
        billing = Mock()
        with (
            patch("app.services.files.transcription_provider_for_workspace", return_value=provider),
            patch("app.services.files.BillingService", billing),
        ):
            try:
                service.transcribe(FILE_ID, AudioTranscriptionCreate(), "realtime")
            except AppError as exc:
                assert exc.status_code == 409
                assert exc.code == "stored_transcription_model_required"
            else:
                raise AssertionError("Expected realtime model rejection")
        assert storage.read_calls == 0
        assert provider.calls == 0
        billing.assert_not_called()
        assert db.scalar(select(AudioTranscription)) is None
        assert db.scalar(
            select(AuditEvent).where(AuditEvent.action.like("file.transcription.%"))
        ) is None


class isolated_database:
    def __enter__(self) -> Session:
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(
            self.engine,
            tables=[
                FileRecord.__table__,
                AudioTranscription.__table__,
                AuditEvent.__table__,
                UsageEvent.__table__,
            ],
        )
        self.db = Session(self.engine, autoflush=False, expire_on_commit=False)
        FakeBilling.instances.clear()
        return self.db

    def __exit__(self, *_args) -> None:
        self.db.close()
        self.engine.dispose()


def main() -> None:
    verify_success()
    verify_provider_failure()
    verify_unexpected_failure()
    verify_realtime_rejection()
    print("Verified stored audio transcription lifecycle and transport guards.")


if __name__ == "__main__":
    main()
