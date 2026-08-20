#!/usr/bin/env python3
"""LearnGraph 手机 APK 下载站。

用法：
    python scripts/download-server.py [--port 18002] [--dir mobile]

功能：
    - 根路径：手机友好的 APK 列表页（文件名/大小/更新时间/下载按钮）
    - /<file>.apk：直接下载（仅允许 .apk 文件，防目录穿越）
    - 独立于 LiveAgent 生命周期，常驻可用
"""
from __future__ import annotations

import argparse
import datetime
import html
import os
import socketserver
import urllib.parse
from http.server import SimpleHTTPRequestHandler

DEFAULT_PORT = 18002


class ApkDownloadHandler(SimpleHTTPRequestHandler):
    apk_dir: str = ""

    # ------------------------------------------------------------------ #
    # 路由
    # ------------------------------------------------------------------ #

    def do_GET(self) -> None:  # noqa: N802 (HTTP 方法名)
        path = urllib.parse.unquote(self.path.split("?", 1)[0])
        if path in ("", "/"):
            self._serve_index()
            return
        name = path.lstrip("/")
        if not name.endswith(".apk") or "/" in name or "\\" in name:
            self.send_error(404, "Not Found")
            return
        full = os.path.normpath(os.path.join(self.apk_dir, name))
        if not full.startswith(os.path.normpath(self.apk_dir)) or not os.path.isfile(full):
            self.send_error(404, "Not Found")
            return
        # 交给 SimpleHTTPRequestHandler 的标准文件服务（支持 Range）
        self.directory = self.apk_dir
        self.path = "/" + name
        super().do_GET()

    # ------------------------------------------------------------------ #
    # 首页：APK 列表
    # ------------------------------------------------------------------ #

    def _serve_index(self) -> None:
        entries: list[tuple[str, int, float]] = []
        for fname in sorted(os.listdir(self.apk_dir), reverse=True):
            if not fname.endswith(".apk"):
                continue
            full = os.path.join(self.apk_dir, fname)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            entries.append((fname, stat.st_size, stat.st_mtime))

        rows = "\n".join(self._row(fname, size, mtime) for fname, size, mtime in entries)
        body = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>LearnGraph APK 下载站</title>
<style>
  body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0; background: #f6f7f9; color: #1c1e21; }}
  header {{ background: linear-gradient(135deg, #4f6df5, #7b5cf0); color: #fff; padding: 22px 18px; }}
  header h1 {{ margin: 0; font-size: 20px; }}
  header p {{ margin: 6px 0 0; opacity: .85; font-size: 13px; }}
  main {{ max-width: 720px; margin: 16px auto; padding: 0 12px 40px; }}
  .card {{ background: #fff; border-radius: 14px; box-shadow: 0 1px 4px rgba(0,0,0,.08); padding: 14px 16px; margin-bottom: 12px; display: flex; align-items: center; gap: 12px; }}
  .info {{ flex: 1; min-width: 0; }}
  .name {{ font-size: 15px; font-weight: 600; word-break: break-all; }}
  .meta {{ font-size: 12px; color: #777; margin-top: 3px; }}
  .dl {{ background: #4f6df5; color: #fff; text-decoration: none; border-radius: 10px; padding: 10px 18px; font-size: 14px; white-space: nowrap; }}
  .empty {{ text-align: center; color: #999; padding: 40px 0; }}
</style>
</head>
<body>
<header>
  <h1>📦 LearnGraph 手机版 APK</h1>
  <p>下载后直接安装，覆盖升级不会丢失登录态</p>
</header>
<main>
  {rows if rows else '<div class="empty">目录中没有 APK 文件</div>'}
</main>
</body>
</html>"""
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _row(fname: str, size: int, mtime: float) -> str:
        size_txt = f"{size / 1024 / 1024:.1f} MB" if size > 1024 * 1024 else f"{size / 1024:.0f} KB"
        when = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        return (
            f'<div class="card"><div class="info">'
            f'<div class="name">{html.escape(fname)}</div>'
            f'<div class="meta">{size_txt} · {when}</div>'
            f'</div>'
            f'<a class="dl" href="{urllib.parse.quote(fname)}" download>下载</a></div>'
        )

    # ------------------------------------------------------------------ #
    # 杂项
    # ------------------------------------------------------------------ #

    def log_message(self, fmt: str, *args: object) -> None:
        try:
            super().log_message(fmt, *args)
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="LearnGraph APK 下载站")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--dir", default="")
    args = parser.parse_args()

    base = os.path.dirname(os.path.abspath(__file__))
    apk_dir = os.path.abspath(args.dir) if args.dir else os.path.abspath(os.path.join(base, "..", "mobile"))

    # 类体无法直接引用外部局部变量（NameError），先设基类属性再继承
    ApkDownloadHandler.apk_dir = apk_dir

    class Handler(ApkDownloadHandler):
        pass

    with socketserver.ThreadingTCPServer(("0.0.0.0", args.port), Handler) as httpd:
        print(f"LearnGraph APK 下载站启动: http://0.0.0.0:{args.port} (目录: {apk_dir})")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
