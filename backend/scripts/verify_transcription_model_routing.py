from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.domain.models import ProviderConfig, ProviderSecret, WorkspaceSetting
from app.providers.factory import transcription_provider_for_workspace


WORKSPACE_ID = "workspace-test"
PROVIDER_ID = "provider-test"


@contextmanager
def database_with(capabilities: dict, *, functional_model: str | None = None):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[
            ProviderConfig.__table__,
            ProviderSecret.__table__,
            WorkspaceSetting.__table__,
        ],
    )
    db = Session(engine, autoflush=False, expire_on_commit=False)
    db.add(
        ProviderConfig(
            id=PROVIDER_ID,
            workspace_id=WORKSPACE_ID,
            display_name="ASR",
            provider_type="openai_compatible_transcription",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            enabled=True,
            remote_capability=True,
            capabilities=capabilities,
            status="enabled_unverified",
        )
    )
    if functional_model:
        db.add(
            WorkspaceSetting(
                workspace_id=WORKSPACE_ID,
                key="models.functional_defaults",
                value={
                    "transcription": {
                        "provider_id": PROVIDER_ID,
                        "model_id": functional_model,
                    }
                },
            )
        )
    db.commit()
    try:
        with patch("app.providers.factory._secret_for_provider", return_value="secret"):
            yield db
    finally:
        db.close()
        engine.dispose()


def resolve(db: Session, purpose: str, model_id: str | None = None):
    return transcription_provider_for_workspace(
        db,
        WORKSPACE_ID,
        SimpleNamespace(),
        provider_id=PROVIDER_ID if model_id is not None else None,
        model_id=model_id,
        purpose=purpose,
    )


def verify_dual_models() -> None:
    with database_with(
        {
            "default_transcription_model_id": "qwen3-asr-flash",
            "default_realtime_transcription_model_id": "paraformer-realtime-v2",
        }
    ) as db:
        stored = resolve(db, "stored")
        realtime = resolve(db, "realtime")
        assert stored is not None and stored.model_id == "qwen3-asr-flash"
        assert realtime is not None and realtime.model_id == "paraformer-realtime-v2"


def verify_legacy_fallbacks() -> None:
    with database_with(
        {"default_transcription_model_id": "whisper-1"}
    ) as db:
        assert resolve(db, "stored") is not None
        assert resolve(db, "realtime") is None
    with database_with(
        {"default_transcription_model_id": "paraformer-realtime-v2"}
    ) as db:
        assert resolve(db, "stored") is None
        realtime = resolve(db, "realtime")
        assert realtime is not None
        assert realtime.model_id == "paraformer-realtime-v2"


def verify_transport_mismatch() -> None:
    with database_with(
        {
            "default_transcription_model_id": "qwen3-asr-flash",
            "default_realtime_transcription_model_id": "paraformer-realtime-v2",
        }
    ) as db:
        assert resolve(db, "stored", "paraformer-realtime-v2") is None
        assert resolve(db, "realtime", "qwen3-asr-flash") is None


def verify_functional_default_is_stored_only() -> None:
    with database_with(
        {
            "default_transcription_model_id": "qwen3-asr-flash",
            "default_realtime_transcription_model_id": "paraformer-realtime-v2",
        },
        functional_model="qwen3-asr-flash",
    ) as db:
        stored = transcription_provider_for_workspace(
            db, WORKSPACE_ID, SimpleNamespace(), purpose="stored"
        )
        realtime = transcription_provider_for_workspace(
            db, WORKSPACE_ID, SimpleNamespace(), purpose="realtime"
        )
        assert stored is not None and stored.model_id == "qwen3-asr-flash"
        assert realtime is not None and realtime.model_id == "paraformer-realtime-v2"


def main() -> None:
    verify_dual_models()
    verify_legacy_fallbacks()
    verify_transport_mismatch()
    verify_functional_default_is_stored_only()
    print("Verified purpose-aware dual transcription model resolution.")


if __name__ == "__main__":
    main()
