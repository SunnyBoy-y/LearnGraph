from __future__ import annotations

import base64
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
