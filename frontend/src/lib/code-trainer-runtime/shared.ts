export type RuntimeRequest = { code: string; stdin: string; language: string; timeLimitMs: number; assets: Record<string, ArrayBuffer> };
export const OUTPUT_LIMIT = 16000;
export function outputCollector() {
  let stdout = "", stderr = "", used = 0;
  return {
    write(text: string, error = false) {
      const remaining = Math.max(0, OUTPUT_LIMIT - used);
      const part = text.slice(0, remaining);
      if (error) stderr += part; else stdout += part;
      used += text.length;
      if (used > OUTPUT_LIMIT) throw new Error("Output limit exceeded");
    },
    get stdout() { return stdout; },
    get stderr() { return stderr; },
    get exceeded() { return used > OUTPUT_LIMIT; },
  };
}
export function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
