import { useEffect, useState } from "react";
import { fileStreamUrl } from "@/api/files";

/** Native image/media URLs: no eager Blob download or cached file bytes. */
export function useFileStreamUrl(fileId?: string, revision = "") {
  const owner = `${fileId ?? ""}:${revision}`;
  const [result, setResult] = useState<{ owner: string; source: string; failed: boolean }>();
  useEffect(() => {
    if (!fileId) return;
    const controller = new AbortController();
    void fileStreamUrl(fileId, { signal: controller.signal }).then(
      (source) => { if (!controller.signal.aborted) setResult({ owner, source, failed: false }); },
      () => { if (!controller.signal.aborted) setResult({ owner, source: "", failed: true }); },
    );
    return () => controller.abort();
  }, [fileId, owner, revision]);
  const current = fileId && result?.owner === owner ? result : undefined;
  return { source: current?.source ?? "", failed: current?.failed ?? false, loading: !!fileId && !current };
}
