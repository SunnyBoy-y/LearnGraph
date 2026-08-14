"""Cross-provider JSON Schema sanitization for Agent tool definitions.

OpenAI-compatible gateways that translate ``tools`` into Gemini
``function_declarations`` enforce the Gemini ``FunctionDeclaration``
subset of JSON Schema (an OpenAPI 3.0 style object).  Two constructs used
by LearnGraph's tool definitions break that subset and make the upstream
stream fail with HTTP 400:

- ``"type": ["string", "null"]`` — the protobuf ``type`` field is not
  repeating, so the array form is rejected with ``Proto field is not
  repeating, cannot start list``.
- ``{"type": "null"}`` as a standalone branch inside ``anyOf``/``oneOf``
  — ``null`` is not a valid standalone type in the Gemini subset.

``sanitize_tool_definitions`` folds every tool schema into the shared
single-``type`` subset before it reaches any provider transport.  The
runtime still validates tool arguments against its own Pydantic schemas
(the Agent dispatcher reads optional fields through ``arguments.get``),
so dropping ``null`` from the model-facing schema never weakens
execution checks — it only stops the upstream 400.  All other keywords
(``enum``/``minimum``/``required``/…) are preserved verbatim.
"""

from __future__ import annotations

from typing import Any

_UNION_KEYS = ("anyOf", "oneOf")
# Keywords whose values are themselves schema maps that must be walked.
_SCHEMA_MAP_KEYS = ("properties", "$defs", "definitions", "patternProperties")


def sanitize_json_schema(node: Any) -> Any:
    """Return a copy of ``node`` restricted to the shared JSON Schema subset.

    Rules:
    - ``type`` arrays collapse to the first non-``"null"`` type, keeping a
      ``nullable`` hint when the original list allowed ``null``.
    - ``anyOf`` / ``oneOf`` unions collapse to the first non-``"null"``
      branch; pure ``{"required": [...]}``-constraint branches are merged
      into the parent ``required`` list instead of being dropped.
    - Nested ``properties`` / ``items`` / ``$defs`` are sanitized
      recursively; every other keyword passes through unchanged.
    """

    if isinstance(node, list):
        return [sanitize_json_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    result: dict[str, Any] = {}

    # 1) type arrays: ["string", "null"] -> type: string (+ nullable)
    raw_type = node.get("type")
    if isinstance(raw_type, list):
        non_null = [item for item in raw_type if item != "null"]
        result["type"] = non_null[0] if non_null else "string"
        if "null" in raw_type:
            result["nullable"] = True
    elif raw_type is not None:
        result["type"] = raw_type

    # 2) anyOf / oneOf unions
    union_key = next(
        (key for key in _UNION_KEYS if isinstance(node.get(key), list)),
        None,
    )
    if union_key is not None:
        branches = [sanitize_json_schema(item) for item in node[union_key]]
        non_null = [branch for branch in branches if branch.get("type") != "null"]
        had_null = len(non_null) != len(branches)
        if non_null and all(set(branch.keys()) <= {"required"} for branch in non_null):
            # Pure required-constraint union: merge into the parent list.
            merged: list[str] = []
            for branch in non_null:
                for name in branch.get("required", []):
                    if name not in merged:
                        merged.append(name)
            result["required"] = merged
        elif len(non_null) == 1 or non_null:
            # One typed branch, or a multi-type union: keep the first typed
            # branch.  Gemini rejects anyOf outright and LearnGraph revalidates
            # arguments at dispatch time, so collapsing is safe.
            branch = non_null[0]
            for key, value in branch.items():
                result[key] = value
            if had_null:
                result["nullable"] = True
        else:
            # Only null branches were present; degrade to a plain string.
            result["type"] = "string"

    # 3) every remaining keyword, recursing into nested schemas
    for key, value in node.items():
        if key == "type" or key in _UNION_KEYS:
            continue
        if key in _SCHEMA_MAP_KEYS and isinstance(value, dict):
            result[key] = {
                str(name): sanitize_json_schema(item)
                for name, item in value.items()
            }
            continue
        result[key] = sanitize_json_schema(value)

    return result


def sanitize_tool_definitions(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitize a list of tool definitions for cross-provider compatibility.

    Accepts the OpenAI Chat Completions shape
    (``{"type": "function", "function": {"name", "description",
    "parameters"}}``), the Responses shape (``{"type": "function",
    "name", "parameters"}``) and the Anthropic shape (``{"name",
    "description", "input_schema"}``).  Only the parameter schema is
    rewritten; the outer tool structure is preserved so providers can
    still apply their own conversions afterwards.
    """

    sanitized: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            sanitized.append(tool)
            continue
        copy = dict(tool)
        function = copy.get("function")
        if isinstance(function, dict):
            function_copy = dict(function)
            parameters = function_copy.get("parameters")
            if isinstance(parameters, dict):
                function_copy["parameters"] = sanitize_json_schema(parameters)
            copy["function"] = function_copy
        elif isinstance(copy.get("parameters"), dict):
            copy["parameters"] = sanitize_json_schema(copy["parameters"])
        elif isinstance(copy.get("input_schema"), dict):
            copy["input_schema"] = sanitize_json_schema(copy["input_schema"])
        sanitized.append(copy)
    return sanitized
