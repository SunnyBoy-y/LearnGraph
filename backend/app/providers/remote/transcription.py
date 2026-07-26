from __future__ import annotations

import json

import httpx

from app.providers.ports.transcription import TranscriptionResult


class TranscriptionProviderError(RuntimeError):
    pass


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
            raise TranscriptionProviderError("The transcription service could not be reached") from exc
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
