"""Incremental JSON input for UI previews; never execute an unfinished command."""
from __future__ import annotations

import copy
import json

from app.services.stream_ui import MAX_UI_BYTES


class UIArgumentStream:
    """A bounded JSON lexer. Only completed fields and inert text are exposed.

    Values remain attached to their path while containers are open. Escape and
    Unicode sequences are decoded only once complete, including surrogate pairs.
    The normal tool dispatcher still parses and validates the original full JSON.
    """

    def __init__(self):
        self.root = None
        self.stack = []
        self.complete = set()
        self.token = ""
        self.string = False
        self.key_string = False
        self.escape = False
        self.size = 0
        self.disabled = False
        self.last = None
        self.last_count = -1
        self.since_emit = 0

    def _path(self):
        if not self.stack:
            return ()
        frame = self.stack[-1]
        return frame["path"] + ((frame["key"] if isinstance(frame["value"], dict) else len(frame["value"])),)

    def _put(self, value):
        path = self._path()
        if not self.stack:
            if self.root is not None:
                raise ValueError("Multiple JSON roots")
            self.root = value
        else:
            frame = self.stack[-1]
            if frame["expect"] != "value":
                raise ValueError("Unexpected JSON value")
            if isinstance(frame["value"], dict):
                frame["value"][frame["key"]] = value
            else:
                frame["value"].append(value)
            frame["expect"] = "comma"
        return path

    def _string_value(self):
        # json.loads preserves isolated UTF-16 surrogates: withhold those until
        # the matching low surrogate arrives; never send invalid UTF-8 to SSE.
        value = json.loads('"' + self.token + '"')
        value.encode("utf-8")
        return value

    def feed(self, delta):
        if self.disabled:
            return None
        if not isinstance(delta, str):
            self.disabled = True
            return None
        try:
            self.size += len(delta.encode("utf-8"))
        except UnicodeError:
            self.disabled = True
            return None
        self.since_emit += len(delta)
        if self.size > MAX_UI_BYTES:
            self.disabled = True
            return None
        try:
            for char in delta:
                if self.string:
                    if char == '"' and not self.escape:
                        value = self._string_value()
                        self.string = False
                        if self.key_string:
                            frame = self.stack[-1]
                            if value in frame["value"]:
                                raise ValueError("Duplicate JSON key")
                            frame.update(key=value, expect="colon")
                        else:
                            self.complete.add(self._put(value))
                        self.token = ""
                    else:
                        self.token += char
                        self.escape = char == "\\" and not self.escape
                    continue
                if self.token:
                    if char not in " \r\n\t,]}":
                        self.token += char
                        continue
                    self.complete.add(self._put(json.loads(self.token)))
                    self.token = ""
                if char.isspace():
                    continue
                if char == '"':
                    self.string = True
                    self.key_string = bool(self.stack and self.stack[-1]["expect"] == "key")
                elif char in "{[":
                    value = {} if char == "{" else []
                    path = self._put(value)
                    self.stack.append({"path": path, "value": value, "key": None,
                                       "expect": "key" if char == "{" else "value", "fresh": True})
                    if len(self.stack) > 24:
                        raise ValueError("JSON nesting limit")
                elif char in "}]":
                    frame = self.stack.pop()
                    if (char == "}") != isinstance(frame["value"], dict):
                        raise ValueError("Mismatched JSON container")
                    if frame["expect"] not in {"comma", "key", "value"} or (
                        frame["expect"] != "comma" and not frame["fresh"]
                    ):
                        raise ValueError("Incomplete JSON container")
                    self.complete.add(frame["path"])
                elif char == ":" and self.stack[-1]["expect"] == "colon":
                    self.stack[-1]["expect"] = "value"
                elif char == "," and self.stack[-1]["expect"] == "comma":
                    self.stack[-1].update(expect="key" if isinstance(self.stack[-1]["value"], dict) else "value", fresh=False)
                elif char in "-0123456789tfn":
                    self.token = char
                else:
                    raise ValueError("Invalid JSON token")
            return self._preview()
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError, RecursionError):
            self.disabled = True
            return None

    def _preview(self):
        raw = self.root
        if not isinstance(raw, dict) or raw.get("op") not in {"mount", "patch"}:
            return None
        required = {"op", "block_id"} | ({"component"} if raw["op"] == "mount" else {"base_revision"})
        if any((key,) not in self.complete for key in required):
            return None
        if any(key in raw and not isinstance(raw[key], dict) for key in ("props", "upsert")):
            raise ValueError("UI props and upsert must be objects")
        if raw["op"] == "mount" and ("props", "title") not in self.complete:
            return None
        command = {key: raw[key] for key in required}
        command["props"] = {key: value for key, value in raw.get("props", {}).items()
                            if ("props", key) in self.complete}
        command["upsert"] = {}
        count = 0
        for collection in ("blocks", "rows", "nodes", "edges", "items"):
            entries = []
            for index, value in enumerate(raw.get("upsert", {}).get(collection, [])):
                path = ("upsert", collection, index)
                if path in self.complete:
                    entries.append(value)
                    count += 1
                elif collection == "blocks" and isinstance(value, dict):
                    node = {key: item for key, item in value.items() if path + (key,) in self.complete}
                    if self.string and not self.key_string and self._path() == path + ("text",):
                        # Withhold an incomplete escape/surrogate suffix while
                        # retaining the longest decodable prefix of the text.
                        for trim in range(min(13, len(self.token) + 1)):
                            try:
                                text = json.loads('"' + (self.token[:-trim] if trim else self.token) + '"')
                                text.encode("utf-8")
                                node["text"] = text
                                break
                            except (ValueError, UnicodeError):
                                continue
                    if "id" in node and node.get("type") in {"paragraph", "heading", "list", "quote", "code", "math"}:
                        node["state"] = "streaming"
                        entries.append(node)
            if entries:
                command["upsert"][collection] = entries
        # Bound persistence/SSE volume for token-sized chunks, but publish each
        # completed record immediately, even if the next record has not started.
        if self.last is not None and count == self.last_count and self.since_emit < 128:
            return None
        candidate = copy.deepcopy(command)
        if candidate == self.last:
            return None
        self.last = candidate
        self.last_count = count
        self.since_emit = 0
        return candidate
