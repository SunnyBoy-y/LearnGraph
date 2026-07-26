from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """CJK-aware heuristic token estimate (no tokenizer dependency).

    Modern tokenizers (Qwen, DeepSeek, GPT, Claude) encode a CJK character as
    roughly one token, while English averages ~4 characters per token. The
    historical ``len(text) / 4`` heuristic therefore underestimated Chinese
    input several-fold, letting prompt budgets overflow the provider's real
    context window. Counting CJK characters at 1 token each keeps the estimate
    conservative for budget decisions without pulling in a tokenizer wheel.
    """

    if not text:
        return 1
    cjk = 0
    for ch in text:
        code = ord(ch)
        if (
            0x2E80 <= code <= 0x9FFF  # CJK radicals, kana, unified ideographs
            or 0xAC00 <= code <= 0xD7A3  # hangul syllables
            or 0xF900 <= code <= 0xFAFF  # compatibility ideographs
            or 0xFF00 <= code <= 0xFFEF  # fullwidth/halfwidth forms
            or 0x20000 <= code <= 0x2FFFF  # extension planes
        ):
            cjk += 1
    other = len(text) - cjk
    return max(1, cjk + (other + 3) // 4)
