const SQL_LINE = /^(?:CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|SELECT|WITH|PRAGMA|SHOW|DESCRIBE|EXPLAIN)\b/i;
const COMMAND_LINE = /^(?:\\d\b|(?:npm|pnpm|yarn|python|pip|curl|docker|git)\s)/i;
const CODE_CONTINUATION = /^(?:\s{2,}|[,)}\]]|FROM\b|WHERE\b|GROUP\s+BY\b|ORDER\s+BY\b|VALUES\b|SET\b|REFERENCES\b|FOREIGN\s+KEY\b)/;

/**
 * Older learning packages sometimes contain a code example as a plain
 * paragraph. Turn only obvious, line-starting examples into fenced Markdown;
 * authored fences and ordinary prose are left untouched.
 */
export function normalizeLearningMarkdown(source: string): string {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const output: string[] = [];
  let inFence = false;
  let inAutoCode = false;

  const closeAutoCode = () => {
    if (!inAutoCode) return;
    while (output.at(-1) === "") output.pop();
    output.push("```");
    inAutoCode = false;
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      closeAutoCode();
      inFence = !inFence;
      output.push(line);
      continue;
    }
    if (inFence) {
      output.push(line);
      continue;
    }

    const startsCode = SQL_LINE.test(trimmed) || COMMAND_LINE.test(trimmed);
    if (!inAutoCode && startsCode) {
      if (output.length > 0 && output.at(-1) !== "") output.push("");
      output.push("```text");
      inAutoCode = true;
      output.push(line);
      continue;
    }
    if (inAutoCode && (startsCode || CODE_CONTINUATION.test(line) || trimmed === "")) {
      output.push(line);
      continue;
    }
    if (inAutoCode) {
      closeAutoCode();
      if (output.at(-1) !== "") output.push("");
    }
    output.push(line);
  }
  closeAutoCode();
  return output.join("\n");
}
