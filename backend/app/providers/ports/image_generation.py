from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class ImageSourceInput:
    """Validated source image bytes for an edit/reference generation."""

    image_bytes: bytes
    mime_type: str
    name: str = "source.png"


@dataclass(frozen=True, slots=True)
class ImageGenerationRequest:
    prompt: str
    partial_images: int = 2
    # OpenAI Images `size` value. `auto` lets the provider choose.
    size: str = "auto"
    # When non-empty, the provider must condition the generation on these
    # source images (image edit) instead of a pure text-to-image call.
    source_images: tuple[ImageSourceInput, ...] = ()


@dataclass(frozen=True, slots=True)
class ImageGenerationEvent:
    type: Literal["partial_image", "completed"]
    image_bytes: bytes
    mime_type: str
    partial_index: int | None = None
    usage: dict[str, int | float] | None = None


class ImageGenerationProviderPort(Protocol):
    provider_id: str
    provider_type: str
    model_id: str
    available: bool
    remote_capability: bool

    def stream_generate(
        self, request: ImageGenerationRequest
    ) -> Iterable[ImageGenerationEvent]: ...
