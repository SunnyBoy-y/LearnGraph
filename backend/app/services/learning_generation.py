"""教学包生成层：分类重试、确定性归一化与阶段内并发。

Why this module exists
----------------------
The learning-package pipeline used to hand the model the *authored* JSON schema
and treat every failure the same way: one blind retry, then a stage failure with
"请检查模型配置后重试本阶段".  Two facts made that misleading and expensive:

1. ``ProviderResponseError`` (JSON truncation, extra keys, wrong scalar types)
   is a ``RuntimeError``, so the old ``except (ValueError, ET.ParseError)`` never
   caught it — the stage died on the first response with **zero retries**;
2. cross-field rules that JSON Schema cannot express (试卷总分必须 = 100,
   ``practical_points`` 必须与是否含实验一致) were left to a single repair call,
   with no deterministic fallback.

So the contract here is: **classify → normalize deterministically → retry only
what a retry can fix → never let one stage's text-shape problem fail a build that
could have been repaired locally.**

Everything in this module is provider-agnostic and session-disciplined: the
concurrent path gives every generation its own short-lived session, because no
transaction may span a model call and the SQLite write gate only serializes
short writes.
"""

from __future__ import annotations

import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from random import uniform
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.errors import AppError
from app.providers.model_options import resolve_model_call_options

PayloadT = TypeVar("PayloadT")

# Error categories. Only the first three are worth another model call.
CATEGORY_STRUCTURAL = "structural"
CATEGORY_TRANSIENT = "transient"
CATEGORY_VALIDATION = "validation"
CATEGORY_FATAL = "fatal"

RETRYABLE_CATEGORIES = frozenset({CATEGORY_STRUCTURAL, CATEGORY_TRANSIENT, CATEGORY_VALIDATION})

# AppError codes that a retry cannot fix: the fix is configuration or budget.
FATAL_APP_CODES = frozenset({
    "learning_model_unavailable",
    "usage_price_required",
    "usage_budget_exceeded",
    "usage_budget_required",
})

TRANSIENT_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524, 529})


class GenerationFailure(RuntimeError):
    """A generation stage gave up after bounded attempts.

    Carries the classification (and how many model calls were already spent) so
    the stage can report something actionable instead of blaming the model
    configuration. ``label`` is the *item* that failed (试卷 / 教材 / 小剧场…),
    which matters now that two items share one stage.
    """

    def __init__(self, category: str, attempts: int, detail: str, errors: list[tuple[str, str]] | None = None, label: str = "") -> None:
        super().__init__(detail)
        self.category = category
        self.attempts = attempts
        self.detail = detail
        self.errors = list(errors or [])
        self.label = label


def provider_error_types() -> tuple[type[BaseException], ...]:
    """Import the provider error family lazily (keeps provider deps off import)."""
    from app.providers.remote.openai import ProviderHTTPError, ProviderResponseError, ProviderTimeoutError

    return ProviderHTTPError, ProviderResponseError, ProviderTimeoutError


def is_output_cap_rejection(exc: BaseException) -> bool:
    """Whether upstream refused the output ceiling we declared.

    Gateways answer 400/413/422 when ``max_tokens`` exceeds what the model can
    actually produce. That is a configuration mismatch, not a broken model: the
    retry must drop the ceiling instead of blaming the model configuration.
    """

    ProviderHTTPError, _, _ = provider_error_types()
    if not isinstance(exc, ProviderHTTPError):
        return False
    if getattr(exc, "status_code", None) not in {400, 413, 422}:
        return False
    text = str(exc).lower()
    return any(
        token in text
        for token in ("max_tokens", "max_completion_tokens", "max_output_tokens")
    )


def classify_generation_error(exc: BaseException) -> str:
    """Map a generation failure onto the retry policy.

    ``structural`` means the model answered, but the text/shape was rejected —
    the single most common failure in this pipeline and always worth a retry.
    """
    if isinstance(exc, GenerationFailure):
        return exc.category
    if isinstance(exc, AppError):
        return CATEGORY_FATAL if exc.code in FATAL_APP_CODES else CATEGORY_VALIDATION
    ProviderHTTPError, ProviderResponseError, ProviderTimeoutError = provider_error_types()
    if isinstance(exc, ValidationError):
        return CATEGORY_VALIDATION
    if isinstance(exc, ProviderTimeoutError) or isinstance(exc, TimeoutError):
        return CATEGORY_TRANSIENT
    if isinstance(exc, ProviderResponseError):
        # A broken *configured schema* is a code/config defect, not the model's fault.
        if "schema is invalid" in str(exc):
            return CATEGORY_FATAL
        return CATEGORY_STRUCTURAL
    if isinstance(exc, ProviderHTTPError):
        if is_output_cap_rejection(exc):
            # Retryable: the next attempt simply will not declare a ceiling.
            return CATEGORY_STRUCTURAL
        code = getattr(exc, "status_code", None)
        if code is None or code in TRANSIENT_STATUS_CODES:
            return CATEGORY_TRANSIENT
        return CATEGORY_FATAL
    # JSON/text-level problems surface as ValueError from the callers' parsers.
    if isinstance(exc, ValueError):
        return CATEGORY_VALIDATION
    return CATEGORY_FATAL


