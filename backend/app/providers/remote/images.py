from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Iterator
from typing import Any

import httpx

from app.providers.ports.image_generation import (
    ImageGenerationEvent,
    ImageGenerationRequest,
)
from app.providers.remote.openai import normalize_openai_api_base_url


class ImageGenerationProviderError(RuntimeError):
    pass


class ImageGenerationProviderTimeout(ImageGenerationProviderError):
    pass


class ImageGenerationProviderHTTPError(ImageGenerationProviderError):
    pass


class ImageGenerationProviderResponseError(ImageGenerationProviderError):
    pass


_MIME_BY_FORMAT = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}
_MAX_ENCODED_IMAGE_BYTES = 80 * 1024 * 1024


def _iter_sse_payloads(response: httpx.Response) -> Iterator[dict[str, Any]]:
    data_lines: list[str] = []
    event_name: str | None = None

    def decode_event() -> dict[str, Any] | None:
        nonlocal data_lines, event_name
        if not data_lines:
            event_name = None
            return None
        data = "\n".join(data_lines)
        data_lines = []
        current_event_name = event_name
        event_name = None
        if data == "[DONE]":
            return {"type": "done"}
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ImageGenerationProviderResponseError(
                "OpenAI Images returned invalid SSE JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ImageGenerationProviderResponseError(
                "OpenAI Images SSE data must be an object"
            )
        payload_type = payload.get("type")
        if current_event_name and payload_type and current_event_name != payload_type:
            raise ImageGenerationProviderResponseError(
                "OpenAI Images SSE event name does not match its payload type"
            )
        if current_event_name and not payload_type:
            payload["type"] = current_event_name
        return payload

    for line in response.iter_lines():
        if line == "":
            payload = decode_event()
            if payload is not None:
                yield payload
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            raise ImageGenerationProviderResponseError(
                "OpenAI Images returned a malformed SSE field"
            )
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            if event_name is not None:
                raise ImageGenerationProviderResponseError(
                    "OpenAI Images returned duplicate SSE event fields"
                )
            event_name = value
        elif field == "data":
            data_lines.append(value)
        elif field not in {"id", "retry"}:
            raise ImageGenerationProviderResponseError(
                "OpenAI Images returned an unsupported SSE field"
            )
    payload = decode_event()
    if payload is not None:
        yield payload


