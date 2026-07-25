/**
 * Decode percent-encoded URL text for safe human display.
 * Streamdown's link-safety modal shows raw hrefs; Chinese titles encoded as
 * %E7%9F%A5… look garbled. This helper best-effort decodes while preserving
 * already-readable text and invalid sequences.
 */

export function decodeUrlForDisplay(value: string | null | undefined): string {
  if (!value) return "";
  const raw = value.trim();
  if (!raw) return "";
  try {
    // Repeated decode handles double-encoded fragments without throwing.
    let current = raw;
    for (let i = 0; i < 3; i += 1) {
      const next = decodeURIComponent(current.replace(/\+/g, " "));
      if (next === current) break;
      current = next;
    }
    return current;
  } catch {
    try {
      return decodeURI(raw);
    } catch {
      return raw;
    }
  }
}
