"""Versioned automatic mapping provenance; explicit user declarations never migrate."""
from typing import Any

THINKING_FIELDS = ("reasoning_efforts", "thinking_mapping", "default_thinking_mode", "reasoning_parameter", "thinking_required")


def automatic_thinking_metadata(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {"thinking_mapping_version": 2}
    controls = current.get("reasoning_controls")
    if isinstance(controls, list):
        result["reasoning_controls"] = controls
    if not isinstance(previous, dict) or previous.get("capability_source") != "official_catalog":
        return result
    # A different wire shape cannot be compared by its scalar values.
    if previous.get("reasoning_parameter") != current.get("reasoning_parameter"):
        return result
    if previous.get("thinking_mapping_version") == 2:
        legacy = previous.get("legacy_thinking_mapping")
    else:
        legacy = previous.get("thinking_mapping")
    if isinstance(legacy, dict) and legacy:
        result["legacy_thinking_mapping"] = dict(legacy)
    return result
