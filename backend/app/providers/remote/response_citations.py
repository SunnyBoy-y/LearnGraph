"""Collect Responses URL annotations without treating search hits as citations."""
from __future__ import annotations

from typing import Any

from app.services.web_evidence import public_source_url


class ResponseCitations:
    def __init__(self) -> None:
        self._text_runs: list[tuple[tuple[int, int], list[str]]] = []
        self._annotations: dict[tuple[int, int], list[dict[str, Any]]] = {}
        self._snapshots: dict[tuple[int, int], str] = {}

    @staticmethod
    def _index(value: Any) -> int | None:
        if type(value) is int and value >= 0:
            return value
        return None

    def _annotation(self, key: tuple[int, int], value: Any) -> None:
        if not isinstance(value, dict) or value.get("type") not in ("url_citation", "citation", "", None):
            return
        nested = value.get("url_citation")
        if isinstance(nested, dict):
            value = {**value, **nested}
        url = public_source_url(value.get("url"))
        if not url:
            return
        self._annotations.setdefault(key, []).append({
            "url": url,
            "title": str(value.get("title") or url)[:1000],
            "start_index": self._index(value.get("start_index")),
            "end_index": self._index(value.get("end_index")),
        })

    def _content(self, key: tuple[int, int], part: Any) -> None:
        if not isinstance(part, dict) or part.get("type") != "output_text":
            return
        text = part.get("text")
        if isinstance(text, str):
            self._snapshots[key] = text
        annotations = part.get("annotations")
        if isinstance(annotations, list):
            for value in annotations:
                self._annotation(key, value)

    def _item(self, output: int, item: Any) -> None:
        if not isinstance(item, dict) or item.get("type") != "message":
            return
        content = item.get("content")
        if isinstance(content, list):
            for part_index, part in enumerate(content):
                self._content((output, part_index), part)

    def observe(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if not isinstance(kind, str):
            return
        key = (self._index(event.get("output_index")) or 0, self._index(event.get("content_index")) or 0)
        if kind in {"response.output_text.delta", "response.refusal.delta"}:
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                if not self._text_runs or self._text_runs[-1][0] != key:
                    self._text_runs.append((key, []))
                self._text_runs[-1][1].append(delta)
        elif kind == "response.output_text.annotation.added":
            self._annotation(key, event.get("annotation"))
        elif kind == "response.content_part.done":
            self._content(key, event.get("part"))
        elif kind == "response.output_item.done":
            self._item(key[0], event.get("item"))
        elif kind == "response.completed":
            response = event.get("response")
            if isinstance(response, dict) and isinstance(response.get("output"), list):
                for item_index, item in enumerate(response["output"]):
                    self._item(item_index, item)

    def collect(self) -> tuple[str, list[dict[str, Any]]]:
        # Offsets are local to an output content part, not to the combined text.
        spans: dict[tuple[int, int], tuple[int, str] | None] = {}
        chunks: list[str] = []
        offset = 0
        for key, deltas in self._text_runs:
            text = "".join(deltas)
            spans[key] = None if key in spans else (offset, text)
            offset += len(text)
            chunks.append(text)

        sources: dict[str, dict[str, Any]] = {}
        for key, annotations in self._annotations.items():
            span = spans.get(key)
            for annotation in annotations:
                url, title = annotation["url"], annotation["title"]
                entry = sources.setdefault(url, {
                    "url": url, "title": title, "index": len(sources) + 1, "end_indexes": [],
                })
                if title != url:
                    entry["title"] = title
                if span is None:
                    continue
                base, text = span
                if key in self._snapshots and self._snapshots[key] != text:
                    continue
                start, end = annotation["start_index"], annotation["end_index"]
                if end is None or end > len(text) or (start is not None and start > end):
                    continue
                end += base
                if end not in entry["end_indexes"]:
                    entry["end_indexes"].append(end)
        return "".join(chunks), list(sources.values())
