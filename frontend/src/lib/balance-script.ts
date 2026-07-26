import type {
  BalanceExtractorResult,
  ProviderBalanceQueryHttpRequest,
} from "@/types/providers";

/**
 * cc-switch style balance query scripts are user-authored JavaScript of the
 * form `({ request: {...}, extractor: function (response) {...} })`.
 *
 * They are evaluated inside a sandboxed iframe (opaque origin, allow-scripts
 * only) so the script can never touch the app origin, the session token, or
 * the DOM. The HTTP request itself runs on the backend, which owns the
 * plaintext API key; only the parsed response travels back into the sandbox
 * for extraction.
 */

const SCRIPT_TIMEOUT_MS = 5_000;

const SANDBOX_HTML = `<!doctype html><html><head><meta charset="utf-8"></head><body><script>
(function () {
  "use strict";
  var config = null;
  function reply(data) { parent.postMessage(data, "*"); }
  window.addEventListener("message", function (event) {
    var message = event.data || {};
    try {
      if (message.type === "parse") {
        config = (0, eval)(String(message.script));
        if (!config || typeof config !== "object") {
          throw new Error("脚本必须是用 () 包裹的对象字面量表达式");
        }
        var request = config.request;
        if (!request || typeof request !== "object" || typeof request.url !== "string" || !request.url.trim()) {
          throw new Error("脚本缺少 request.url");
        }
        if (typeof config.extractor !== "function") {
          throw new Error("脚本缺少 extractor 函数");
        }
        reply({ type: "request", request: JSON.parse(JSON.stringify({
          url: request.url,
          method: typeof request.method === "string" && request.method ? request.method.toUpperCase() : "GET",
          headers: request.headers && typeof request.headers === "object" ? request.headers : {},
          body: typeof request.body === "string" ? request.body : null
        })) });
      } else if (message.type === "extract") {
        if (!config) { throw new Error("脚本尚未解析"); }
        var result = config.extractor(message.response);
        reply({ type: "result", result: JSON.parse(JSON.stringify(result === undefined ? null : result)) });
      }
    } catch (error) {
      reply({ type: "error", message: error && error.message ? String(error.message) : String(error) });
    }
  });
  reply({ type: "ready" });
})();
</` + `script></body></html>`;

const ALLOWED_METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);

export interface BalanceScriptRuntime {
  request: ProviderBalanceQueryHttpRequest;
  extract(response: unknown): Promise<BalanceExtractorResult[]>;
  dispose(): void;
}

interface SandboxMessage {
  type?: string;
  request?: unknown;
  result?: unknown;
  message?: string;
}

/** Evaluate the config script and keep the sandbox alive for extraction. */
export async function openBalanceScript(
  script: string,
): Promise<BalanceScriptRuntime> {
  const iframe = document.createElement("iframe");
  iframe.setAttribute("sandbox", "allow-scripts");
  iframe.setAttribute("referrerpolicy", "no-referrer");
  iframe.style.display = "none";
  iframe.srcdoc = SANDBOX_HTML;

  let disposed = false;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    iframe.remove();
  };

  const call = <T>(
    payload: { type: string; [key: string]: unknown } | null,
    accept: (message: SandboxMessage) => T | undefined,
  ): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        cleanup();
        reject(new Error("脚本执行超时"));
      }, SCRIPT_TIMEOUT_MS);
      const onMessage = (event: MessageEvent) => {
        if (event.source !== iframe.contentWindow) return;
        const message = (event.data ?? {}) as SandboxMessage;
        if (message.type === "error") {
          cleanup();
          reject(new Error(message.message || "脚本执行失败"));
          return;
        }
        const value = accept(message);
        if (value !== undefined) {
          cleanup();
          resolve(value);
        }
      };
      const cleanup = () => {
        window.clearTimeout(timer);
        window.removeEventListener("message", onMessage);
      };
      window.addEventListener("message", onMessage);
      if (payload) {
        iframe.contentWindow?.postMessage(payload, "*");
      }
    });

  document.body.appendChild(iframe);
  try {
    await call(null, (message) => (message.type === "ready" ? true : undefined));
    const request = await call(
      { type: "parse", script },
      (message) => (message.type === "request" ? message.request : undefined),
    );
    return {
      request: normalizeRequest(request),
      extract: async (response: unknown) => {
        const raw = await call(
          { type: "extract", response },
          (message) => (message.type === "result" ? (message.result ?? null) : undefined),
        );
        return normalizeExtractorResults(raw);
      },
      dispose,
    };
  } catch (error) {
    dispose();
    throw error;
  }
}

function normalizeRequest(raw: unknown): ProviderBalanceQueryHttpRequest {
  const record = (raw ?? {}) as Record<string, unknown>;
  const url = typeof record.url === "string" ? record.url.trim() : "";
  if (!url) throw new Error("脚本缺少 request.url");
  const method =
    typeof record.method === "string" && ALLOWED_METHODS.has(record.method)
      ? (record.method as ProviderBalanceQueryHttpRequest["method"])
      : "GET";
  const headers: Record<string, string> = {};
  if (
    record.headers &&
    typeof record.headers === "object" &&
    !Array.isArray(record.headers)
  ) {
    for (const [name, value] of Object.entries(
      record.headers as Record<string, unknown>,
    )) {
      if (!name.trim()) continue;
      headers[name.trim()] = String(value ?? "");
    }
  }
  return {
    url,
    method,
    headers,
    body: typeof record.body === "string" ? record.body : null,
  };
}

function normalizeExtractorResults(raw: unknown): BalanceExtractorResult[] {
  const entries = Array.isArray(raw) ? raw : [raw];
  if (!entries.length) {
    throw new Error("extractor 返回了空数组");
  }
  return entries.map((entry, index) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new Error(
        entries.length > 1
          ? `extractor 第 ${index + 1} 项不是对象`
          : "extractor 必须返回对象或对象数组",
      );
    }
    const record = entry as Record<string, unknown>;
    const numeric = (value: unknown): number | undefined =>
      typeof value === "number" && Number.isFinite(value) ? value : undefined;
    const text = (value: unknown): string | undefined =>
      typeof value === "string" && value.trim() ? value.trim() : undefined;
    return {
      isValid: typeof record.isValid === "boolean" ? record.isValid : undefined,
      invalidMessage: text(record.invalidMessage),
      remaining: numeric(record.remaining),
      unit: text(record.unit),
      planName: text(record.planName),
      total: numeric(record.total),
      used: numeric(record.used),
      extra: text(record.extra),
    } satisfies BalanceExtractorResult;
  });
}
