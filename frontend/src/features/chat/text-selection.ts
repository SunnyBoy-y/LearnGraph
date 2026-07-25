/**
 * Chat message text-selection helpers.
 *
 * The browser returns the *rendered* selection (`Selection.toString()`), while
 * the durable message body is raw markdown. Single-line plain text usually
 * matches 1:1; multi-line / cross-block selections often insert extra newlines
 * or skip markdown markers, so exact `indexOf` fails and the floating toolbar
 * used to disappear. These helpers keep the toolbar visible and recover a
 * durable substring whenever possible.
 */

export type LocatedSelection = {
  selected_text: string;
  prefix: string;
  suffix: string;
  /** True when `selected_text` is a contiguous substring of the source content. */
  contentMatched: boolean;
};

function collapseWhitespace(value: string): string {
  return value.replace(/\s+/gu, " ").trim();
}

function collectExactIndexes(content: string, selectedText: string): number[] {
  if (!selectedText) return [];
  const indexes: number[] = [];
  let offset = 0;
  while (offset <= content.length - selectedText.length) {
    const index = content.indexOf(selectedText, offset);
    if (index < 0) break;
    indexes.push(index);
    offset = index + Math.max(1, selectedText.length);
  }
  return indexes;
}

/**
 * Prefer the occurrence suggested by the DOM walk, then fall back to the
 * prefix/suffix hints the way the backend resolves multi-occurrence text.
 */
function pickExactIndex(
  content: string,
  selectedText: string,
  occurrenceIndex: number,
  prefixHint: string,
  suffixHint: string,
): number | undefined {
  const indexes = collectExactIndexes(content, selectedText);
  if (!indexes.length) return undefined;

  const preferred = indexes[occurrenceIndex];
  if (preferred !== undefined) {
    const before = content.slice(0, preferred);
    const after = content.slice(preferred + selectedText.length);
    if (
      (!prefixHint || before.endsWith(prefixHint)) &&
      (!suffixHint || after.startsWith(suffixHint))
    ) {
      return preferred;
    }
  }

  const prefix = prefixHint.slice(-500);
  const suffix = suffixHint.slice(0, 500);
  for (const candidate of indexes) {
    const before = content.slice(0, candidate);
    const after = content.slice(candidate + selectedText.length);
    if (
      (!prefix || before.endsWith(prefix)) &&
      (!suffix || after.startsWith(suffix))
    ) {
      return candidate;
    }
  }
  return indexes[0];
}

/**
 * Find a content span whose whitespace-collapsed form equals the collapsed
 * selection. Returns the exact content slice so the backend can re-verify it.
 */
function findCollapsedSpan(
  content: string,
  selectedText: string,
): { index: number; end: number } | null {
  const target = collapseWhitespace(selectedText);
  if (target.length < 3) return null;

  for (let start = 0; start < content.length; start += 1) {
    // Starting mid-run of whitespace cannot produce a tighter match.
    if (start > 0 && /\s/u.test(content[start]!) && /\s/u.test(content[start - 1]!)) {
      continue;
    }
    let collapsed = "";
    let trailingSpace = false;
    for (let end = start; end < content.length; end += 1) {
      const ch = content[end]!;
      if (/\s/u.test(ch)) {
        if (collapsed.length > 0) trailingSpace = true;
        continue;
      }
      if (trailingSpace) {
        collapsed += " ";
        trailingSpace = false;
      }
      collapsed += ch;
      if (collapsed.length > target.length) break;
      if (collapsed === target) return { index: start, end: end + 1 };
    }
  }
  return null;
}

/**
 * When the browser drops markdown markers (table pipes, emphasis, etc.) the
 * collapsed forms diverge. Walk significant tokens from the selection in order
 * through the content and take the span covering the first through last hit.
 */
