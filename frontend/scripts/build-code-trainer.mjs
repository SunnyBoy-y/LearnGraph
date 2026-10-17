import { build } from "esbuild";
import { copyFile, mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(import.meta.url);
const output = join(root, "public/code-trainer");
await mkdir(output, { recursive: true });
for (const language of ["javascript", "python"]) {
  await build({ entryPoints: [join(root, `src/lib/code-trainer-runtime/${language}.ts`)], outfile: join(output, `${language}.js`), bundle: true, format: "iife", platform: "browser", target: "es2022", minify: true, external: ["node:*"], logLevel: "error" });
}
const pyodide = dirname(require.resolve("pyodide/package.json"));
for (const name of ["pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]) await copyFile(join(pyodide, name), join(output, name));
await copyFile(require.resolve("esbuild-wasm/esbuild.wasm"), join(output, "esbuild.wasm"));
