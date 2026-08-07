#!/usr/bin/env python3
"""Scaffold a minimal, offline-buildable frontend project in the workspace.

Writes only inside the workspace; never downloads packages (the image's
/node_modules already provides vite/vue/react/typescript). For plain-html the
template is a single self-contained index.html.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _safe(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RuntimeError("path must stay inside the sandbox workspace")
    return path


_VITE_PKG = (
    '{"name":"app","private":true,"version":"0.1.0",'
    '"scripts":{"build":"vite build","dev":"vite"}}'
)

_PLAIN_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font-family:'Noto Sans CJK SC',sans-serif;margin:0;background:#f7f7f9;color:#222;}}
main{{max-width:720px;margin:3rem auto;padding:2rem;background:#fff;border-radius:12px;
box-shadow:0 2px 12px rgba(0,0,0,.08);}}
h1{{color:#C4472E;}}</style></head><body><main>
<h1>{title}</h1><p>这是一个离线可构建的静态页面。</p></main></body></html>
"""

_REACT_MAIN = """import React from 'react'
import { createRoot } from 'react-dom/client'
function App() {{
  return <main style={{fontFamily:"'Noto Sans CJK SC',sans-serif",padding:"2rem"}}>
    <h1>__TITLE__</h1><p>React + Vite 离线示例。</p></main>
}}
createRoot(document.getElementById('root')!).render(<App />)
"""

_VUE_MAIN = """import { createApp } from 'vue'
const App = {{
  template: `<main style="font-family:'Noto Sans CJK SC',sans-serif;padding:2rem">
    <h1>__TITLE__</h1><p>Vue + Vite 离线示例。</p></main>`,
}}
createApp(App).mount('#root')
"""


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def scaffold(root: Path, framework: str, title: str) -> int:
    if root.exists() and any(root.iterdir()) and not (root / "scaffold.lg").exists():
        raise RuntimeError("target directory is not empty; choose a new path or --overwrite")
    if framework == "plain-html":
        _write_text(root / "index.html", _PLAIN_HTML.format(title=title))
        _write_text(root / "scaffold.lg", "plain-html\n")
    else:
        _write_text(root / "package.json", _VITE_PKG)
        if framework in ("react", "vite-ts"):
            _write_text(root / "index.html", '<div id="root"></div>\n<script type="module" src="/src/main.tsx"></script>\n')
            _write_text(root / "src" / "main.tsx", _REACT_MAIN.replace("__TITLE__", title))
            _write_text(root / "vite.config.ts", "import react from '@vitejs/plugin-react'\nimport { defineConfig } from 'vite'\nexport default defineConfig({ plugins: [react()] })\n")
        elif framework == "vue":
            _write_text(root / "index.html", '<div id="root"></div>\n<script type="module" src="/src/main.ts"></script>\n')
            _write_text(root / "src" / "main.ts", _VUE_MAIN.replace("__TITLE__", title))
            _write_text(root / "vite.config.ts", "import vue from '@vitejs/plugin-vue'\nimport { defineConfig } from 'vite'\nexport default defineConfig({ plugins: [vue()] })\n")
        else:
            raise RuntimeError(f"unsupported framework: {framework}")
        _write_text(root / "scaffold.lg", f"{framework}\n")
    files = list(root.rglob("*"))
    return len([f for f in files if f.is_file()])


def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold a minimal offline frontend project.")
    parser.add_argument("--dir", required=True, help="workspace-relative project directory")
    parser.add_argument("--framework", choices=["react", "vue", "plain-html", "vite-ts"], default="plain-html")
    parser.add_argument("--title", default="离线页面")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    root = _safe(args.dir)
    if root.exists() and any(root.iterdir()) and not args.overwrite and not (root / "scaffold.lg").exists():
        raise RuntimeError("target directory is not empty; pass --overwrite to replace")
    count = scaffold(root, args.framework, args.title)
    print(json.dumps({"status": "ok", "dir": str(root), "framework": args.framework, "files": count}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
