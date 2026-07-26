from __future__ import annotations

from typing import Protocol


class EmbeddingProviderPort(Protocol):
    """Optional semantic-retrieval capability for the memory subsystem.

    Memory recall works without any embedding provider (scope/strength
    heuristics only). When a workspace configures an OpenAI-compatible
    embedding endpoint (for example Qwen ``text-embedding-v4`` through the
    DashScope compatible mode), retrieval blends cosine similarity on top of
    the heuristic score instead of replacing it.
    """

    provider_id: str
    model_id: str
    available: bool

    def embed(self, texts: list[str]) -> list[list[float]]: ...