def safe_failure_detail(exc: BaseException, *, limit: int = 240) -> str:
    """A diagnosable detail string that never carries model output or answer keys.

    Pydantic embeds the offending *value* in some messages, and the exam payload
    holds private answer keys, so only locations and rule texts are kept.
    """
    if isinstance(exc, GenerationFailure):
        return exc.detail[:limit]
    if isinstance(exc, ValidationError):
        parts = []
        for item in exc.errors()[:3]:
            location = ".".join(str(part) for part in item.get("loc") or ()) or "$"
            parts.append(f"{location}: {item.get('msg')}")
        return "; ".join(parts)[:limit] or "invalid structured output"
    text = str(exc) or type(exc).__name__
    text = re.sub(r"\s*(?:input_value|input|body|request_id)\s*[=:].*", "", text, flags=re.I).strip()
    return text[:limit] or type(exc).__name__


def repair_instruction(
    category: str,
    detail: str,
    *,
    schema: type[BaseModel] | None = None,
    label: str = "",
) -> str:
    """A concrete repair, not "please try again"."""
    schema_name = getattr(schema, "__name__", "")
    if schema_name == "Activity":
        contract_hint = (
            "\n实验引用必须严格闭合：requires/effects/success 中的 variable 必须逐字复制 variables[].id；"
            "solution 中每一项必须逐字复制 actions[].id（动作可重复）。不要使用 label、中文名称、别名，"
            "也不要引用未声明的变量或动作。"
        )
    elif schema_name == "Exam":
        contract_hint = (
            "\n各题 points 与 practical_points 之和必须恰好等于 100；选择题 answers 使用从 0 开始的"
            "索引字符串。"
        )
    else:
        contract_hint = ""
    if category == CATEGORY_STRUCTURAL:
        return (
            "\n上一次响应的 JSON 结构不符合契约：" + detail +
            "\n请只输出一个 JSON 对象（不要 markdown 代码块、不要解释文字），字段名与类型必须与 schema 完全一致，"
            "不要增加 schema 之外的键；数组元素必须是 schema 声明的类型。" + contract_hint
        )
    if category == CATEGORY_TRANSIENT:
        return ""
    return (
        "\n上一次输出未通过业务校验：" + detail +
        "\n请修正该问题后重新输出完整 JSON。" + contract_hint
    )


# --------------------------------------------------------------------------- #
# Deterministic normalization (cheap fixes before spending another model call)
# --------------------------------------------------------------------------- #

def _project(model_cls: type[BaseModel], value: Any) -> Any:
    """Drop keys the closed schema does not declare, recursively.

    ``extra="forbid"`` means one stray key ("difficulty", "tags") would otherwise
    reject a perfectly usable payload; the pipeline only ever reads declared
    fields, so projecting is lossless for us.
    """
    if not isinstance(value, dict):
        return value
    projected: dict[str, Any] = {}
    for name, field in model_cls.model_fields.items():
        if name not in value:
            continue
        projected[name] = _project_field(field.annotation, value[name])
    return projected


def _project_field(annotation: Any, value: Any) -> Any:
    item_cls = _model_in_annotation(annotation)
    if item_cls is None:
        return value
    if isinstance(value, list):
        return [_project(item_cls, item) for item in value]
    if isinstance(value, dict):
        return _project(item_cls, value)
    return value


@lru_cache(maxsize=64)
def _model_in_annotation(annotation: Any) -> type[BaseModel] | None:
    from typing import get_args

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in get_args(annotation):
        found = _model_in_annotation(arg)
        if found is not None:
            return found
    return None


def normalize_payload(model_cls: type[BaseModel], raw: Any) -> Any:
    return _project(model_cls, raw)


