/// <reference lib="esnext.disposable" />
import { newQuickJSWASMModuleFromVariant } from "quickjs-emscripten";
import variant from "@jitl/quickjs-singlefile-browser-release-sync";
import * as esbuild from "esbuild-wasm/lib/browser";
import { errorText, outputCollector } from "./shared";
import type { RuntimeRequest } from "./shared";

globalThis.onmessage = async ({ data }: MessageEvent<RuntimeRequest>) => {
  const out = outputCollector();
  let phase = "load";
  let started = 0;
  try {
    let code = data.code;
    const QuickJS = await newQuickJSWASMModuleFromVariant(variant);
    if (data.language === "typescript") {
      await esbuild.initialize({ wasmModule: await WebAssembly.compile(data.assets["esbuild.wasm"]), worker: false });
      phase = "compile";
      code = (await esbuild.transform(code, { loader: "ts", target: "es2022", format: "cjs", sourcefile: "solution.ts" })).code;
    }
    using runtime = QuickJS.newRuntime();
    runtime.setMemoryLimit(64 * 1024 * 1024);
    runtime.setMaxStackSize(512 * 1024);
    started = performance.now();
    const deadline = started + data.timeLimitMs;
    runtime.setInterruptHandler(() => performance.now() > deadline || out.exceeded);
    using vm = runtime.newContext();
    const put = (name: string, value: string) => { using handle = vm.newString(value); vm.setProp(vm.global, name, handle); };
    put("stdin", data.stdin);
    using print = vm.newFunction("__print", (text, error) => { out.write(vm.getString(text), vm.getNumber(error) === 1); });
    vm.setProp(vm.global, "__print", print);
    // Only string IO is bridged. No fetch, DOM, cookies, storage, imports, or host objects.
    const prelude = `const __lines=stdin.split(/\\r?\\n/);let __line=0;
      const readline=()=>__line<__lines.length?__lines[__line++]:null;
      const __format=x=>typeof x==='string'?x:JSON.stringify(x)??String(x);
      const console={log:(...x)=>__print(x.map(__format).join(' ')+'\\n',0),info:(...x)=>__print(x.map(__format).join(' ')+'\\n',0),error:(...x)=>__print(x.map(__format).join(' ')+'\\n',1),warn:(...x)=>__print(x.map(__format).join(' ')+'\\n',1)};
      const print=(...x)=>console.log(...x);const exports={};`;
    postMessage({ phase: "ready" });
    phase = "run";
    const result = vm.evalCode(`${prelude}\n${code}`, "solution.js", { type: "global" });
    if (result.error) {
      using error = result.error;
      const dumped = vm.dump(error);
      throw new Error(typeof dumped === "object" ? `${dumped?.name ?? "Error"}: ${dumped?.message ?? "Execution failed"}` : String(dumped));
    }
    result.value.dispose();
    while (runtime.hasPendingJob()) {
      const jobs = runtime.executePendingJobs(1);
      if (jobs.error) { using error = jobs.error; throw new Error(JSON.stringify(jobs.error.context.dump(error))); }
      if (performance.now() > deadline) throw new Error("interrupted");
    }
    postMessage({ status: "completed", stdout: out.stdout, stderr: out.stderr, durationMs: performance.now() - started });
  } catch (error) {
    const message = errorText(error);
    const status = phase === "load" ? "load_error" : phase === "compile" || message.includes("SyntaxError") ? "compile_error" : out.exceeded ? "output_limit" : /interrupted/.test(message) ? "time_limit" : /out of memory/i.test(message) ? "memory_limit" : "runtime_error";
    postMessage({ status, stdout: out.stdout, stderr: (out.stderr + message).slice(0, 16000), durationMs: started ? performance.now() - started : undefined });
  }
};
