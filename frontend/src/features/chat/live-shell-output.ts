export const LIVE_SHELL_MAX_LINES = 200;
export const LIVE_SHELL_MAX_CHARS = 8_000;

// CSI sequences (ESC [ ... final), OSC sequences (ESC ] ... BEL/ST), and simple ESC codes.
const ANSI_OSC_RE = /\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g;
const ANSI_CSI_RE = /\x1b\[[0-?]*[ -/]*[@-~]/g;
const ANSI_OTHER_RE = /\x1b[@-Z\\-_]/g;

export interface NormalizedLiveShellOutput {
  text: string;
  truncated: boolean;
  lineCount: number;
}

/**
 * Normalize raw live terminal output for safe bounded rendering:
 * - Strip ANSI escape sequences (colors, cursor movement, OSC titles)
 * - Apply carriage-return (\r) overwrites so progress bars stay single-line
 * - Cap output to the trailing 200 lines and 8,000 characters
 */
export function normalizeLiveShellOutput(
  raw: string,
  options: {
    alreadyTruncated?: boolean;
    maxLines?: number;
    maxChars?: number;
  } = {},
): NormalizedLiveShellOutput {
  const maxLines = options.maxLines ?? LIVE_SHELL_MAX_LINES;
  const maxChars = options.maxChars ?? LIVE_SHELL_MAX_CHARS;

  if (!raw) {
    return { text: "", truncated: Boolean(options.alreadyTruncated), lineCount: 0 };
  }

  const stripped = raw
    .replace(ANSI_OSC_RE, "")
    .replace(ANSI_CSI_RE, "")
    .replace(ANSI_OTHER_RE, "")
    .replace(/\r\n/g, "\n");

  const resolvedLines = stripped.split("\n").map((line) => {
    if (!line.includes("\r")) return line;
    const segments = line.split("\r");
    // Trailing \r (common at the end of a progress bar line) should keep the last non-empty overwrite.
    for (let index = segments.length - 1; index >= 0; index -= 1) {
      if (segments[index].length > 0) return segments[index];
    }
    return "";
  });

  let truncated = Boolean(options.alreadyTruncated);
  let boundedLines = resolvedLines;
  if (boundedLines.length > maxLines) {
    truncated = true;
    boundedLines = boundedLines.slice(-maxLines);
  }

  let text = boundedLines.join("\n");
  if (text.length > maxChars) {
    truncated = true;
    text = text.slice(-maxChars);
    const firstNewline = text.indexOf("\n");
    if (firstNewline > 0 && firstNewline < text.length - 1) {
      text = text.slice(firstNewline + 1);
    }
  }

  const trimmed = text.replace(/\n+$/, "");
  const lineCount = trimmed ? trimmed.split("\n").length : 0;
  return { text: trimmed, truncated, lineCount };
}
