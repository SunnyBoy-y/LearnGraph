"""Compatibility shim for the upgraded temporal normalizer.

M3 moved the deterministic normalizer to ``app.services.temporal_normalizer``
and versioned it as ``temporal-v2``. Keep this module importable for callers
that were written against the v1 location.
"""

from __future__ import annotations

from app.services.temporal_normalizer import (
    NORMALIZER_VERSION,
    TemporalNormalizer,
    TemporalSemantics,
    temporal_semantics_payload,
)

__all__ = [
    "NORMALIZER_VERSION",
    "TemporalNormalizer",
    "TemporalSemantics",
    "temporal_semantics_payload",
]