def _reference_key(value: Any) -> str:
    """Normalize an authored identifier/label for exact alias matching.

    This deliberately is not fuzzy matching. It only ignores Unicode width,
    case, whitespace and common ID separators, so a repair cannot silently
    redirect an activity to a semantically different variable or action.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _reference_index(items: list[dict[str, Any]], original_ids: list[str]) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for item, original_id in zip(items, original_ids):
        canonical = str(item.get("id") or "").strip()
        for alias in (canonical, original_id, item.get("label")):
            key = _reference_key(alias)
            if key:
                candidates.setdefault(key, set()).add(canonical)
    return {key: next(iter(values)) for key, values in candidates.items() if len(values) == 1}


def _repair_declared_ids(
    items: list[dict[str, Any]],
    *,
    prefix: str,
    pattern: re.Pattern[str],
    max_length: int,
    separator: str,
) -> list[str]:
    """Repair malformed declared IDs while preserving every already-valid ID.

    Valid duplicates remain duplicates and therefore still fail validation: an
    ambiguous authored contract must be regenerated, not guessed locally.
    """
    original_ids = [str(item.get("id") or "").strip() for item in items]
    reserved = {identifier for identifier in original_ids if pattern.fullmatch(identifier)}
    generated: set[str] = set()
    for index, (item, original_id) in enumerate(zip(items, original_ids), start=1):
        if pattern.fullmatch(original_id):
            continue
        base = unicodedata.normalize("NFKC", original_id).strip().lower()
        base = re.sub(r"[^a-z0-9_-]+", separator, base)
        base = re.sub(r"[_-]+", separator, base).strip("_-")
        if not base or not base[0].isalpha():
            base = f"{prefix}{index}"
        candidate = base[:max_length]
        suffix = 2
        while candidate in reserved or candidate in generated:
            tail = f"{separator}{suffix}"
            candidate = f"{base[:max_length - len(tail)]}{tail}"
            suffix += 1
        item["id"] = candidate
        generated.add(candidate)
    return original_ids


def normalize_activity(raw: Any) -> Any:
    """Repair lossless activity ID/reference deviations before another call.

    Models commonly declare ``sun_points`` and later refer to ``Sun-Points`` or
    the display label ``阳光值``. Those payloads describe the same state machine,
    but the closed contract correctly rejects them. This normalizer aligns only
    unique, exact aliases and leaves genuinely unknown/ambiguous references in
    place so the model receives a targeted repair request instead of us
    inventing game logic.
    """
    from app.domain.schemas.learning_packages import Activity

    projected = _project(Activity, raw)
    if not isinstance(projected, dict):
        return projected
    variables = projected.get("variables")
    actions = projected.get("actions")
    if not isinstance(variables, list) or not isinstance(actions, list):
        return projected
    if not all(isinstance(item, dict) for item in [*variables, *actions]):
        return projected

    variable_original_ids = _repair_declared_ids(
        variables,
        prefix="v",
        pattern=re.compile(r"^[a-z][a-z0-9_]{0,30}$"),
        max_length=31,
        separator="_",
    )
    action_original_ids = _repair_declared_ids(
        actions,
        prefix="a",
        pattern=re.compile(r"^[a-z][a-z0-9_-]{0,39}$"),
        max_length=40,
        separator="_",
    )
    variable_index = _reference_index(variables, variable_original_ids)
    action_index = _reference_index(actions, action_original_ids)

    def resolve(value: Any, index: dict[str, str]) -> Any:
        return index.get(_reference_key(value), value)

    for action in actions:
        for condition in action.get("requires") or []:
            if isinstance(condition, dict) and "variable" in condition:
                condition["variable"] = resolve(condition["variable"], variable_index)
        for effect in action.get("effects") or []:
            if isinstance(effect, dict) and "variable" in effect:
                effect["variable"] = resolve(effect["variable"], variable_index)
    for condition in projected.get("success") or []:
        if isinstance(condition, dict) and "variable" in condition:
            condition["variable"] = resolve(condition["variable"], variable_index)
    if isinstance(projected.get("solution"), list):
        projected["solution"] = [resolve(action_id, action_index) for action_id in projected["solution"]]
    return projected


CHOICE_KINDS = frozenset({"single_choice", "multiple_choice", "true_false"})
TRUE_FALSE_TOKENS = frozenset({
    "对", "错", "正确", "错误", "是", "否", "真", "假", "true", "false", "yes", "no",
})
EXAM_QUESTION_KEYS = ("id", "kind", "prompt", "options", "points", "answers", "rubric", "explanation", "critical")
EXAM_QUESTION_KINDS = frozenset({"single_choice", "multiple_choice", "true_false", "fill_blank", "short_answer", "essay"})
MIN_USABLE_QUESTIONS = 3
DEFAULT_RUBRIC = "按参考答案要点给分。"
DEFAULT_EXPLANATION = "参考教材相关章节。"


def normalize_exam(raw: Any, *, has_activity: bool) -> Any:
    """Repair an almost-valid exam instead of failing the build.

    Only lossless or arithmetic repairs happen here. Answer keys are never
    invented: a question whose key is unusable is dropped, and if fewer than
    ``MIN_USABLE_QUESTIONS`` survive the payload is passed through unchanged so
    the model gets asked again with the concrete rule that failed.
    """
    if not isinstance(raw, dict):
        return raw
    questions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw.get("questions") or []):
        question = _normalize_question(item, index, seen_ids)
        if question is not None:
            questions.append(question)
    if len(questions) < MIN_USABLE_QUESTIONS:
        return raw

    # 实践分与"是否有实验"必须一致；这也决定笔试部分的总分。
    practical = 30 if has_activity else 0
    exam_total = 100 - practical
    points = _distribute_points([q["points"] for q in questions], exam_total)
    for question, value in zip(questions, points):
        question["points"] = value

    pass_score = raw.get("pass_score", 80)
    try:
        pass_score = int(pass_score)
    except (TypeError, ValueError):
        pass_score = 80
    return {
        "title": str(raw.get("title") or "").strip()[:200] or "节点测评",
        "pass_score": min(100, max(50, pass_score)),
        "practical_points": practical,
        "questions": questions,
    }


def _normalize_question(item: Any, index: int, seen_ids: set[str]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    kind = str(item.get("kind") or "").strip().lower()
    if kind not in EXAM_QUESTION_KINDS:
        return None
    prompt = str(item.get("prompt") or "").strip()
    if len(prompt) < 3:
        return None
    options = [str(option).strip() for option in (item.get("options") or []) if str(option).strip()][:8]
    answers = _normalize_answers(item.get("answers"))

    if kind in CHOICE_KINDS:
        if kind == "true_false":
            options = _true_false_options(options)
            if options is None:
                return None
        if len(options) < 2:
            return None
        valid = {str(position) for position in range(len(options))}
        answers = [answer for answer in answers if answer in valid]
        if kind != "multiple_choice":
            # 单选题/判断题的答案只能有一个；多写或缺失都要重问，绝不替模型挑答案。
            if len(answers) != 1:
                return None
        elif not answers:
            return None
    else:
        # 书写题不允许选项（契约如此），选项内容一律丢弃。
        options = []
        if not answers:
            return None

    identifier = _unique_id(str(item.get("id") or "").strip(), index, seen_ids)
    points = item.get("points", 0)
    try:
        points = int(points)
    except (TypeError, ValueError):
        points = 1
    return {
        "id": identifier,
        "kind": kind,
        "prompt": prompt[:4000],
        "options": options,
        "points": min(100, max(1, points)),
        "answers": list(dict.fromkeys(answers))[:12],
        "rubric": str(item.get("rubric") or "").strip()[:3000] or DEFAULT_RUBRIC,
        "explanation": str(item.get("explanation") or "").strip()[:3000] or DEFAULT_EXPLANATION,
        "critical": bool(item.get("critical")),
    }


def _normalize_answers(value: Any) -> list[str]:
    """Numeric option indexes are the single most common model deviation."""
    if not isinstance(value, (list, tuple)):
        value = [value] if value is not None else []
    answers: list[str] = []
    for entry in value:
        if isinstance(entry, bool):
            continue
        if isinstance(entry, int):
            answers.append(str(entry))
            continue
        text = str(entry).strip()
        if not text:
            continue
        if re.fullmatch(r"\d+", text):
            answers.append(text)
        else:
            answers.append(text[:2000])
    return answers


def _true_false_options(options: list[str]) -> list[str] | None:
    """Keep a canonical 对/错 pair; never reorder a model's option list."""
    if len(options) == 2:
        return options
    if len(options) > 2 and options[0].lower() in TRUE_FALSE_TOKENS and options[1].lower() in TRUE_FALSE_TOKENS:
        return options[:2]
    return None


