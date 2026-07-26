from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Iterator
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.providers.ports.image_generation import (
    ImageGenerationEvent,
    ImageGenerationRequest,
    ImageSourceInput,
)
from app.providers.qwen_catalog import is_dashscope_api_base_url
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

_DASHSCOPE_NATIVE_GENERATION_PATH = (
    "/api/v1/services/aigc/multimodal-generation/generation"
)


def dashscope_native_generation_url(base_url: str) -> str | None:
    """Return the DashScope-native image endpoint for ``base_url``, if any.

    Alibaba's gateways (``dashscope*.aliyuncs.com`` and dedicated
    ``*.maas.aliyuncs.com`` deployments) advertise image models on the
    compatible-mode ``/models`` list but do not route ``/images/generations``;
    generation only answers on the native multimodal-generation endpoint of
    the same host.
    """

    try:
        parsed = urlsplit(base_url.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https" or not host:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if not host.endswith(".maas.aliyuncs.com") and not is_dashscope_api_base_url(
        base_url
    ):
        return None
    return f"https://{parsed.netloc}{_DASHSCOPE_NATIVE_GENERATION_PATH}"


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


def _decode_image_bytes(value: str) -> bytes:
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
    return image_bytes


def _decode_image(value: object, expected_mime_type: str) -> tuple[bytes, str]:
    if not isinstance(value, str) or not value:
        raise ImageGenerationProviderResponseError(
            "OpenAI Images event has no base64 image"
        )
    image_bytes = _decode_image_bytes(value)
    detected_mime_type = _image_mime_type(image_bytes)
    if detected_mime_type != expected_mime_type:
        raise ImageGenerationProviderResponseError(
            "OpenAI Images event image format does not match the configured output format"
        )
    return image_bytes, detected_mime_type


