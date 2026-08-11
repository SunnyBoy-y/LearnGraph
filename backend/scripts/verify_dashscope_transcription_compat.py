"""验证 DashScope 转写兼容修复：

1. 工厂路由：只有 DashScope 系 Provider（qwen 等，无 transcription 角色）时，
   stored / realtime / stored_async 三个用途都能解析出可用 Provider 与模型。
2. 传输：DashScope 网关直达 /chat/completions 的 input_audio 通道，
   不再空打 404 的 /audio/transcriptions；非 DashScope 网关保持 multipart 回退。
3. 异步录音文件识别：提交 /api/v1/services/asr/transcription → 轮询 tasks → 解析。

运行：backend/.venv/Scripts/python.exe backend/scripts/verify_dashscope_transcription_compat.py
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.domain.models import ProviderConfig, ProviderSecret, WorkspaceSetting
from app.providers.factory import transcription_provider_for_workspace
from app.providers.remote.transcription import (
    DashScopeAsyncTranscriptionProvider,
    OpenAICompatibleTranscriptionProvider,
)

WORKSPACE_ID = "workspace-test"
DASHSCOPE_PROVIDER_ID = "provider-dashscope-qwen"
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@contextmanager
def database_with_qwen_only(*, enabled: bool = True):
    """仅一条 DashScope 系 qwen Provider（无 transcription 角色）。"""
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
            id=DASHSCOPE_PROVIDER_ID,
            workspace_id=WORKSPACE_ID,
            display_name="Qwen (DashScope)",
            provider_type="qwen",
            base_url=DASHSCOPE_BASE_URL,
            enabled=enabled,
            remote_capability=True,
            capabilities={"default_model": "qwen3.8-max"},
            status="enabled_unverified",
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
        provider_id=DASHSCOPE_PROVIDER_ID if model_id is not None else None,
        model_id=model_id,
        purpose=purpose,
    )


def verify_factory_dashscope_fallback() -> None:
    with database_with_qwen_only() as db:
        stored = resolve(db, "stored")
        realtime = resolve(db, "realtime")
        async_provider = resolve(db, "stored_async")
        assert stored is not None, "stored 应兜底解析出 Provider"
        assert isinstance(stored, OpenAICompatibleTranscriptionProvider)
        assert stored.model_id == "qwen3-asr-flash"
        assert stored.base_url == DASHSCOPE_BASE_URL
        assert realtime is not None and realtime.model_id == "qwen3-asr-flash-realtime"
        assert async_provider is not None
        assert isinstance(async_provider, DashScopeAsyncTranscriptionProvider)
        assert async_provider.model_id == "paraformer-v2"
        assert async_provider.supports_async is True

        # 显式选择 qwen 行（前端下拉选中自己配置的 DashScope Provider）也要命中。
        explicit_realtime = resolve(db, "realtime", "qwen3-asr-flash-realtime")
        assert explicit_realtime is not None
        assert explicit_realtime.model_id == "qwen3-asr-flash-realtime"

        # 传输不匹配仍要拒绝：stored 用途显式给实时模型。
        assert resolve(db, "stored", "qwen3-asr-flash-realtime") is None


def verify_factory_disabled_provider_is_ignored() -> None:
    with database_with_qwen_only(enabled=False) as db:
        assert resolve(db, "stored") is None
        assert resolve(db, "realtime") is None
        assert resolve(db, "stored_async") is None


def verify_dashscope_goes_straight_to_input_audio() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        body = json.loads(request.content.decode("utf-8"))
        assert "input_audio" in json.dumps(body), "DashScope 必须走 input_audio 形状"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "你好，世界。"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    provider = OpenAICompatibleTranscriptionProvider(
        provider_id="p",
        model_id="qwen3-asr-flash",
        base_url=DASHSCOPE_BASE_URL,
        api_key="secret",
        transport=httpx.MockTransport(handler),
    )
    result = provider.transcribe(
        filename="a.wav", mime_type="audio/wav", content=b"\x00\x01"
    )
    assert seen == ["POST /compatible-mode/v1/chat/completions"], seen
    assert result.text == "你好，世界。"
    assert result.usage.get("input_tokens") == 10


def verify_non_dashscope_keeps_multipart_fallback() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(404, json={"error": "not found"})
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "fallback ok"}}]}
        )

    provider = OpenAICompatibleTranscriptionProvider(
        provider_id="p",
        model_id="whisper-1",
        base_url="https://api.openai.com/v1",
        api_key="secret",
        transport=httpx.MockTransport(handler),
    )
    result = provider.transcribe(
        filename="a.mp3", mime_type="audio/mpeg", content=b"\xff\xfb"
    )
    assert seen == [
        "POST /v1/audio/transcriptions",
        "POST /v1/chat/completions",
    ], seen
    assert result.text == "fallback ok"


def verify_async_submit_and_poll() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/asr/transcription"):
            body = json.loads(request.content.decode("utf-8"))
            assert request.headers.get("x-dashscope-async") == "enable"
            assert body["model"] == "paraformer-v2"
            assert body["input"]["source_url"] == "https://example.com/a.mp3"
            return httpx.Response(
                200, json={"output": {"task_id": "task-123", "task_status": "PENDING"}}
            )
        if request.url.path.endswith("/tasks/task-123"):
            return httpx.Response(
                200,
                json={
                    "output": {
                        "task_status": "SUCCEEDED",
                        "result": {
                            "duration_in_milliseconds": 12_345,
                            "transcripts": [
                                {"text": "第一句。", "begin_time": 0, "end_time": 6000},
                                {"text": "第二句。", "begin_time": 6000, "end_time": 12000},
                            ],
                        },
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    provider = DashScopeAsyncTranscriptionProvider(
        provider_id="p",
        model_id="paraformer-v2",
        base_url=DASHSCOPE_BASE_URL,
        api_key="secret",
        poll_interval_seconds=0.01,
        transport=httpx.MockTransport(handler),
    )
    task_id = provider.submit(source_url="https://example.com/a.mp3")
    assert task_id == "task-123"
    result = provider.wait_for_result(task_id, max_wait_seconds=5)
    assert result.text == "第一句。\n第二句。"
    assert result.duration_seconds == 12.345
    assert result.request_id == "task-123"
    assert calls == [
        "POST /api/v1/services/asr/transcription",
        "GET /api/v1/tasks/task-123",
    ], calls


def verify_async_rejects_private_url_and_non_dashscope_origin() -> None:
    provider = DashScopeAsyncTranscriptionProvider(
        provider_id="p",
        model_id="sensevoice-v1",
        base_url=DASHSCOPE_BASE_URL,
        api_key="secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    try:
        provider.submit(source_url="file:///tmp/a.mp3")
        raise AssertionError("私网 URL 应被拒绝")
    except Exception as exc:
        assert "public http(s)" in str(exc)
    try:
        DashScopeAsyncTranscriptionProvider(
            provider_id="p",
            model_id="paraformer-v2",
            base_url="https://api.openai.com/v1",
            api_key="secret",
        )
        raise AssertionError("非 DashScope origin 应被拒绝")
    except ValueError as exc:
        assert "DashScope" in str(exc)


def main() -> None:
    verify_factory_dashscope_fallback()
    verify_factory_disabled_provider_is_ignored()
    verify_dashscope_goes_straight_to_input_audio()
    verify_non_dashscope_keeps_multipart_fallback()
    verify_async_submit_and_poll()
    verify_async_rejects_private_url_and_non_dashscope_origin()
    print("Verified DashScope transcription compatibility (routing/transport/async).")


if __name__ == "__main__":
    main()
