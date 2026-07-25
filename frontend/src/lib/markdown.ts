/**
 * Models commonly emit TeX delimiters or fenced `latex` blocks. Streamdown's
 * math plugin expects Markdown math delimiters, so convert explicit math
 * fences while preserving every other code block verbatim.
 */
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
