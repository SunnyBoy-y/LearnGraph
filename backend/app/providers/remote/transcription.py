from __future__ import annotations

import base64
import json
import time
from urllib.parse import urlsplit

import httpx

from app.providers.ports.transcription import TranscriptionResult

# DashScope 录音文件识别（异步提交 + 轮询）只对这批模型开放。qwen3-asr-flash
# 系列是同步通道（compatible-mode input_audio），不进异步任务。
ASYNC_TRANSCRIPTION_MODELS: frozenset[str] = frozenset(
    {"paraformer-v2", "sensevoice-v1"}
)


def is_async_transcription_model(model_id: str | None) -> bool:
    return (model_id or "").strip().casefold() in ASYNC_TRANSCRIPTION_MODELS


class TranscriptionProviderError(RuntimeError):
    pass


def is_dashscope_origin(base_url: str | None) -> bool:
    """Whether ``base_url`` addresses a DashScope (aliyuncs.com) origin.

    Self-contained so ``remote/transcription.py`` does not import the catalog:
    the public ``dashscope*.aliyuncs.com`` gateways and the private
    ``*.maas.aliyuncs.com`` tenants are both DashScope-flavoured (realtime WS at
    ``/api-ws/v1/inference``, native async at ``/api/v1/services/asr/*``).
    """

    if not base_url:
        return False
    try:
        parsed = urlsplit(base_url.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https" or not host:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if host.endswith(".maas.aliyuncs.com"):
        return True
    label, _, domain = host.partition(".")
    return domain == "aliyuncs.com" and (
        label == "dashscope" or label.startswith("dashscope-")
    )


class OpenAICompatibleTranscriptionProvider:
    available = True
    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 180.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.model_id = model_id
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def transcribe(
        self,
        *,
        filename: str,
        mime_type: str,
        content: bytes,
        language: str | None = None,
        allow_empty: bool = False,
    ) -> TranscriptionResult:
        # DashScope 网关不暴露 OpenAI 兼容的 /audio/transcriptions（实测 404），
        # ASR 模型走 /chat/completions 的 input_audio 形状。直接走该通道，
        # 避免每次转写都空打一次注定失败的多部分上传请求。
        if is_dashscope_origin(self.base_url):
            return self._transcribe_via_chat_completions(
                mime_type=mime_type,
                content=content,
                language=language,
                allow_empty=allow_empty,
            )
        data = {"model": self.model_id, "response_format": "verbose_json"}
        if language:
            data["language"] = language
        try:
            with httpx.Client(
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post(
                    f"{self.base_url}/audio/transcriptions",
                    data=data,
                    files={"file": (filename, content, mime_type)},
                )
        except httpx.TimeoutException as exc:
            raise TranscriptionProviderError("The transcription request timed out") from exc
        except httpx.HTTPError as exc:
            # Gateways without an /audio/transcriptions route may 404 or drop
            # the multipart upload outright; try the input_audio chat shape
            # before reporting the transport failure.
            try:
                return self._transcribe_via_chat_completions(
                    mime_type=mime_type,
                    content=content,
                    language=language,
                    allow_empty=allow_empty,
                )
            except TranscriptionProviderError:
                raise TranscriptionProviderError(
                    "The transcription service could not be reached"
                ) from exc
        if response.status_code in {404, 405}:
            # Several OpenAI-compatible gateways (notably DashScope private MaaS
            # deployments) never expose /audio/transcriptions and instead accept
            # audio as an input_audio chat message on the same origin.
            return self._transcribe_via_chat_completions(
                mime_type=mime_type,
                content=content,
                language=language,
                allow_empty=allow_empty,
            )
        if not response.is_success:
            raise TranscriptionProviderError(
                f"The transcription provider returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise TranscriptionProviderError(
                "The transcription provider returned non-JSON data"
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            raise TranscriptionProviderError(
                "The transcription provider response has no text"
            )
        text = payload["text"].strip()
        if not text and not allow_empty:
            raise TranscriptionProviderError("The transcription provider returned empty text")
        duration = payload.get("duration")
        return TranscriptionResult(
            text=text,
            language=payload.get("language") if isinstance(payload.get("language"), str) else language,
            duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
            request_id=response.headers.get("x-request-id"),
            usage=(
                {key: int(value) for key, value in payload["usage"].items() if isinstance(value, int)}
                if isinstance(payload.get("usage"), dict)
                else {}
            ),
        )

    def _transcribe_via_chat_completions(
        self,
        *,
        mime_type: str,
        content: bytes,
        language: str | None,
        allow_empty: bool,
    ) -> TranscriptionResult:
        """Transcribe through the OpenAI-compatible input_audio chat shape.

        Used only as a fallback when the origin has no /audio/transcriptions
        route. The audio rides as a base64 data URL, which is how DashScope's
        compatible mode exposes qwen ASR models.
        """

        media_type = (mime_type or "audio/wav").split(";", 1)[0].strip() or "audio/wav"
        encoded = base64.b64encode(content).decode("ascii")
        payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": ""}]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": f"data:{media_type};base64,{encoded}"},
                        }
                    ],
                },
            ],
        }
        if language:
            payload["asr_options"] = {"language": language}
        try:
            with httpx.Client(
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post(f"{self.base_url}/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise TranscriptionProviderError("The transcription request timed out") from exc
        except httpx.HTTPError as exc:
            raise TranscriptionProviderError(
                "The transcription service could not be reached"
            ) from exc
        if not response.is_success:
            raise TranscriptionProviderError(
                f"The transcription provider returned HTTP {response.status_code}"
            )
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise TranscriptionProviderError(
                "The transcription provider returned non-JSON data"
            ) from exc
        choices = body.get("choices") if isinstance(body, dict) else None
        message = choices[0].get("message") if isinstance(choices, list) and choices else None
        raw_text = message.get("content") if isinstance(message, dict) else None
        if isinstance(raw_text, list):
            raw_text = "".join(
                part.get("text", "")
                for part in raw_text
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        if not isinstance(raw_text, str):
            raise TranscriptionProviderError(
                "The transcription provider response has no text"
            )
        text = raw_text.strip()
        if not text and not allow_empty:
            raise TranscriptionProviderError("The transcription provider returned empty text")
        detected_language = language
        annotations = message.get("annotations") if isinstance(message, dict) else None
        if isinstance(annotations, list):
            for annotation in annotations:
                if isinstance(annotation, dict) and isinstance(
                    annotation.get("language"), str
                ):
                    detected_language = annotation["language"]
                    break
        raw_usage = body.get("usage") if isinstance(body, dict) else None
        usage: dict[str, int] = {}
        duration_seconds: float | None = None
        if isinstance(raw_usage, dict):
            prompt_tokens = raw_usage.get("prompt_tokens")
            completion_tokens = raw_usage.get("completion_tokens")
            if isinstance(prompt_tokens, int):
                usage["input_tokens"] = prompt_tokens
            if isinstance(completion_tokens, int):
                usage["output_tokens"] = completion_tokens
            seconds = raw_usage.get("seconds")
            if isinstance(seconds, (int, float)):
                duration_seconds = float(seconds)
        return TranscriptionResult(
            text=text,
            language=detected_language,
            duration_seconds=duration_seconds,
            request_id=response.headers.get("x-request-id") or (
                body.get("id") if isinstance(body, dict) and isinstance(body.get("id"), str) else None
            ),
            usage=usage,
        )


class DashScopeAsyncTranscriptionProvider:
    """DashScope 录音文件识别：提交音频 URL → 轮询任务 → 取回全文。

    走原生 ``POST /api/v1/services/asr/transcription``（X-DashScope-Async:
    enable）+ ``GET /api/v1/tasks/{task_id}``，支持 paraformer-v2 /
    sensevoice-v1。音频必须通过公网可访问的 URL（OSS/分享链接）提交；
    本地存储的字节不能直接进入异步任务。
    """

    available = True
    remote_capability = True
    supports_async = True

    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        api_key: str,
        poll_interval_seconds: float = 2.0,
        poll_timeout_seconds: float = 900.0,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not is_dashscope_origin(base_url):
            raise ValueError("DashScopeAsyncTranscriptionProvider requires a DashScope origin")
        parsed = urlsplit(base_url.strip())
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.provider_id = provider_id
        self.model_id = model_id
        self.api_key = api_key
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_timeout_seconds = poll_timeout_seconds
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout_seconds,
            transport=self.transport,
        )

    def submit(self, *, source_url: str, language: str | None = None) -> str:
        """Submit the recording URL and return the DashScope task id."""
        if not source_url or not source_url.startswith(("http://", "https://")):
            raise TranscriptionProviderError(
                "Async recording transcription needs a public http(s) audio URL"
            )
        payload: dict[str, object] = {
            "model": self.model_id,
            "input": {"source_url": source_url},
            "parameters": {},
        }
        if language:
            payload["parameters"]["language_hints"] = [language]  # type: ignore[assignment]
        try:
            with self._client() as client:
                response = client.post(
                    f"{self.origin}/api/v1/services/asr/transcription",
                    headers={"X-DashScope-Async": "enable"},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise TranscriptionProviderError("The transcription submit request timed out") from exc
        except httpx.HTTPError as exc:
            raise TranscriptionProviderError("The transcription submit could not be reached") from exc
        if not response.is_success:
            raise TranscriptionProviderError(
                f"The transcription provider returned HTTP {response.status_code}"
            )
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise TranscriptionProviderError("The transcription provider returned non-JSON data") from exc
        task_id = body.get("output", {}).get("task_id") if isinstance(body, dict) else None
        if not isinstance(task_id, str) or not task_id:
            raise TranscriptionProviderError(
                "The transcription provider response has no task_id"
            )
        return task_id

    def poll(self, task_id: str) -> dict:
        """Fetch one task snapshot; never raises for a live task."""
        try:
            with self._client() as client:
                response = client.get(f"{self.origin}/api/v1/tasks/{task_id}")
        except (httpx.TimeoutException, httpx.HTTPError):
            return {"status": "UNKNOWN"}
        if not response.is_success:
            return {"status": "UNKNOWN"}
        try:
            body = response.json()
        except json.JSONDecodeError:
            return {"status": "UNKNOWN"}
        if not isinstance(body, dict):
            return {"status": "UNKNOWN"}
        output = body.get("output") if isinstance(body.get("output"), dict) else {}
        status = str(output.get("task_status") or body.get("status") or "UNKNOWN")
        return {"status": status, "body": body}

    def wait_for_result(
        self, task_id: str, *, max_wait_seconds: float | None = None
    ) -> TranscriptionResult:
        """Poll until SUCCEEDED / FAILED or ``max_wait_seconds`` elapses.

        On success returns the joined transcript; on failure raises; on timeout
        raises :class:`TranscriptionProviderError` with the task id embedded so
        the caller can resume polling later.
        """
        deadline = time.monotonic() + (
            self.poll_timeout_seconds if max_wait_seconds is None else max_wait_seconds
        )
        last_snapshot: dict = {}
        while True:
            snapshot = self.poll(task_id)
            last_snapshot = snapshot
            status = snapshot.get("status")
            if status == "SUCCEEDED":
                return self._parse_result(task_id, snapshot.get("body") or {})
            if status in {"FAILED", "CANCELED", "TERMINATED"}:
                raise TranscriptionProviderError(
                    f"The DashScope transcription task failed (status={status})"
                )
            if time.monotonic() >= deadline:
                raise TranscriptionProviderError(
                    f"The DashScope transcription task is still running (task_id={task_id})"
                )
            time.sleep(self.poll_interval_seconds)

    @staticmethod
    def _parse_result(task_id: str, body: dict) -> TranscriptionResult:
        output = body.get("output") if isinstance(body.get("output"), dict) else {}
        result = output.get("result") if isinstance(output.get("result"), dict) else {}
        transcripts = result.get("transcripts")
        segments: list[str] = []
        if isinstance(transcripts, list):
            for item in transcripts:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    segments.append(item["text"])
        text = "\n".join(part.strip() for part in segments if part.strip())
        duration_seconds: float | None = None
        raw_duration = result.get("duration_in_milliseconds")
        if isinstance(raw_duration, (int, float)):
            duration_seconds = float(raw_duration) / 1000.0
        usage: dict[str, int] = {}
        raw_usage = body.get("usage") if isinstance(body, dict) else None
        if isinstance(raw_usage, dict):
            for key, value in raw_usage.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    usage[str(key)] = value
        return TranscriptionResult(
            text=text,
            duration_seconds=duration_seconds,
            request_id=task_id,
            usage=usage,
        )