function findTokenSpan(
  content: string,
  selectedText: string,
): { index: number; end: number } | null {
  // Tokens of 2+ non-whitespace chars keep short particles from false positives.
  const tokens = selectedText
    .split(/\s+/u)
    .map((token) => {
      // Strip common markdown wrappers so table-cell text can re-anchor.
      let cleaned = token;
      while (cleaned.length > 0 && "|`*_~>#+-[]()".includes(cleaned[0]!)) {
        cleaned = cleaned.slice(1);
      }
      while (
        cleaned.length > 0 &&
        "|`*_~>#+-[]()".includes(cleaned[cleaned.length - 1]!)
      ) {
        cleaned = cleaned.slice(0, -1);
      }
      return cleaned;
    })
    .filter((token) => token.length >= 2);
  if (tokens.length < 2) return null;

  let searchFrom = 0;
  let spanStart = -1;
  let spanEnd = -1;
  let hits = 0;
  for (const token of tokens) {
    const hit = content.indexOf(token, searchFrom);
    if (hit < 0) continue;
    if (spanStart < 0) spanStart = hit;
    spanEnd = hit + token.length;
    searchFrom = spanEnd;
    hits += 1;
  }
  // Require a real majority of tokens so random shared words don't match.
  if (spanStart < 0 || spanEnd <= spanStart || hits < Math.ceil(tokens.length * 0.6)) {
    return null;
  }
  const matched = content.slice(spanStart, spanEnd);
  // Guard against accidentally spanning half the message for a short pick.
  if (matched.length > Math.max(selectedText.length * 3, 80) && matched.length > 240) {
    return null;
  }
  return { index: spanStart, end: spanEnd };
}

/**
 * Map a browser selection onto the durable message body.
 * Always returns a payload suitable for the floating toolbar; `contentMatched`
 * tells the caller whether `selection_context` is safe to send upstream.
 */
export function locateSelectionInContent(
  content: string,
  selectedText: string,
  options: {
    occurrenceIndex?: number;
    prefixHint?: string;
    suffixHint?: string;
  } = {},
): LocatedSelection {
  const trimmed = selectedText.trim();
  const occurrenceIndex = options.occurrenceIndex ?? 0;
  const prefixHint = options.prefixHint ?? "";
  const suffixHint = options.suffixHint ?? "";

  if (!trimmed) {
    return {
      selected_text: "",
      prefix: "",
      suffix: "",
      contentMatched: false,
    };
  }

  const exactIndex = pickExactIndex(
    content,
    trimmed,
    occurrenceIndex,
    prefixHint,
    suffixHint,
  );
  if (exactIndex !== undefined) {
    return {
      selected_text: trimmed,
      prefix: content.slice(Math.max(0, exactIndex - 500), exactIndex),
      suffix: content.slice(
        exactIndex + trimmed.length,
        exactIndex + trimmed.length + 500,
      ),
      contentMatched: true,
    };
  }

  const collapsed = findCollapsedSpan(content, trimmed);
  if (collapsed) {
    const matched = content.slice(collapsed.index, collapsed.end);
    return {
      selected_text: matched,
      prefix: content.slice(Math.max(0, collapsed.index - 500), collapsed.index),
      suffix: content.slice(collapsed.end, collapsed.end + 500),
      contentMatched: true,
    };
  }

  const tokenSpan = findTokenSpan(content, trimmed);
  if (tokenSpan) {
    const matched = content.slice(tokenSpan.index, tokenSpan.end);
    return {
      selected_text: matched,
      prefix: content.slice(Math.max(0, tokenSpan.index - 500), tokenSpan.index),
      suffix: content.slice(tokenSpan.end, tokenSpan.end + 500),
      contentMatched: true,
    };
  }

  return {
    selected_text: trimmed.slice(0, 500),
    prefix: prefixHint.slice(-500),
    suffix: suffixHint.slice(0, 500),
    contentMatched: false,
  };
}

/** Position the floating toolbar near the first line of a multi-line range. */
export function selectionToolbarPoint(range: Range): { left: number; top: number } | null {
  const clientRects = range.getClientRects();
  const rect =
    clientRects.length > 0
      ? clientRects[0]!
      : range.getBoundingClientRect();
  if (!rect.width && !rect.height) return null;
  const safeHalfWidth = Math.min(190, Math.max(120, window.innerWidth / 2 - 12));
  return {
    left: Math.min(
      window.innerWidth - safeHalfWidth,
      Math.max(safeHalfWidth, rect.left + rect.width / 2),
    ),
    top: Math.max(64, rect.top - 9),
  };
}