def _unique_id(candidate: str, index: int, seen_ids: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_-]", "", candidate.lower())
    if not base or not base[0].isalpha():
        base = f"q{index + 1}"
    identifier, suffix = base[:40], 2
    while identifier in seen_ids:
        identifier = f"{base[:36]}-{suffix}"
        suffix += 1
    seen_ids.add(identifier)
    return identifier


def _distribute_points(points: list[int], total: int) -> list[int]:
    """Re-scale question points so they sum to exactly ``total``.

    Largest-remainder first for the surplus, then the largest questions give
    points back for a deficit: deterministic, order-preserving and bounded.
    """
    count = len(points)
    if count == 0:
        return []
    budget = max(count, int(total))
    weights = [max(1, int(point)) for point in points]
    exact = [weight * budget / sum(weights) for weight in weights]
    result = [max(1, int(value)) for value in exact]
    order = sorted(range(count), key=lambda i: (-(exact[i] - int(exact[i])), -weights[i]))
    steps = 0
    while sum(result) < budget and steps < budget * count:
        result[order[steps % count]] += 1
        steps += 1
    desc = sorted(range(count), key=lambda i: -result[i])
    steps = 0
    while sum(result) > budget and steps < budget * count:
        target = desc[steps % count]
        if result[target] > 1:
            result[target] -= 1
        steps += 1
    return [min(100, value) for value in result]


