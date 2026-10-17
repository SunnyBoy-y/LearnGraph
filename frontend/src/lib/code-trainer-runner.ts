import { z } from "zod";
import { localLanguages, normalizeLanguage } from "./code-trainer";
import type { RunResult } from "./code-trainer";

const resultSchema = z.object({
  status: z.enum(["completed", "compile_error", "runtime_error", "time_limit", "memory_limit", "output_limit", "load_error"]),
  stdout: z.string().max(16000), stderr: z.string().max(16000),
  durationMs: z.number().finite().nonnegative().optional(),
}).strict();
export const TRAINER_CSP = "default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' blob:; worker-src blob:; connect-src 'none'; frame-src 'none'; child-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";

// Only trusted relay code goes in srcdoc. Source code, input and runtime bytes are sent via structured clone.
export function runnerDocument(channel: string) {
  return `<!doctype html><meta http-equiv="Content-Security-Policy" content="${TRAINER_CSP}"><script>
  const channel=${JSON.stringify(channel)};let worker;
  addEventListener('message',event=>{
    if(event.source!==parent||event.data?.channel!==channel||worker)return;
    const {source,request}=event.data;
    const url=URL.createObjectURL(new Blob([source],{type:'text/javascript'}));
    worker=new Worker(url);URL.revokeObjectURL(url);
    worker.onmessage=e=>parent.postMessage({channel,result:e.data},'*');
    worker.onerror=event=>{event.preventDefault();parent.postMessage({channel,result:{status:'load_error',stdout:'',stderr:'浏览器运行环境初始化失败：'+String(event.message||'Worker error').slice(0,500)}},'*');};
    worker.postMessage(request);
  });
  parent.postMessage({channel,ready:true},'*');
  </script>`;
}

const resourceCache = new Map<string, Promise<ArrayBuffer>>();
function resource(name: string) {
  if (!resourceCache.has(name)) {
    const url = `${import.meta.env.BASE_URL}code-trainer/${name}`;
    const pending = fetch(url, { credentials: "omit" }).then(response => {
      if (!response.ok) throw new Error(`运行环境资源加载失败 (${response.status})`);
      return response.arrayBuffer();
    }).catch(error => { resourceCache.delete(name); throw error; });
    resourceCache.set(name, pending);
  }
  return resourceCache.get(name)!;
}

export async function runCode(request: { language: string; code: string; stdin: string; timeLimitMs: number }, signal?: AbortSignal): Promise<RunResult> {
  const language = normalizeLanguage(request.language);
  if (!localLanguages.has(language)) throw new Error("此语言仅支持 AI 分析");
  if (request.code.length > 16000 || request.stdin.length > 4000 || !Number.isFinite(request.timeLimitMs) || request.timeLimitMs < 100 || request.timeLimitMs > 5000) throw new Error("运行请求超出限制");
  if (signal?.aborted) return { status: "cancelled", stdout: "", stderr: "运行已停止" };
  return new Promise(resolve => {
    const frame = document.createElement("iframe");
    frame.hidden = true;
    frame.title = "隔离的编程执行环境";
    frame.setAttribute("sandbox", "allow-scripts");
    frame.referrerPolicy = "no-referrer";
    const channel = crypto.randomUUID();
    let settled = false, ready = false, loaded = false, sent = false;
    let source = "";
    const assets: Record<string, ArrayBuffer> = {};
    let timer = window.setTimeout(() => finish({ status: "load_error", stdout: "", stderr: "运行环境加载超时，请重试" }), 45000);
    const finish = (result: RunResult) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      window.removeEventListener("message", receive);
      signal?.removeEventListener("abort", cancel);
      frame.remove(); // Destroys its entire worker tree, including infinite-loop programs.
      resolve(result);
    };
    const cancel = () => finish({ status: "cancelled", stdout: "", stderr: "运行已停止" });
    const start = () => {
      if (!ready || !loaded || settled || sent) return;
      sent = true;
      frame.contentWindow?.postMessage({ channel, source, request: { ...request, language, assets } }, "*");
    };
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.contentWindow || event.origin !== "null" || event.data?.channel !== channel) return;
      if (event.data.ready === true) { ready = true; start(); return; }
      if (event.data.result?.phase === "ready") {
        clearTimeout(timer);
        timer = window.setTimeout(() => finish({ status: "time_limit", stdout: "", stderr: "超过运行时间限制" }), request.timeLimitMs + 100);
        return;
      }
      const parsed = resultSchema.safeParse(event.data.result);
      if (parsed.success) finish(parsed.data);
    };
    window.addEventListener("message", receive);
    signal?.addEventListener("abort", cancel, { once: true });
    frame.srcdoc = runnerDocument(channel);
    document.body.append(frame);
    void (async () => {
      try {
        const names = language === "python" ? ["pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"] : language === "typescript" ? ["esbuild.wasm"] : [];
        const values = await Promise.all([resource(language === "python" ? "python.js" : "javascript.js"), ...names.map(resource)]);
        source = new TextDecoder().decode(values[0]);
        names.forEach((name, index) => { assets[name] = values[index + 1]; });
        loaded = true; start();
      } catch (error) {
        finish({ status: "load_error", stdout: "", stderr: error instanceof Error ? error.message : "运行环境加载失败" });
      }
    })();
  });
}
