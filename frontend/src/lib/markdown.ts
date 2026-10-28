/**
 * Models commonly emit TeX delimiters or fenced `latex` blocks. Streamdown's
 * math plugin expects Markdown math delimiters, so convert explicit math
 * fences while preserving every other code block verbatim.
 */
export function mathExpression(text: string): string {
  let source = text.trim();
  // Typed math nodes may contain delimiters despite the bare-TeX contract.
  // Unwrap before adding our own pair, including nested generated wrappers.
  for (let i = 0; i < 8; i++) {
    const match = source.match(/^\$\$([\s\S]*)\$\$$/u)
      ?? source.match(/^\\\[([\s\S]*)\\\]$/u)
      ?? source.match(/^\\\(([\s\S]*)\\\)$/u)
      ?? source.match(/^\$([^$][\s\S]*?)\$$/u);
    if (!match) break;
    source = match[1].trim();
  }
  return source;
}

export function mathMarkdown(text: string, inline = false): string {
  const source = mathExpression(text);
  return inline ? `$${source}$` : `$$\n${source}\n$$`;
}

export function normalizeLatexDelimiters(markdown: string): string {
  const withMathFences = markdown.replace(
    /(^|\n)[ \t]*```(?:latex|tex|math)[ \t]*\r?\n([\s\S]*?)\r?\n[ \t]*```(?=\n|$)/giu,
    (_, prefix: string, expression: string) => {
      const source = expression.trim();
      const wrapped =
        source.match(/^\\\(([\s\S]*)\\\)$/u) ??
        source.match(/^\\\[([\s\S]*)\\\]$/u) ??
        source.match(/^\$\$([\s\S]*)\$\$$/u);
      return `${prefix}$$\n${(wrapped?.[1] ?? source).trim()}\n$$`;
    },
  );
  const protectedSegments = /(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\r\n]*`)/g;
  return withMathFences
    .split(protectedSegments)
    .map((segment, index) => {
      if (index % 2 === 1) return segment;
      return segment
        .replace(/\\\[([\s\S]*?)\\\]/g, (_, expression: string) =>
          `$$\n${expression.trim()}\n$$`,
        )
        .replace(/\\\(([\s\S]*?)\\\)/g, (_, expression: string) =>
          `$${expression.trim()}$`,
        );
    })
    .join("");
}