# --------------------------------------------------------------------------- #
# Bounded retries + stage-internal parallelism
# --------------------------------------------------------------------------- #

def _attempt_budget(attempts: int | None) -> int:
    if attempts is not None:
        return max(1, int(attempts))
    return max(1, int(getattr(get_settings(), "learning_generation_attempts", 3) or 3))


def _backoff_seconds(category: str, attempt: int) -> float:
    base = 2.0 if category == CATEGORY_TRANSIENT else 0.75
    return min(20.0, base * (2 ** (attempt - 1))) + uniform(0, 0.4)


def generate_raw(
    db: Session,
    workspace_id: str,
    actor_id: str,
    schema: type[BaseModel],
    prompt: str,
    *,
    drop_output_cap: bool = False,
    notes: list[str] | None = None,
) -> Any:
    """One provider call plus its billing rows; returns the *unvalidated* payload.

    The model comes from the workspace's 「功能模型 → 教学包生成模型」 setting and
    falls back to the workspace chat model when that is unset, so an operator can
    point long structured generation at a stronger (or larger-output) model
    without changing the conversational default.
    """
    from app.domain.settings import LEARNING_PACKAGE_MODEL_SETTING_KEY
    from app.providers.factory import feature_model_target, model_provider_for_workspace
    from app.services.billing import BillingService

    provider = model_provider_for_workspace(
        db,
        workspace_id,
        get_settings(),
        **feature_model_target(db, workspace_id, LEARNING_PACKAGE_MODEL_SETTING_KEY),
    )
    if not getattr(provider, "available", True):
        raise AppError(503, "learning_model_unavailable", "请在设置中配置可用的结构化生成模型。")
    _force_fast_structured_options(provider)
    if drop_output_cap:
        # Upstream refused our declared ceiling. Do not fail the stage over a
        # configuration mismatch we can side-step.
        setattr(provider, "output_budget_disabled", True)
    billing = BillingService(db, workspace_id, actor_id)
    quote = billing.preflight_model_call(provider_id=provider.provider_id, model_id=provider.model_id,
        feature="learning_package", estimated_input_tokens=max(1, len(prompt) // 2),
        estimated_output_tokens=8000, remote_capability=provider.remote_capability)
    db.commit()
    result = provider.generate_json(prompt, schema.__name__, schema.model_json_schema())
    usage = dict(getattr(provider, "last_usage", {}) or {})
    billing.record_usage(quote, input_tokens=int(usage.get("input_tokens") or 0), output_tokens=int(usage.get("output_tokens") or 0), attempt=1, usage_reported=bool(usage))
    db.commit()
    return result


def _force_fast_structured_options(provider: Any) -> None:
    """Disable chat-only reasoning/search overhead for package JSON calls.

    Learning stages already have a closed schema plus deterministic validators;
    carrying the workspace chat mode (for example ``medium`` thinking) into
    every background call adds latency without improving the contract. Resolve
    the provider's own capability mapping so each dialect receives the correct
    off switch. If a legacy/custom provider cannot expose its capabilities,
    leave its existing options untouched rather than breaking generation.
    """
    capabilities = getattr(provider, "capabilities", None)
    model_id = str(getattr(provider, "model_id", "") or "").strip()
    if not isinstance(capabilities, dict) or not model_id:
        return
    try:
        provider.call_options = resolve_model_call_options(
            capabilities,
            model_id,
            thinking_mode="off",
            search_route="disabled",
            disable_thinking_fallback=True,
        )
    except Exception:
        # Capability snapshots from older/custom adapters may not contain the
        # dialect metadata needed to express "off". Keep their original mode.
        return


def generate_checked(
    workspace_id: str,
    actor_id: str,
    schema: type[BaseModel],
    prompt: str,
    validator: Callable[[Any], None] | None = None,
    normalize: Callable[[Any], Any] | None = None,
    *,
    attempts: int | None = None,
    label: str = "",
    notes: list[str] | None = None,
) -> Any:
    """Generate, repair locally, and retry only what a retry can fix.

    Order per attempt: provider call → deterministic normalization → schema
    validation → caller's semantic validator. Each failure is classified; the
    next attempt carries a concrete repair instruction instead of a generic
    "please try again". ``notes`` collects operator-facing, safe observations
    (for example "the ceiling was refused and dropped").
    """
    budget = _attempt_budget(attempts)
    failures: list[tuple[str, str]] = []
    drop_output_cap = False
    for attempt in range(1, budget + 1):
        try:
            with SessionLocal() as db:
                raw = generate_raw(db, workspace_id, actor_id, schema, prompt, drop_output_cap=drop_output_cap, notes=notes)
            candidate = normalize(raw) if normalize is not None else raw
            value = schema.model_validate(candidate).model_dump()
            if validator is not None:
                validator(value)
            return value
        except Exception as exc:  # noqa: BLE001 - classification decides the policy
            category = classify_generation_error(exc)
            detail = safe_failure_detail(exc)
            failures.append((category, detail))
            cap_rejected = not drop_output_cap and is_output_cap_rejection(exc)
            if cap_rejected:
                drop_output_cap = True
                if notes is not None:
                    notes.append("模型拒绝了声明的输出上限，已改用不指定上限的方式重试。")
            if category == CATEGORY_FATAL or attempt >= budget:
                prefix = f"{label}：" if label else ""
                suffix = "（已在不指定输出上限的情况下重试过）" if drop_output_cap else ""
                raise GenerationFailure(category, attempt, prefix + suffix + detail, failures, label=label) from exc
            if not cap_rejected:
                prompt = prompt + repair_instruction(category, detail, schema=schema, label=label)
            time.sleep(_backoff_seconds(category, attempt))
    raise GenerationFailure(CATEGORY_FATAL, budget, "generation attempts exhausted", failures)  # pragma: no cover


def run_parallel_generations(
    tasks: list[tuple[str, Callable[[], Any]]],
    *,
    max_workers: int | None = None,
    on_result: Callable[[str, Any, BaseException | None], None] | None = None,
) -> dict[str, tuple[Any, BaseException | None]]:
    """Run independent generations concurrently; each task owns its DB session.

    Only *model calls* are concurrent: every callable opens a short-lived session
    through :func:`generate_checked`, so no transaction spans inference and the
    SQLite write gate keeps serializing the short billing writes. ``on_result``
    runs on the collector thread as each future completes, allowing callers to
    checkpoint successful siblings without waiting for the slowest generation.
    """
    workers = max_workers if max_workers is not None else int(getattr(get_settings(), "learning_generation_parallel", 2) or 2)
    results: dict[str, tuple[Any, BaseException | None]] = {}
    if len(tasks) <= 1 or max(1, workers) <= 1:
        for name, task in tasks:
            try:
                results[name] = (task(), None)
            except Exception as exc:  # noqa: BLE001 - caller decides per-task policy
                results[name] = (None, exc)
            if on_result is not None:
                on_result(name, results[name][0], results[name][1])
        return results
    with ThreadPoolExecutor(max_workers=min(max(1, workers), len(tasks)), thread_name_prefix="learning-gen") as pool:
        futures = {name: pool.submit(task) for name, task in tasks}
        names = {future: name for name, future in futures.items()}
        for future in as_completed(futures.values()):
            name = names[future]
            try:
                results[name] = (future.result(), None)
            except Exception as exc:  # noqa: BLE001 - caller decides per-task policy
                results[name] = (None, exc)
            if on_result is not None:
                on_result(name, results[name][0], results[name][1])
    return results
