from __future__ import annotations

from typing import Any, Protocol


class DeepResearchProviderPort(Protocol):
    """Provider-neutral boundary for a long-running research task."""

    provider_id: str
    remote_capability: bool

    def capabilities(self) -> dict[str, Any]: ...

    def estimate(self, *, question: str, budget_cny: float) -> float: ...

    def create_task(
        self,
        *,
        question: str,
        budget_cny: float,
        source_scope: list[str],
        allowed_domains: list[str],
    ) -> str: ...

    def get_task(self, task_id: str) -> dict[str, Any]: ...

    def cancel_task(self, task_id: str) -> None: ...

    def probe(self) -> dict[str, Any]: ...