def _response_error_detail(response: httpx.Response) -> str:
    """Extract a short human-readable error message from a failed response."""

    try:
        response.read()
    except httpx.HTTPError:
        return ""
    text = (response.text or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            code = error.get("code")
            if isinstance(message, str) and message.strip():
                prefix = (
                    f"{code.strip()}: "
                    if isinstance(code, str) and code.strip()
                    else ""
                )
                text = f"{prefix}{message.strip()}"
        elif isinstance(payload.get("message"), str) and payload["message"].strip():
            # DashScope-native errors: {"code": "...", "message": "...", ...}
            code = payload.get("code")
            prefix = (
                f"{code.strip()}: " if isinstance(code, str) and code.strip() else ""
            )
            text = f"{prefix}{payload['message'].strip()}"
    return " ".join(text.split())[:300]


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


def _native_image_url(payload: dict[str, Any]) -> str | None:
    """Find the first result image URL in a DashScope-native response."""

    output = payload.get("output")
    if not isinstance(output, dict):
        return None
    choices = output.get("choices")
    if not isinstance(choices, list):
        return None
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            image = part.get("image")
            if isinstance(image, str) and image.startswith(("http://", "https://")):
                return image
    return None


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
        self.native_generation_url = dashscope_native_generation_url(self.base_url)
        self.api_key = api_key
        self.output_format = normalized_format
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.last_usage: dict[str, int | float] = {}
        self.last_request_id: str | None = None

    # Streaming is an OpenAI-only extension of /images/generations.  These
    # statuses mean the gateway rejected the request shape itself (for example
    # DashScope refusing `stream` / `partial_images` / `output_format`), so a
    # plain non-streaming request is retried once before giving up.
    _STREAM_FALLBACK_STATUSES = frozenset({400, 404, 405, 415, 422})

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
        if self.native_generation_url is not None:
            yield from self._generate_native(
                prompt,
                self.native_generation_url,
                source_images=request.source_images,
            )
            return
        if request.source_images:
            # Image edits are a one-shot multipart endpoint; partial previews
            # are not portable across OpenAI-compatible gateways.
            yield from self._edit_with_sources(prompt, request.source_images)
            return
        streaming_payload = {
            "model": self.model_id,
            "prompt": prompt,
            "stream": True,
            "partial_images": request.partial_images,
            "output_format": self.output_format,
        }
        try:
            with httpx.Client(
                headers={
                    "Accept": "text/event-stream",
                    "Authorization": f"Bearer {self.api_key}",
                },
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                fallback_detail = ""
                with client.stream(
                    "POST",
                    f"{self.base_url}/images/generations",
                    json=streaming_payload,
                ) as response:
                    if response.is_success:
                        content_type = (
                            response.headers.get("content-type", "")
                            .split(";", 1)[0]
                            .strip()
                            .casefold()
                        )
                        self.last_request_id = response.headers.get("x-request-id")
                        if content_type == "text/event-stream":
                            yield from self._consume_sse(response, request)
                            return
                        if content_type == "application/json":
                            # OpenAI-compatible gateways without streaming
                            # support ignore `stream: true` and answer with the
                            # standard one-shot Images JSON payload.
                            response.read()
                            yield self._json_completed_event(response)
                            return
                        raise ImageGenerationProviderResponseError(
                            "Image Provider returned neither an SSE stream nor a JSON generation response"
                        )
                    detail = _response_error_detail(response)
                    if response.status_code not in self._STREAM_FALLBACK_STATUSES:
                        raise ImageGenerationProviderHTTPError(
                            f"Image Provider returned HTTP {response.status_code}"
                            + (f": {detail}" if detail else "")
                        )
                    fallback_detail = detail or f"HTTP {response.status_code}"
                # The streaming request was rejected; retry once with the
                # minimal portable payload and accept a one-shot JSON result.
                fallback_response = client.post(
                    f"{self.base_url}/images/generations",
                    headers={"Accept": "application/json"},
                    json={"model": self.model_id, "prompt": prompt},
                )
                if not fallback_response.is_success:
                    detail = (
                        _response_error_detail(fallback_response) or fallback_detail
                    )
                    raise ImageGenerationProviderHTTPError(
                        f"Image Provider returned HTTP {fallback_response.status_code}"
                        + (f": {detail}" if detail else "")
                    )
                self.last_request_id = fallback_response.headers.get("x-request-id")
                yield self._json_completed_event(fallback_response)
        except httpx.TimeoutException as exc:
            raise ImageGenerationProviderTimeout(
                "OpenAI Images stream timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageGenerationProviderHTTPError(
                "OpenAI Images request failed"
            ) from exc

    def _generate_native(
        self,
        prompt: str,
        url: str,
        *,
        source_images: tuple[ImageSourceInput, ...] = (),
    ) -> Iterator[ImageGenerationEvent]:
        # DashScope's native generation endpoint is one-shot JSON with no SSE
        # variant, so partial previews are unavailable and a single completed
        # event is emitted.  Source images (image edit) travel as additional
        # base64 content items on the same multimodal message.
        content: list[dict[str, Any]] = []
        for source in source_images:
            encoded = base64.b64encode(source.image_bytes).decode("ascii")
            content.append({"image": f"data:{source.mime_type};base64,{encoded}"})
        content.append({"text": prompt})
        payload = {
            "model": self.model_id,
            "input": {"messages": [{"role": "user", "content": content}]},
        }
        try:
            with httpx.Client(
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise ImageGenerationProviderTimeout(
                "DashScope image generation timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageGenerationProviderHTTPError(
                "DashScope image generation request failed"
            ) from exc
        if not response.is_success:
            detail = _response_error_detail(response)
            raise ImageGenerationProviderHTTPError(
                f"Image Provider returned HTTP {response.status_code}"
                + (f": {detail}" if detail else "")
            )
        yield self._native_completed_event(response)

    _SOURCE_SUFFIX_BY_MIME = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }

    def _edit_with_sources(
        self, prompt: str, source_images: tuple[ImageSourceInput, ...]
    ) -> Iterator[ImageGenerationEvent]:
        """One-shot OpenAI-compatible /images/edits call with source images."""

        # OpenAI accepts a single file as `image`; multiple references use the
        # `image[]` array form (gpt-image-1). Compatible gateways follow suit.
        field = "image" if len(source_images) == 1 else "image[]"
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for index, source in enumerate(source_images):
            suffix = self._SOURCE_SUFFIX_BY_MIME.get(source.mime_type, ".png")
            name = source.name.strip() or f"source-{index}{suffix}"
            files.append((field, (name, source.image_bytes, source.mime_type)))
        try:
            with httpx.Client(
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post(
                    f"{self.base_url}/images/edits",
                    data={"model": self.model_id, "prompt": prompt},
                    files=files,
                )
        except httpx.TimeoutException as exc:
            raise ImageGenerationProviderTimeout(
                "OpenAI Images edit request timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageGenerationProviderHTTPError(
                "OpenAI Images edit request failed"
            ) from exc
        if not response.is_success:
            detail = _response_error_detail(response)
            raise ImageGenerationProviderHTTPError(
                f"Image Provider returned HTTP {response.status_code}"
                + (f": {detail}" if detail else "")
            )
        self.last_request_id = response.headers.get("x-request-id")
        yield self._json_completed_event(response)

    def _native_completed_event(self, response: httpx.Response) -> ImageGenerationEvent:
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ImageGenerationProviderResponseError(
                "Image Provider returned invalid JSON for a native generation"
            ) from exc
        if not isinstance(payload, dict):
            raise ImageGenerationProviderResponseError(
                "Image Provider returned a non-object native generation response"
            )
        request_id = payload.get("request_id")
        if isinstance(request_id, str) and request_id:
            self.last_request_id = request_id
        image_url = _native_image_url(payload)
        if image_url is None:
            raise ImageGenerationProviderResponseError(
                "Image Provider native response has no generated image URL"
            )
        image_bytes = self._download_image(image_url)
        mime_type = _image_mime_type(image_bytes)
        try:
            usage = _usage(payload.get("usage"))
        except ImageGenerationProviderResponseError:
            usage = None
        self.last_usage = dict(usage or {})
        return ImageGenerationEvent(
            type="completed",
            image_bytes=image_bytes,
            mime_type=mime_type,
            usage=usage,
        )

    def _consume_sse(
        self, response: httpx.Response, request: ImageGenerationRequest
    ) -> Iterator[ImageGenerationEvent]:
        completed = False
        partial_indexes: set[int] = set()
        for event_payload in _iter_sse_payloads(response):
            event_type = event_payload.get("type")
            if event_type in {"error", "image_generation.failed"}:
                error = event_payload.get("error")
                detail = (
                    error.get("message", "").strip()
                    if isinstance(error, dict)
                    and isinstance(error.get("message"), str)
                    else ""
                )
                raise ImageGenerationProviderHTTPError(
                    "OpenAI Images stream returned an error event"
                    + (f": {detail}" if detail else "")
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
        if not completed:
            raise ImageGenerationProviderResponseError(
                "OpenAI Images stream ended before the completed event"
            )

    def _json_completed_event(self, response: httpx.Response) -> ImageGenerationEvent:
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ImageGenerationProviderResponseError(
                "Image Provider returned invalid JSON for a non-streaming generation"
            ) from exc
        if not isinstance(payload, dict):
            raise ImageGenerationProviderResponseError(
                "Image Provider returned a non-object JSON generation response"
            )
        data = payload.get("data")
        first = data[0] if isinstance(data, list) and data else None
        if not isinstance(first, dict):
            raise ImageGenerationProviderResponseError(
                "Image Provider JSON response has no generated image data"
            )
        b64_value = first.get("b64_json")
        url_value = first.get("url")
        if isinstance(b64_value, str) and b64_value:
            image_bytes = _decode_image_bytes(b64_value)
        elif isinstance(url_value, str) and url_value.startswith(
            ("http://", "https://")
        ):
            image_bytes = self._download_image(url_value)
        else:
            raise ImageGenerationProviderResponseError(
                "Image Provider JSON response contains neither b64_json nor an image URL"
            )
        # Gateways decide the output format themselves in one-shot mode, so
        # trust the sniffed signature instead of the configured output_format.
        mime_type = _image_mime_type(image_bytes)
        try:
            usage = _usage(payload.get("usage"))
        except ImageGenerationProviderResponseError:
            usage = None
        self.last_usage = dict(usage or {})
        return ImageGenerationEvent(
            type="completed",
            image_bytes=image_bytes,
            mime_type=mime_type,
            usage=usage,
        )

    def _download_image(self, url: str) -> bytes:
        # Result URLs point at CDN/object storage; use a clean client so the
        # provider API key is never sent to that host.
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                transport=self.transport,
                follow_redirects=True,
            ) as client:
                response = client.get(url)
        except httpx.TimeoutException as exc:
            raise ImageGenerationProviderTimeout(
                "Image result download timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise ImageGenerationProviderHTTPError(
                "Image result download failed"
            ) from exc
        if not response.is_success:
            raise ImageGenerationProviderHTTPError(
                f"Image result download returned HTTP {response.status_code}"
            )
        content = response.content
        if not content:
            raise ImageGenerationProviderResponseError(
                "Image result download returned an empty body"
            )
        if len(content) > _MAX_ENCODED_IMAGE_BYTES:
            raise ImageGenerationProviderResponseError(
                "Image result exceeds the download size limit"
            )
        return content


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
