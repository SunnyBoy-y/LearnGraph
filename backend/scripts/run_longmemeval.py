#!/usr/bin/env python3
"""Run an isolated LongMemEval-S benchmark against a LearnGraph instance.

The runner uses one freshly bootstrapped workspace per question.  ``--dry-run``
only validates and summarizes the dataset and never needs a provider key.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

API_KEY_ENV = "LEARNGRAPH_MEMORY_EVAL_DEEPSEEK_API_KEY"
DEFAULT_DATASET = "data/benchmarks/longmemeval/longmemeval_s_cleaned.json"
DEFAULT_OUTPUT = "data/benchmarks/longmemeval/results"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        return "unknown"


def _load_dataset(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("LongMemEval dataset must be a JSON array")
    for index, item in enumerate(data):
        if not isinstance(item, dict) or not item.get("question_id"):
            raise ValueError(f"dataset item {index} is missing question_id")
        if not isinstance(item.get("haystack_sessions"), list):
            raise ValueError(f"dataset item {index} is missing haystack_sessions")
    return data


def _question_date(item: dict[str, Any], index: int) -> str:
    dates = item.get("haystack_dates") or []
    return str(dates[index] if index < len(dates) else item.get("question_date") or "1970-01-01")


def _session_id(item: dict[str, Any], index: int) -> str:
    ids = item.get("haystack_session_ids") or []
    return str(ids[index] if index < len(ids) else f"session-{index}")


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _answer_hit(actual: str, expected: str) -> bool:
    a, e = _norm(actual), _norm(expected)
    return bool(a and e and (e in a or a in e))


_ABSTENTION_MARKERS = (
    "i don't know",
    "i cannot answer",
    "i can't answer",
    "cannot determine",
    "can't determine",
    "no information",
    "无法回答",
    "无法确定",
    "没有相关信息",
    "我不知道",
)


def _is_abstention(value: str) -> bool:
    normalized = _norm(value)
    return bool(normalized) and any(marker in normalized for marker in _ABSTENTION_MARKERS)


def _safe_trace(value: Any) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key).casefold()
            output[str(key)] = "[REDACTED]" if any(
                marker in key_text for marker in ("key", "token", "secret", "authorization", "password")
            ) else _safe_trace(child)
        return output
    if isinstance(value, list):
        return [_safe_trace(item) for item in value]
    if isinstance(value, str) and ("sk-" in value or "bearer " in value.casefold()):
        return "[REDACTED]"
    return value


def _safe_error(value: str, secret: str) -> str:
    text = str(value or "")
    return text.replace(secret, "[REDACTED]") if secret else text


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run isolated LongMemEval-S against LearnGraph")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--question-id")
    parser.add_argument("--question-type")
    parser.add_argument("--base-url", default="http://localhost:8080/v1")
    parser.add_argument(
        "--api-url",
        default=os.environ.get(
            "LEARNGRAPH_MEMORY_EVAL_API_URL",
            "http://127.0.0.1:18000/api/v1",
        ),
        help="LearnGraph API root; --base-url remains the OpenAI-compatible model URL",
    )
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--thinking-mode", default="low")
    parser.add_argument(
        "--ingestion-mode",
        choices=("evidence", "product_memory"),
        default="evidence",
        help="Use benchmark evidence ingestion or the real product memory intent path for user turns.",
    )
    parser.add_argument("--judge-model")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _select_questions(data: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    selected = data
    if args.question_id:
        selected = [item for item in selected if str(item.get("question_id")) == args.question_id]
    if args.question_type:
        selected = [item for item in selected if str(item.get("question_type")) == args.question_type]
    return selected[: max(0, args.limit)]


def _existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
            if item.get("question_id"):
                ids.add(str(item["question_id"]))
        except json.JSONDecodeError:
            continue
    return ids


def _post(
    client: Any,
    url: str,
    payload: dict[str, Any],
    *,
    stage: str,
) -> dict[str, Any]:
    response = client.post(url, json=payload)
    if response.status_code >= 400:
        try:
            detail: Any = response.json()
        except Exception:
            detail = response.text[:2000]
        raise RuntimeError(
            f"stage={stage}; LearnGraph request failed with HTTP "
            f"{response.status_code}; detail={json.dumps(detail, ensure_ascii=False)[:2000]}"
        )
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("LearnGraph returned a non-object response")
    return value


def _metric_row(
    item: dict[str, Any],
    response: dict[str, Any],
    elapsed_ms: int,
    top_k: int,
    judge: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected_sessions = [str(value) for value in item.get("answer_session_ids") or []]
    retrieved = response.get("retrieved_memories") or []
    retrieved_sessions = [
        str((entry.get("external_source") or {}).get("external_session_id") or "")
        for entry in retrieved
    ]
    retrieved_sessions = [value for value in retrieved_sessions if value]
    memory_context = (response.get("provider_trace") or {}).get("memory_context") or {}
    injected_sources = memory_context.get("injected_external_sources") or []
    injected_sessions = [
        str(source.get("external_session_id") or "")
        for source in injected_sources
        if isinstance(source, dict) and source.get("external_session_id")
    ]
    first_rank = next((index + 1 for index, value in enumerate(retrieved_sessions) if value in expected_sessions), None)
    expected_has_answer = bool(item.get("has_answer", expected_sessions))
    actual_answer = str(response.get("answer") or "")
    predicted_has_answer = bool(actual_answer.strip()) and not _is_abstention(actual_answer)
    if not expected_has_answer:
        answer_correct = True
    elif judge is not None and judge.get("correct") is not None:
        answer_correct = bool(judge.get("correct"))
    else:
        answer_correct = _answer_hit(actual_answer, str(item.get("answer") or ""))
    return {
        "question_id": item.get("question_id"),
        "question_type": item.get("question_type"),
        "workspace_id": response.get("workspace_id"),
        "question": item.get("question"),
        "expected_answer": item.get("answer"),
        "actual_answer": actual_answer,
        "answer_session_ids": expected_sessions,
        "retrieved_external_session_ids": retrieved_sessions,
        "injected_external_session_ids": injected_sessions,
        "context_injection_verified": bool(
            memory_context.get("context_build_id")
            and memory_context.get("manifest_status") in {"ok", "truncated"}
        ),
        "memory_context": memory_context,
        "recall_at_1": bool(first_rank and first_rank <= 1),
        "recall_at_3": bool(first_rank and first_rank <= 3),
        "recall_at_5": bool(first_rank and first_rank <= 5),
        "recall_at_k": bool(first_rank and first_rank <= top_k),
        "mrr": (1.0 / first_rank if first_rank else 0.0),
        "evidence_session_hit": bool(first_rank),
        "has_answer_expected": expected_has_answer,
        "has_answer_predicted": predicted_has_answer,
        "abstention_correct": predicted_has_answer == expected_has_answer,
        "answer_correct": answer_correct,
        "judge": judge,
        "retrieval_excluded": response.get("retrieval_excluded") or {},
        "latency_ms": elapsed_ms,
        "token_usage": (response.get("provider_trace") or {}).get("usage"),
        "provider_trace": _safe_trace(response.get("provider_trace") or {}),
        "failure": response.get("failure"),
    }


def _judge_answer(
    client: Any,
    base_url: str,
    api_key: str,
    model: str,
    *,
    question: str,
    expected: str,
    actual: str,
) -> dict[str, Any]:
    prompt = (
        "Evaluate whether the actual answer answers the question correctly. "
        "Return JSON only: {\"correct\":true|false,\"reason\":\"short reason\"}.\n"
        f"Question: {question}\nExpected answer: {expected}\nActual answer: {actual}"
    )
    response = client.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a strict evaluation judge."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 200,
        },
    )
    response.raise_for_status()
    content = str(response.json()["choices"][0]["message"]["content"] or "")
    match = re.search(r"\{.*\}", content, flags=re.S)
    if not match:
        raise RuntimeError("judge returned no JSON object")
    parsed = json.loads(match.group(0))
    return {"correct": bool(parsed.get("correct")), "reason": str(parsed.get("reason") or "")}


def main() -> int:
    args = _parse_args()
    dataset = Path(args.dataset).resolve()
    if not dataset.exists():
        print(f"dataset not found: {dataset}", file=sys.stderr)
        return 2
    try:
        data = _load_dataset(dataset)
        selected = _select_questions(data, args)
    except Exception as exc:
        print(f"dataset validation failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps({"status": "ok", "count": len(selected), "question_ids": [q["question_id"] for q in selected]}, ensure_ascii=False))
        return 0
    try:
        import httpx
    except ImportError:
        print("httpx is required for smoke/full runs; install backend project dependencies", file=sys.stderr)
        return 2
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        print(f"{API_KEY_ENV} is required unless --dry-run is used", file=sys.stderr)
        return 2
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.jsonl"
    completed = _existing_ids(results_path) if args.resume else set()
    config = {
        "dataset_path": str(dataset),
        "dataset_sha256": _sha256(dataset),
        "git_commit": _git_commit(),
        "model": args.model,
        "base_url": args.base_url,
        "api_url": args.api_url,
        "thinking_mode": args.thinking_mode,
        "top_k": args.top_k,
        "min_score": 0.20,
        "embedding_enabled": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "code_version": "longmemeval-adapter-v1",
        "judge_model": args.judge_model,
        "ingestion_mode": args.ingestion_mode,
    }
    (output_dir / "run_config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    endpoint = args.api_url.rstrip("/") + "/memory-eval"
    client = httpx.Client(timeout=300.0)
    rows: list[dict[str, Any]] = []
    try:
        with results_path.open("a", encoding="utf-8") as output:
            for item in selected:
                question_id = str(item["question_id"])
                if question_id in completed:
                    continue
                started = time.perf_counter()
                workspace_id = None
                stage = "bootstrap"
                try:
                    boot = _post(client, endpoint + "/bootstrap", {
                        "base_url": args.base_url,
                        "model_id": args.model,
                        "api_key": api_key,
                    }, stage=stage)
                    workspace_id = str(boot["workspace_id"])
                    for index, session in enumerate(item["haystack_sessions"]):
                        stage = f"ingest[{index}]"
                        _post(client, endpoint + "/ingest-session?workspace_id=" + workspace_id, {
                            "benchmark": "longmemeval",
                            "ingestion_mode": args.ingestion_mode,
                            "question_id": question_id,
                            "session_id": _session_id(item, index),
                            "session_index": index,
                            "session_date": _question_date(item, index),
                            "timezone_name": "UTC",
                            "turns": session,
                        }, stage=stage)
                    stage = "ask"
                    response = _post(client, endpoint + "/ask", {
                        "workspace_id": workspace_id,
                        "question_id": question_id,
                        "content": item.get("question") or "",
                        "model_id": args.model,
                        "thinking_mode": args.thinking_mode,
                        "agent_mode": True,
                        "search_route": "disabled",
                        "include_retrieved_memories": True,
                        "top_k": args.top_k,
                    }, stage=stage)
                    judge = None
                    if args.judge_model:
                        stage = "judge"
                        judge = _judge_answer(
                            client,
                            args.base_url,
                            api_key,
                            args.judge_model,
                            question=str(item.get("question") or ""),
                            expected=str(item.get("answer") or ""),
                            actual=str(response.get("answer") or ""),
                        )
                    row = _metric_row(
                        item,
                        response,
                        int((time.perf_counter() - started) * 1000),
                        args.top_k,
                        judge,
                    )
                except Exception as exc:
                    row = {
                        "question_id": question_id,
                        "question_type": item.get("question_type"),
                        "workspace_id": locals().get("workspace_id"),
                        "question": item.get("question"),
                        "stage": stage,
                        "failure": {
                            "type": type(exc).__name__,
                            "message": _safe_error(str(exc), api_key),
                        },
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                    }
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
                output.flush()
                rows.append(row)
    finally:
        client.close()
    all_rows = []
    if results_path.exists():
        all_rows = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    valid = [row for row in all_rows if not row.get("failure")]
    def _usage_total(row: dict[str, Any]) -> int:
        usage = row.get("token_usage")
        if not isinstance(usage, dict):
            return 0
        for key in ("total_tokens", "total"):
            try:
                if usage.get(key) is not None:
                    return int(usage[key])
            except (TypeError, ValueError):
                pass
        return sum(
            int(usage.get(key) or 0)
            for key in ("prompt_tokens", "completion_tokens", "input_tokens", "output_tokens")
            if str(usage.get(key) or "").lstrip("-").isdigit()
        )

    exclusion_totals: dict[str, int] = defaultdict(int)
    for row in valid:
        for key, value in (row.get("retrieval_excluded") or {}).items():
            try:
                exclusion_totals[str(key)] += int(value)
            except (TypeError, ValueError):
                continue
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_rows:
        by_type[str(row.get("question_type") or "unknown")].append(row)
    summary = {
        "count": len(all_rows),
        "success_count": len(valid),
        "failure_count": len(all_rows) - len(valid),
        "average_latency_ms": (sum(int(row.get("latency_ms") or 0) for row in all_rows) / len(all_rows)) if all_rows else 0,
        "retrieval_recall_at_k": (sum(bool(row.get("recall_at_k")) for row in valid) / len(valid)) if valid else 0,
        "retrieval_recall_at_1": (sum(bool(row.get("recall_at_1")) for row in valid) / len(valid)) if valid else 0,
        "retrieval_recall_at_3": (sum(bool(row.get("recall_at_3")) for row in valid) / len(valid)) if valid else 0,
        "retrieval_recall_at_5": (sum(bool(row.get("recall_at_5")) for row in valid) / len(valid)) if valid else 0,
        "mrr": (sum(float(row.get("mrr") or 0) for row in valid) / len(valid)) if valid else 0,
        "answer_accuracy": (sum(bool(row.get("answer_correct")) for row in valid) / len(valid)) if valid else 0,
        "abstention_accuracy": (sum(bool(row.get("abstention_correct")) for row in valid) / len(valid)) if valid else 0,
        "context_injection_verified": (
            sum(bool(row.get("context_injection_verified")) for row in valid) / len(valid)
            if valid
            else 0
        ),
        "judge_accuracy": (
            sum(bool((row.get("judge") or {}).get("correct")) for row in valid if row.get("judge") is not None)
            / sum(1 for row in valid if row.get("judge") is not None)
            if any(row.get("judge") is not None for row in valid)
            else None
        ),
        "total_token_usage": sum(_usage_total(row) for row in valid),
        "retrieval_excluded_totals": dict(exclusion_totals),
        "by_question_type": {
            key: {
                "count": len(group),
                "failure_count": sum(bool(row.get("failure")) for row in group),
                "retrieval_recall_at_k": (sum(bool(row.get("recall_at_k")) for row in group) / len(group)) if group else 0,
                "answer_accuracy": (sum(bool(row.get("answer_correct")) for row in group) / len(group)) if group else 0,
                "average_latency_ms": sum(int(row.get("latency_ms") or 0) for row in group) / len(group),
            }
            for key, group in by_type.items()
        },
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if not rows or all(not row.get("failure") for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