def _image_mime_type(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if (
        len(image_bytes) >= 12
        and image_bytes.startswith(b"RIFF")
        and image_bytes[8:12] == b"WEBP"
    ):
        return "image/webp"
    raise ImageGenerationProviderResponseError(
        "OpenAI Images returned bytes without a supported image signature"
    )


def _decode_image(value: object, expected_mime_type: str) -> tuple[bytes, str]:
    if not isinstance(value, str) or not value:
        raise ImageGenerationProviderResponseError(
            "OpenAI Images event has no base64 image"
        )
    if len(value) > _MAX_ENCODED_IMAGE_BYTES:
        raise ImageGenerationProviderResponseError(
            "OpenAI Images event exceeds the encoded image size limit"
        )
    try:
        image_bytes = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageGenerationProviderResponseError(
            "OpenAI Images event contains invalid base64"
        ) from exc
    if not image_bytes:
        raise ImageGenerationProviderResponseError(
            "OpenAI Images event contains an empty image"
        )
    detected_mime_type = _image_mime_type(image_bytes)
    if detected_mime_type != expected_mime_type:
        raise ImageGenerationProviderResponseError(
            "OpenAI Images event image format does not match the configured output format"
        )
    return image_bytes, detected_mime_type


def _usage(payload: object) -> dict[str, int | float] | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ImageGenerationProviderResponseError(
            "OpenAI Images completed event has invalid usage data"
        )
    flattened: dict[str, int | float] = {}

    def visit(prefix: str, value: object) -> None:
        if isinstance(value, bool):
            raise ImageGenerationProviderResponseError(
                "OpenAI Images usage values must be numeric"
            )
        if isinstance(value, (int, float)):
            if value < 0:
                raise ImageGenerationProviderResponseError(
                    "OpenAI Images usage values cannot be negative"
                )
            flattened[prefix] = value
            return
        if isinstance(value, dict):
            for key, nested in value.items():
                if not isinstance(key, str) or not key:
                    raise ImageGenerationProviderResponseError(
                        "OpenAI Images usage keys must be non-empty strings"
                    )
                visit(f"{prefix}.{key}" if prefix else key, nested)
            return
        raise ImageGenerationProviderResponseError(
            "OpenAI Images usage values must be numeric objects"
        )

    visit("", payload)
    return flattened


def parse_image_generation_event(
    payload: dict[str, Any], expected_mime_type: str
) -> ImageGenerationEvent:
    event_type = payload.get("type")
    if event_type == "image_generation.partial_image":
        partial_index = payload.get("partial_image_index")
        if (
            isinstance(partial_index, bool)
            or not isinstance(partial_index, int)
            or partial_index < 0
        ):
            raise ImageGenerationProviderResponseError(
                "OpenAI Images partial event has an invalid index"
            )
        image_bytes, mime_type = _decode_image(
            payload.get("b64_json"), expected_mime_type
        )
        return ImageGenerationEvent(
            type="partial_image",
            image_bytes=image_bytes,
            mime_type=mime_type,
            partial_index=partial_index,
        )
    if event_type == "image_generation.completed":
        image_bytes, mime_type = _decode_image(
            payload.get("b64_json"), expected_mime_type
        )
        return ImageGenerationEvent(
            type="completed",
            image_bytes=image_bytes,
            mime_type=mime_type,
            usage=_usage(payload.get("usage")),
        )
    raise ImageGenerationProviderResponseError(
        "OpenAI Images returned an unsupported SSE event type"
    )


class OpenAIImagesProvider:
    available = True
    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        api_key: str,
        output_format: str = "png",
        timeout_seconds: float = 180.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        normalized_format = output_format.strip().casefold()
        if normalized_format not in _MIME_BY_FORMAT:
            raise ValueError("OpenAI Images output format must be png, jpeg, or webp")
        if not model_id.strip():
            raise ValueError("OpenAI Images requires a configured model ID")
        self.provider_id = provider_id
        self.provider_type = "openai_images"
        self.model_id = model_id.strip()
        self.base_url = normalize_openai_api_base_url(base_url)
        self.api_key = api_key
        self.output_format = normalized_format
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.last_usage: dict[str, int | float] = {}
        self.last_request_id: str | None = None

    def stream_generate(
        self, request: ImageGenerationRequest
    ) -> Iterable[ImageGenerationEvent]:
        prompt = request.prompt.strip()
        if not prompt:
            raise ImageGenerationProviderResponseError(
                "Image generation prompt cannot be empty"
            )
        if (
            isinstance(request.partial_images, bool)
            or not isinstance(request.partial_images, int)
            or not 0 <= request.partial_images <= 3
        ):
            raise ImageGenerationProviderResponseError(
                "partial_images must be an integer from 0 to 3"
            )
        self.last_usage = {}
        self.last_request_id = None
        payload = {
            "model": self.model_id,
            "prompt": prompt,
            "stream": True,
            "partial_images": request.partial_images,
            "output_format": self.output_format,
        }
        completed = False
        partial_indexes: set[int] = set()
        try:
            with httpx.Client(
                headers={
                    "Accept": "text/event-stream",
                    "Authorization": f"Bearer {self.api_key}",
                },
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                with client.stream(
                    "POST",
                    f"{self.base_url}/images/generations",
                    json=payload,
                ) as response:
                    if not response.is_success:
                        raise ImageGenerationProviderHTTPError(
                            f"OpenAI Images returned HTTP {response.status_code}"
                        )
                    content_type = response.headers.get("content-type", "")
                    if content_type.split(";", 1)[0].strip().casefold() != "text/event-stream":
                        raise ImageGenerationProviderResponseError(
                            "OpenAI Images did not return an SSE response"
                        )
                    self.last_request_id = response.headers.get("x-request-id")
                    for event_payload in _iter_sse_payloads(response):
                        event_type = event_payload.get("type")
                        if event_type in {"error", "image_generation.failed"}:
                            raise ImageGenerationProviderHTTPError(
                                "OpenAI Images stream returned an error event"
                            )
                        if event_type == "done":
                            continue
                        if completed:
                            raise ImageGenerationProviderResponseError(
                                "OpenAI Images returned data after the completed event"
                            )
                        if event_type in {
                            "image_generation.partial_image",
                            "image_generation.completed",
                        }:
                            event = parse_image_generation_event(
                                event_payload,
                                _MIME_BY_FORMAT[self.output_format],
                            )
                            if event.type == "partial_image":
                                partial_index = event.partial_index
                                if partial_index is None:
                                    raise ImageGenerationProviderResponseError(
                                        "OpenAI Images partial event has no index"
                                    )
                                if partial_index >= request.partial_images:
                                    raise ImageGenerationProviderResponseError(
                                        "OpenAI Images returned more partial images than requested"
                                    )
                                if partial_index in partial_indexes:
                                    raise ImageGenerationProviderResponseError(
                                        "OpenAI Images returned a duplicate partial image index"
                                    )
                                partial_indexes.add(partial_index)
                                yield event
                                continue
                            usage = event.usage
                            self.last_usage = dict(usage or {})
                            completed = True
                            yield event
                            continue
                        raise ImageGenerationProviderResponseError(
                            "OpenAI Images returned an unsupported SSE event type"
                        )
        except httpx.TimeoutException as exc:
            raise ImageGenerationProviderTimeout(
                "OpenAI Images stream timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageGenerationProviderHTTPError(
                "OpenAI Images request failed"
            ) from exc
        if not completed:
            raise ImageGenerationProviderResponseError(
                "OpenAI Images stream ended before the completed event"
            )


class UnavailableImageGenerationProvider:
    available = False
    remote_capability = False
    provider_type = "unavailable"

    def __init__(
        self,
        reason: str,
        *,
        provider_id: str = "unavailable",
        model_id: str = "unavailable",
    ) -> None:
        self.reason = reason
        self.provider_id = provider_id
        self.model_id = model_id

    def stream_generate(
        self, request: ImageGenerationRequest
    ) -> Iterable[ImageGenerationEvent]:
        del request
        raise ImageGenerationProviderError(self.reason)
