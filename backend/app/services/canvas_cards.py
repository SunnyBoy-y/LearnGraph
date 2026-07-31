"""Canvas Builder helpers for conversational generative micro-apps.

Channel A (trusted declarative components) and the first-class magic_card Part
are assembled here. Channel B runtime compilation remains unavailable, while
self-contained HTML previews run inside an opaque-origin browser sandbox.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.core.errors import AppError
from app.services.components import BUILTIN_COMPONENT_IDS, _builtin_specs, _validate_instance


CHANNEL_A_TYPES = frozenset(
    {
        "weather_card",
        "metric_card",
        "option_group",
        "single_choice",
        "multiple_choice",
        "fill_blank",
        "short_answer_table",
        "image_frame",
    }
)
MAX_MAGIC_CARD_PREVIEW_CHARS = 100_000

DEFAULT_RENDER_CONTRACT: dict[str, Any] = {
    "slot": "inline",
    "available_width": 684,
    "min_width": 320,
    "max_width": 760,
    "max_height": 720,
    "device_pixel_ratio": 1,
    "theme": "light",
    "locale": "zh-CN",
    "font_scale": 1,
    "reduced_motion": False,
    "supports": {
        "canvas2d": True,
        "webgl": False,
        "fullscreen": True,
        "file_drop": False,
        "sandboxed_html_preview": True,
        "react_sandbox_runtime": False,
    },
    "component_catalog": sorted(CHANNEL_A_TYPES),
    "capabilities": [
        "card.local-state",
        "agent.continue",
        "canvas.emit_trusted_component",
        "canvas.emit_magic_card",
    ],
    "channels": {
        "declarative": True,
        "sandboxed_html_preview": True,
        "react_sandbox": False,
        "reason": "isolated_browser_renderer_not_configured",
    },
}


def get_render_contract(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = dict(DEFAULT_RENDER_CONTRACT)
    if not overrides:
        return contract
    for key in (
        "slot",
        "available_width",
        "min_width",
        "max_width",
        "max_height",
        "device_pixel_ratio",
        "theme",
        "locale",
        "font_scale",
        "reduced_motion",
    ):
        if key in overrides and overrides[key] is not None:
            contract[key] = overrides[key]
    supports = overrides.get("supports")
    if isinstance(supports, dict):
        merged = dict(contract["supports"])
        for key, value in supports.items():
            if key in merged:
                merged[key] = bool(value)
        contract["supports"] = merged
    return contract


def _strip_nulls(value: Any) -> Any:
    """Drop JSON nulls that models often emit for optional fields."""

    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if item is None:
                continue
            cleaned[key] = _strip_nulls(item)
        return cleaned
    if isinstance(value, list):
        return [_strip_nulls(item) for item in value if item is not None]
    return value


def _normalize_answer_key(props: dict[str, Any]) -> dict[str, Any]:
    """Keep optional answer-key fields used for client-side grading."""

    cleaned = dict(props)
    correct_ids = cleaned.get("correct_option_ids")
    if isinstance(correct_ids, list):
        cleaned["correct_option_ids"] = [
            item.strip()[:80]
            for item in correct_ids
            if isinstance(item, str) and item.strip()
        ][:100]
    elif "correct_option_ids" in cleaned:
        cleaned.pop("correct_option_ids", None)

    # Derive answer key from option-level is_correct when not provided.
    if not cleaned.get("correct_option_ids"):
        options = cleaned.get("options")
        if isinstance(options, list):
            derived = [
                str(item.get("id")).strip()[:80]
                for item in options
                if isinstance(item, dict)
                and item.get("is_correct") is True
                and isinstance(item.get("id"), str)
                and str(item.get("id")).strip()
            ]
            if derived:
                cleaned["correct_option_ids"] = derived

    correct_answers = cleaned.get("correct_answers")
    if isinstance(correct_answers, list):
        cleaned["correct_answers"] = [
            item.strip()[:2_000]
            for item in correct_answers
            if isinstance(item, str) and item.strip()
        ][:20]
    elif isinstance(correct_answers, str) and correct_answers.strip():
        cleaned["correct_answers"] = [correct_answers.strip()[:2_000]]
    elif "correct_answers" in cleaned:
        cleaned.pop("correct_answers", None)

    for field in ("explanation", "feedback_correct", "feedback_incorrect"):
        value = cleaned.get(field)
        if isinstance(value, str) and value.strip():
            limit = 5_000 if field == "explanation" else 2_000
            cleaned[field] = value.strip()[:limit]
        elif field in cleaned:
            cleaned.pop(field, None)
    return cleaned


def _answer_key_renderer_props(props: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in (
        "correct_option_ids",
        "correct_answers",
        "explanation",
        "feedback_correct",
        "feedback_incorrect",
    ):
        if field in props:
            out[field] = props[field]
    return out


def _normalize_props(component_type: str, props: dict[str, Any]) -> dict[str, Any]:
    """Map Agent-friendly props onto the builtin data_schema shapes."""

    props = _strip_nulls(props if isinstance(props, dict) else {})
    if not isinstance(props, dict):
        props = {}

    if component_type in {"option_group", "single_choice", "multiple_choice"}:
        if "options" not in props and isinstance(props.get("choices"), list):
            props = {**props, "options": props["choices"]}
        options = props.get("options")
        if isinstance(options, list):
            cleaned_options: list[dict[str, Any]] = []
            for index, item in enumerate(options):
                if not isinstance(item, dict):
                    continue
                option_id = item.get("id")
                label = item.get("label")
                if not isinstance(option_id, str) or not option_id.strip():
                    option_id = f"opt_{index + 1}"
                if not isinstance(label, str) or not label.strip():
                    continue
                cleaned: dict[str, Any] = {
                    "id": option_id.strip()[:80],
                    "label": label.strip()[:500],
                }
                description = item.get("description")
                if isinstance(description, str) and description.strip():
                    cleaned["description"] = description.strip()[:2_000]
                if item.get("is_correct") is True:
                    cleaned["is_correct"] = True
                cleaned_options.append(cleaned)
            props = {**props, "options": cleaned_options}
        if "options" not in props or not isinstance(props.get("options"), list):
            props = {**props, "options": []}
        if component_type != "option_group" and "prompt" not in props:
            title = props.get("title")
            if isinstance(title, str) and title.strip():
                props = {**props, "prompt": title}
        if "title" not in props:
            prompt = props.get("prompt")
            if isinstance(prompt, str) and prompt.strip():
                props = {**props, "title": prompt}
        props = _normalize_answer_key(props)
    if component_type == "fill_blank":
        if "blank_ids" not in props:
            props = {**props, "blank_ids": ["answer"]}
        if "prompt" not in props and isinstance(props.get("title"), str):
            props = {**props, "prompt": props["title"]}
        if "title" not in props and isinstance(props.get("prompt"), str):
            props = {**props, "title": props["prompt"]}
        if "prompt" not in props and "title" not in props:
            props = {**props, "title": "请填写", "prompt": "请填写"}
        props = _normalize_answer_key(props)
    if component_type == "short_answer_table":
        if "title" not in props:
            props = {**props, "title": "简答题表"}
        if not isinstance(props.get("columns"), list):
            props = {**props, "columns": ["问题", "回答"]}
        if not isinstance(props.get("rows"), list):
            props = {**props, "rows": [[""]]}
        props = _normalize_answer_key(props)
    if component_type == "image_frame":
        status = props.get("status")
        if status in {"completed", "ready"}:
            props = {**props, "status": "ready" if status == "completed" else status}
        elif status in {"queued", "running", "placeholder"}:
            props = {**props, "status": "placeholder"}
        elif status in {"failed", "cancelled"}:
            props = {**props, "status": "failed"}
        elif not isinstance(status, str):
            props = {**props, "status": "placeholder"}
        if not isinstance(props.get("alt"), str) or not str(props.get("alt")).strip():
            props = {
                **props,
                "alt": props.get("title") if isinstance(props.get("title"), str) else "image",
            }
    return props


def build_trusted_component_part(
    *,
    component_type: str,
    props: dict[str, Any],
    component_id: str | None = None,
    allowed_events: list[str] | None = None,
    schema_version: str = "1.0",
) -> dict[str, Any]:
    if component_type not in CHANNEL_A_TYPES or component_type not in BUILTIN_COMPONENT_IDS:
        raise AppError(
            422,
            "canvas_component_type_unsupported",
            f"Component type '{component_type}' is not in the channel-A builtin catalog",
        )
    if not isinstance(props, dict):
        raise AppError(422, "invalid_tool_arguments", "props must be a JSON object")

    specs = _builtin_specs()
    spec = specs[component_type]
    normalized_props = _normalize_props(component_type, props)
    _validate_instance(
        spec["data_schema"],
        normalized_props,
        label=f"{component_type}.props",
        trusted_main_dom=True,
    )

    events = allowed_events
    if events is None:
        action_events: list[str] = []
        for action in normalized_props.get("actions") or []:
            if isinstance(action, dict):
                event_name = action.get("event") or action.get("id")
                if isinstance(event_name, str) and event_name:
                    action_events.append(event_name)
        if component_type == "image_frame":
            events = []
        elif action_events:
            events = action_events
        else:
            events = ["submit"]
    cleaned_events: list[str] = []
    for event in events:
        if isinstance(event, str) and event and event not in cleaned_events:
            cleaned_events.append(event[:80])

    # Frontend trusted renderers expect either flat option/text schemas with a
    # `props` envelope or the graph-style envelope. Always emit the envelope.
    if component_type in {"option_group", "single_choice", "multiple_choice"}:
        title = (
            normalized_props.get("title")
            or normalized_props.get("prompt")
            or "请选择"
        )
        renderer_props = {
            "title": str(title)[:500],
            "options": normalized_props.get("options") or [],
            "allow_custom": bool(normalized_props.get("allow_custom", True)),
            "allow_skip": bool(normalized_props.get("allow_skip", True)),
            **_answer_key_renderer_props(normalized_props),
        }
        description = normalized_props.get("description")
        if isinstance(description, str) and description.strip():
            renderer_props["description"] = description.strip()[:2_000]
        submit_label = normalized_props.get("submit_label")
        if isinstance(submit_label, str) and submit_label.strip():
            renderer_props["submit_label"] = submit_label.strip()[:80]
    elif component_type in {"fill_blank", "short_answer_table"}:
        if component_type == "short_answer_table" and "columns" in normalized_props:
            # Keep table data accessible while the simple text renderer uses title.
            renderer_props = {
                "title": normalized_props.get("title") or "简答题表",
                "multiline": True,
                "columns": normalized_props.get("columns"),
                "rows": normalized_props.get("rows"),
                **_answer_key_renderer_props(normalized_props),
            }
            description = normalized_props.get("description")
            if isinstance(description, str) and description.strip():
                renderer_props["description"] = description.strip()[:2_000]
            elif normalized_props.get("columns"):
                renderer_props["description"] = " / ".join(
                    str(item) for item in (normalized_props.get("columns") or [])
                )
            placeholder = normalized_props.get("placeholder")
            if isinstance(placeholder, str) and placeholder.strip():
                renderer_props["placeholder"] = placeholder.strip()[:500]
            submit_label = normalized_props.get("submit_label")
            if isinstance(submit_label, str) and submit_label.strip():
                renderer_props["submit_label"] = submit_label.strip()[:80]
        else:
            title = (
                normalized_props.get("title")
                or normalized_props.get("prompt")
                or "请填写"
            )
            renderer_props = {
                "title": str(title)[:500],
                "multiline": bool(normalized_props.get("multiline", False)),
                **_answer_key_renderer_props(normalized_props),
            }
            description = normalized_props.get("description")
            if isinstance(description, str) and description.strip():
                renderer_props["description"] = description.strip()[:2_000]
            placeholder = normalized_props.get("placeholder")
            if isinstance(placeholder, str) and placeholder.strip():
                renderer_props["placeholder"] = placeholder.strip()[:500]
            submit_label = normalized_props.get("submit_label")
            if isinstance(submit_label, str) and submit_label.strip():
                renderer_props["submit_label"] = submit_label.strip()[:80]
    elif component_type == "image_frame":
        status = str(normalized_props.get("status") or "placeholder")
        frontend_status = {
            "placeholder": "queued",
            "ready": "completed",
            "failed": "failed",
        }.get(status, "queued")
        renderer_props = {
            "title": normalized_props.get("title") or normalized_props.get("alt") or "图片",
            "alt": normalized_props.get("alt") or "image",
            "status": frontend_status,
        }
        src = normalized_props.get("src")
        if isinstance(src, str) and src.strip():
            renderer_props["src"] = src.strip()
        aspect = normalized_props.get("aspect_ratio")
        if isinstance(aspect, str) and aspect.strip():
            renderer_props["aspect_ratio"] = aspect.strip()[:32]
    else:
        renderer_props = dict(normalized_props)

    data = {
        "component_type": component_type,
        "component_id": component_id or f"{component_type}_{uuid4().hex[:10]}",
        "schema_version": schema_version[:32],
        "props": renderer_props,
        "allowed_events": cleaned_events[:10],
    }
    title = (
        renderer_props.get("title")
        or renderer_props.get("location")
        or component_type
    )
    return {
        "type": "component",
        "status": "completed",
        "content": str(title),
        "data": data,
    }


def build_magic_card_part(
    *,
    title: str,
    fallback_text: str | None = None,
    card_id: str | None = None,
    version: int = 1,
    preferred_height: int | None = None,
    preview_html: str | None = None,
    artifact_url: str | None = None,
    origin_verified: bool = False,
    scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cleaned_title = " ".join((title or "").split())[:200] or "交互卡片"
    instance_id = f"card_inst_{uuid4().hex[:12]}"
    card_key = card_id or f"card_{uuid4().hex[:10]}"
    runtime_ready = bool(
        origin_verified
        and isinstance(artifact_url, str)
        and artifact_url.startswith("https://")
    )
    cleaned_preview = preview_html.strip() if isinstance(preview_html, str) else ""
    if not runtime_ready and not cleaned_preview:
        raise AppError(
            422,
            "magic_card_preview_required",
            "preview_html must contain a complete self-contained HTML preview",
        )
    if len(cleaned_preview) > MAX_MAGIC_CARD_PREVIEW_CHARS:
        raise AppError(
            422,
            "magic_card_preview_too_large",
            f"preview_html must not exceed {MAX_MAGIC_CARD_PREVIEW_CHARS} characters",
            {"max_chars": MAX_MAGIC_CARD_PREVIEW_CHARS},
        )

    inline_ready = bool(cleaned_preview)
    data: dict[str, Any] = {
        "card_instance_id": instance_id,
        "card_id": card_key,
        "version": max(1, int(version or 1)),
        "runtime": "react-sandbox-v1" if runtime_ready else "html-srcdoc-sandbox-v1",
        "title": cleaned_title,
        "fallback_text": (fallback_text or cleaned_title)[:500],
        "status": "ready",
        "origin_verified": runtime_ready,
        "viewport": {
            "mode": "inline",
            "preferred_height": preferred_height or 360,
            "max_height": 720,
        },
        "scope": scope or {},
        "reason": None,
    }
    if preferred_height is not None:
        data["preferred_height"] = max(120, min(900, int(preferred_height)))
    if inline_ready:
        # Dynamic preview HTML runs in an opaque-origin iframe. The host replaces
        # its CSP and blocks network, frames, forms, and host-DOM access.
        data["preview_html"] = cleaned_preview
    if runtime_ready:
        data["artifact_url"] = artifact_url
    return {
        "type": "magic_card",
        "status": "completed",
        "content": cleaned_title,
        "data": data,
    }
