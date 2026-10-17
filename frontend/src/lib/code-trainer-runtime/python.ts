import { loadPyodide } from "pyodide";
// @ts-expect-error Pyodide exports its Emscripten factory without a declaration.
import createPyodideModule from "pyodide/pyodide.asm.mjs";
import { errorText, outputCollector } from "./shared";
import type { RuntimeRequest } from "./shared";

globalThis.onmessage = async ({ data }: MessageEvent<RuntimeRequest>) => {
  const out = outputCollector();
  let started = 0;
  try {
    // All runtime resources arrive as bytes. This function never forwards a URL to the network.
    globalThis.fetch = async (input) => {
      const name = String(input).split("/").pop()!;
      const bytes = data.assets[name];
      if (!bytes) throw new Error("Network access is disabled");
      return new Response(bytes, { headers: { "Content-Type": name.endsWith(".wasm") ? "application/wasm" : "application/octet-stream" } });
    };
    const py = await loadPyodide({
      indexURL: "https://runtime.invalid/", createPyodideModule,
      lockFileContents: new TextDecoder().decode(data.assets["pyodide-lock.json"]),
      jsglobals: Object.create(null), stdout: () => {}, stderr: () => {},
    });
    // Capture partial lines, support input()/sys.stdin.read(), and isolate every execution in a fresh worker.
    const input = new TextEncoder().encode(data.stdin);
    let offset = 0;
    py.setStdin({ read: buffer => {
      const count = Math.min(buffer.length, input.length - offset);
      buffer.set(input.subarray(offset, offset + count)); offset += count; return count;
    } });
    const stdout = new TextDecoder(), stderr = new TextDecoder();
    py.setStdout({ write: buffer => { out.write(stdout.decode(buffer, { stream: true })); return buffer.length; } });
    py.setStderr({ write: buffer => { out.write(stderr.decode(buffer, { stream: true }), true); return buffer.length; } });
    postMessage({ phase: "ready" });
    started = performance.now();
    // Synchronous algorithm programs only. No automatic package downloading or tool bridge.
    const result = py.runPython(data.code);
    if (result && typeof result.destroy === "function") result.destroy();
    py.runPython("import sys; sys.stdout.flush(); sys.stderr.flush()");
    postMessage({ status: "completed", stdout: out.stdout, stderr: out.stderr, durationMs: performance.now() - started });
  } catch (error) {
    postMessage({ status: !started ? "load_error" : out.exceeded ? "output_limit" : "runtime_error", stdout: out.stdout, stderr: (out.stderr + errorText(error)).slice(0, 16000), durationMs: started ? performance.now() - started : undefined });
  }
};
