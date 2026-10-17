"""Streaming rejection of tool envelopes on a direct-answer path.

This is deliberately not a tool-call parser: rejected text is never decoded or
executed. Only a short possible opening/closing marker is retained in memory.
Literal Markdown examples and native UI declarations remain ordinary content.
"""
from __future__ import annotations

import logging
import re


_OPENINGS = {
    f"<{marker} {kind}{delimiter}": f"</{marker} {kind}>"
    for marker in ("｜DSML｜", "｜｜DSML｜｜", "|DSML|", "||DSML||")
    for kind in ("calls", "function_calls", "invoke", "parameter")
    for delimiter in (">", " ", "\n")
}
# DeepSeek also uses the spelling without a space after its sentinel.
_OPENINGS.update({
    f"<{marker}{kind}{delimiter}": f"</{marker}{kind}>"
    for marker in ("｜DSML｜", "｜｜DSML｜｜", "|DSML|", "||DSML||")
    for kind in ("calls", "function_calls", "invoke", "parameter")
    for delimiter in (">", " ", "\n")
})
_OPENINGS.update({f"<{name}{delimiter}": f"</{name}>"
                  for name in ("tool_call", "tool_calls", "function_calls")
                  for delimiter in (">", " ", "\n")})


class ToolProtocolGuard:
    def __init__(self):
        self.suppressed = False
        self._candidate = ""
        self._closing = ""
        self._tail = ""
        self._line = ""
        self._fence: tuple[str, int] | None = None
        self._declaration = False
        self._ticks = 0
        self._inline_ticks: int | None = None

    def reject(self):
        if not self.suppressed:
            logging.getLogger(__name__).warning("Tool protocol suppressed on direct-answer path")
        self.suppressed = True

    def _track(self, char):
        if char == "`":
            self._ticks += 1
        else:
            if self._ticks:
                if self._inline_ticks is None:
                    self._inline_ticks = self._ticks
                elif self._inline_ticks == self._ticks:
                    self._inline_ticks = None
                self._ticks = 0
        if char != "\n":
            self._line = (self._line + char)[:256]
            return
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", self._line)
        if fence and not self._declaration:
            marker, suffix = fence.groups()
            if self._fence is None:
                self._fence = (marker[0], len(marker))
            elif marker[0] == self._fence[0] and len(marker) >= self._fence[1] and not suffix.strip():
                self._fence = None
            self._inline_ticks = None
        if self._fence is None:
            if self._declaration and self._line.strip() == ":::":
                self._declaration = False
            elif re.fullmatch(r":::ui [a-zA-Z0-9_-]{1,70}\r?", self._line):
                self._declaration = True
        self._line = ""

    def feed(self, text):
        out = []
        for char in text:
            if self._closing:
                self._tail = (self._tail + char)[-len(self._closing):]
                if self._tail == self._closing:
                    self._closing = self._tail = ""
                continue
            if char != "`" and self._ticks:
                if self._inline_ticks is None:
                    self._inline_ticks = self._ticks
                elif self._inline_ticks == self._ticks:
                    self._inline_ticks = None
                self._ticks = 0
            literal = self._fence or self._declaration or self._inline_ticks or self._ticks
            if self._candidate or (char == "<" and not literal):
                self._candidate += char
                closing = _OPENINGS.get(self._candidate)
                if closing:
                    self.reject()
                    self._closing = closing
                    self._candidate = ""
                elif not any(opening.startswith(self._candidate) for opening in _OPENINGS):
                    out.append(self._candidate)
                    for value in self._candidate:
                        self._track(value)
                    self._candidate = ""
                continue
            out.append(char)
            self._track(char)
        return "".join(out)

    def finish(self):
        # An incomplete reserved sentinel is also a protocol leak. A plain '<'
        # or '<tool' can be ordinary prose and must remain intact.
        pending = self._candidate
        self._candidate = ""
        if pending and ("DSML" in pending or pending.startswith(("<｜", "<|D", "<||D", "<tool_call", "<function_call"))):
            self.reject()
            return ""
        return pending
