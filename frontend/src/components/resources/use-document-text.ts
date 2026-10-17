import { useEffect, useState } from "react";
import { readDocumentText } from "@/lib/document-text";

export function useDocumentText(blob: Blob) {
  const [result, setResult] = useState<{ blob: Blob; text?: string; error?: string }>();
  useEffect(() => {
    let cancelled = false;
    void readDocumentText(blob).then(
      (text) => { if (!cancelled) setResult({ blob, text }); },
      (reason: unknown) => { if (!cancelled) setResult({ blob, error: reason instanceof Error ? reason.message : "无法读取文本文件" }); },
    );
    return () => { cancelled = true; };
  }, [blob]);
  const current = result?.blob === blob ? result : undefined;
  return { text: current?.text ?? "", error: current?.error, loading: !current };
}
