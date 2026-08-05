from __future__ import annotations

import re

# http(s) URL spanning characters up to the next whitespace/quote/angle
# bracket. CJK text rarely uses angle brackets in URLs, so treating < > " '
# plus CJK closing quotes as terminators keeps the match tight.
_URL_RE = re.compile(r"https?://[^\s<>()\"'“”‘’]+", re.IGNORECASE)


def truncate_without_splitting_urls(text: str, budget: int) -> str:
    """Truncate ``text`` to roughly ``budget`` characters without cutting a URL.

    A naive ``text[:budget]`` can slice an http(s) link in half, leaving the
    model a broken ``https://example.com/p/art…`` it cannot resolve. When the
    natural boundary lands strictly inside a URL, the boundary is pulled back
    to that URL's start so the whole link is either kept or dropped — never
    bisected. Pulling back favours link integrity over squeezing every last
    character out of the budget; over-length history entries are already
    handled by compaction, so this is a safety net, not a sizing mechanism.
    """

    if not text or budget <= 0:
        return text[: max(budget, 0)]
    if len(text) <= budget:
        return text
    boundary = budget
    for match in _URL_RE.finditer(text):
        if match.start() >= boundary:
            # Matches are ordered; no later URL can contain the boundary.
            break
        if match.start() < boundary < match.end():
            boundary = match.start()
            break
    return text[:boundary]
