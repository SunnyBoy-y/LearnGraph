from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from app.core.errors import AppError


@dataclass(frozen=True, slots=True)
class SensitiveFinding:
    category: str
    path: str


class SensitiveDataFilter:
    """Deterministic credential/high-risk secret gate for event and candidate payloads."""

    _PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
        ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I)),
        ("api_key", re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b")),
        ("connection_string", re.compile(r"\b(?:postgres|mysql|mongodb)(?:ql)?://[^\s]+", re.I)),
        ("password_assignment", re.compile(r"\b(?:password|passwd|pwd)\s*[:=]\s*\S+", re.I)),
        ("cookie", re.compile(r"\b(?:session|auth|access)[_-]?(?:cookie|token)\s*[:=]\s*\S+", re.I)),
    )

    def scan(self, payload: dict[str, Any]) -> list[SensitiveFinding]:
        findings: list[SensitiveFinding] = []

        def visit(value: Any, path: str) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized = str(key).casefold()
                    child_path = f"{path}.{key}" if path else str(key)
                    if normalized in {
                        "api_key", "apikey", "password", "cookie", "access_token",
                        "refresh_token", "private_key", "authorization",
                    } and child not in (None, "", "[REDACTED]"):
                        findings.append(SensitiveFinding("sensitive_field", child_path))
                    visit(child, child_path)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, f"{path}[{index}]")
            elif isinstance(value, str):
                for category, pattern in self._PATTERNS:
                    if pattern.search(value):
                        findings.append(SensitiveFinding(category, path or "$"))

        visit(payload, "")
        unique = {(finding.category, finding.path): finding for finding in findings}
        return list(unique.values())

    def require_safe(self, payload: dict[str, Any]) -> None:
        findings = self.scan(payload)
        if findings:
            raise AppError(
                422,
                "sensitive_memory_payload_rejected",
                "Secrets and credentials cannot be stored in long-term memory",
                {"categories": sorted({finding.category for finding in findings})},
            )

    @staticmethod
    def safe_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
