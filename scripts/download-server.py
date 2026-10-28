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
import hashlib
import html
import os
import re
import socket
import socketserver
import urllib.parse
from http.server import SimpleHTTPRequestHandler

DEFAULT_PORT = 18002
FAVICON_SVG = (
    b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    b"<rect width='32' height='32' rx='7' fill='#16635a'/>"
    b"<path d='M16 7v15m0 0-6-6m6 6 6-6' stroke='white' stroke-width='2.5' "
    b"stroke-linecap='round' stroke-linejoin='round'/></svg>"
)


def _detect_lan_ip() -> str:
    """Return the interface IP used for the default route, avoiding link-local addresses."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        routed_ip = sock.getsockname()[0]
        if routed_ip and not routed_ip.startswith(("127.", "169.254.")):
            return routed_ip
    except OSError:
        pass
    finally:
        sock.close()

    try:
        host_ip = socket.gethostbyname(socket.gethostname())
        if host_ip and not host_ip.startswith(("127.", "169.254.")):
            return host_ip
    except OSError:
        pass
    return "127.0.0.1"


class ApkDownloadHandler(SimpleHTTPRequestHandler):
    apk_dir: str = ""
    preferred_ip: str = ""
    current_version: str = ""
    protocol_version = "HTTP/1.1"  # Python 3.14 默认 1.0 且无 Range，这里显式开 1.1

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------------------------------------------ #
    # 路由
    # ------------------------------------------------------------------ #

    def do_GET(self) -> None:  # noqa: N802 (HTTP 方法名)
        self._route(head=False)

    def do_HEAD(self) -> None:  # noqa: N802 (HTTP 方法名)
        self._route(head=True)

    def _route(self, *, head: bool) -> None:
        path = urllib.parse.unquote(self.path.split("?", 1)[0])
        if path in ("", "/"):
            self._serve_index(head=head)
            return
        if path == "/favicon.ico":
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(FAVICON_SVG)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            if not head:
                self.wfile.write(FAVICON_SVG)
            return
        if path.startswith("/sha256/"):
            name = path[len("/sha256/"):]
            if not name.endswith(".apk") or "/" in name or "\\" in name:
                self.send_error(404, "Not Found")
                return
            full = os.path.normpath(os.path.join(self.apk_dir, name))
            if not full.startswith(os.path.normpath(self.apk_dir)) or not os.path.isfile(full):
                self.send_error(404, "Not Found")
                return
            self._serve_hash(full, head=head)
            return
        name = path.lstrip("/")
        if not name.endswith(".apk") or "/" in name or "\\" in name:
            self.send_error(404, "Not Found")
            return
        full = os.path.normpath(os.path.join(self.apk_dir, name))
        if not full.startswith(os.path.normpath(self.apk_dir)) or not os.path.isfile(full):
            self.send_error(404, "Not Found")
            return
        self._serve_apk(full, head=head)

    def _serve_hash(self, full: str, *, head: bool) -> None:
        digest = hashlib.sha256()
        with open(full, "rb") as fh:
            while chunk := fh.read(1024 * 1024):
                digest.update(chunk)
        data = f"{digest.hexdigest()}  {os.path.basename(full)}\n".encode("ascii")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=ascii")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head:
            self.wfile.write(data)

    # ------------------------------------------------------------------ #
    # APK 直链：自定义 Range 支持（Python 3.14 移除了 SimpleHTTPRequestHandler 的 Range）
    # ------------------------------------------------------------------ #

    def _serve_apk(self, full: str, *, head: bool) -> None:
        size = os.path.getsize(full)
        start, end = 0, size - 1
        range_header = self.headers.get("Range")
        if range_header:
            match = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
            if match:
                raw_start, raw_end = match.groups()
                if raw_start:
                    start = int(raw_start)
                    if raw_end:
                        end = min(int(raw_end), size - 1)
                elif raw_end:  # suffix range: bytes=-N
                    start = max(size - int(raw_end), 0)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return

        partial = start > 0 or end < size - 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "application/vnd.android.package-archive")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        try:
            with open(full, "rb") as fh:
                fh.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    chunk = fh.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端中断下载：静默

    # ------------------------------------------------------------------ #
    # 首页：APK 列表
    # ------------------------------------------------------------------ #

    def _serve_index(self, *, head: bool) -> None:
        entries: list[tuple[str, int, float]] = []
        for fname in os.listdir(self.apk_dir):
            if not fname.endswith(".apk"):
                continue
            full = os.path.join(self.apk_dir, fname)
            try:
                stat = os.stat(full)
            except OSError:
                continue
            entries.append((fname, stat.st_size, stat.st_mtime))
        # 按修改时间倒序：新的版本排最前
        entries.sort(key=lambda e: e[2], reverse=True)

        rows = "\n".join(self._row(fname, size, mtime) for fname, size, mtime in entries)
        lan_url = f"http://{self.preferred_ip}:{self.server.server_address[1]}/"
        body = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>LearnGraph APK 下载站</title>
<link rel="icon" href="/favicon.ico" />
<style>
  :root {{ color-scheme: light dark; --bg: #f5f3ed; --fg: #1c2927; --muted: #66736f; --line: #d9ddd5; --accent: #16635a; --accent-fg: #fff; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #121917; --fg: #edf2ed; --muted: #a7b3ae; --line: #34413d; --accent: #78cbbd; --accent-fg: #10211d; }} }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: var(--bg); color: var(--fg); }}
  header {{ padding: 28px max(18px, calc((100vw - 780px) / 2)); border-bottom: 1px solid var(--line); }}
  header h1 {{ margin: 0; font-size: 24px; letter-spacing: 0; }}
  header p {{ margin: 8px 0 0; color: var(--muted); font-size: 14px; line-height: 1.5; }}
  main {{ max-width: 780px; margin: 0 auto; padding: 18px 12px 48px; }}
  .notice {{ border-left: 3px solid var(--accent); padding: 0 0 0 12px; margin: 4px 6px 18px; color: var(--muted); font-size: 13px; line-height: 1.6; }}
  .toolbar {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 6px 14px; }}
  .toolbar span {{ color: var(--muted); font-size: 13px; margin-right: 2px; }}
  .filter {{ border: 1px solid var(--line); border-radius: 7px; padding: 7px 12px; background: transparent; color: var(--fg); font: inherit; font-size: 13px; cursor: pointer; }}
  .filter[aria-pressed="true"] {{ border-color: var(--accent); background: var(--accent); color: var(--accent-fg); }}
  [hidden] {{ display: none !important; }}
  .card {{ background: color-mix(in srgb, var(--bg) 88%, var(--fg)); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; margin-bottom: 10px; display: flex; align-items: center; gap: 14px; }}
  .info {{ flex: 1; min-width: 0; }}
  .version-line {{ display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }}
  .version {{ border: 1px solid var(--line); border-radius: 4px; padding: 1px 5px; font-size: 11px; color: var(--muted); white-space: nowrap; }}
  .version.current {{ border-color: var(--accent); color: var(--accent); }}
  .name {{ font-size: 14px; font-weight: 650; overflow-wrap: anywhere; }}
  .meta {{ font-size: 12px; color: var(--muted); margin-top: 5px; }}
  .actions {{ display: flex; align-items: center; gap: 10px; flex-shrink: 0; }}
  .hash {{ color: var(--muted); font-size: 12px; text-decoration: none; }}
  .dl {{ background: var(--accent); color: var(--accent-fg); text-decoration: none; border-radius: 7px; padding: 10px 16px; font-size: 14px; font-weight: 600; white-space: nowrap; }}
  .empty {{ text-align: center; color: var(--muted); padding: 40px 0; }}
  @media (max-width: 520px) {{
    .card {{ align-items: flex-start; flex-direction: column; }}
    .actions {{ width: 100%; justify-content: space-between; }}
  }}
</style>
</head>
<body>
<header>
  <h1>LearnGraph APK</h1>
  <p>旧版本与本机编译包。普通安卓手机优先下载 arm64；不确定架构时使用 universal 通用包。</p>
</header>
<main>
  <div class="notice">下载站 <a href="{html.escape(lan_url)}">{html.escape(lan_url)}</a>。安装前确认版本号；同包名版本降级可能需要先卸载新版本，卸载会清除本机应用数据。</div>
  <div class="toolbar" aria-label="版本筛选"><span>筛选</span>
    <button class="filter" type="button" data-filter="all" aria-pressed="true">全部</button>
    <button class="filter" type="button" data-filter="old" aria-pressed="false">旧版本</button>
  </div>
  {rows if rows else '<div class="empty">目录中没有 APK 文件</div>'}
</main>
<script>
  const buttons = [...document.querySelectorAll("[data-filter]")];
  buttons.forEach((button) => button.addEventListener("click", () => {{
    const oldOnly = button.dataset.filter === "old";
    buttons.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    document.querySelectorAll(".card").forEach((card) => {{
      card.hidden = oldOnly && card.dataset.old !== "true";
    }});
  }}));
</script>
</body>
</html>"""
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head:
            self.wfile.write(data)

    @staticmethod
    def _row(fname: str, size: int, mtime: float) -> str:
        size_txt = f"{size / 1024 / 1024:.1f} MB" if size > 1024 * 1024 else f"{size / 1024:.0f} KB"
        when = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        match = re.search(r"-v(\d+\.\d+\.\d+)-", fname)
        version = match.group(1) if match else "旧构建"
        old = bool(
            match
            and ApkDownloadHandler.current_version
            and tuple(map(int, version.split(".")))
            < tuple(map(int, ApkDownloadHandler.current_version.split(".")))
        )
        version_label = (
            f'<span class="version current">{html.escape(version)} · 当前</span>'
            if version == ApkDownloadHandler.current_version
            else f'<span class="version">{html.escape(version)}</span>'
        )
        return (
            f'<div class="card" data-old="{"true" if old else "false"}"><div class="info">'
            f'<div class="version-line">{version_label}<div class="name">{html.escape(fname)}</div></div>'
            f'<div class="meta">{size_txt} · {when}</div>'
            f'</div>'
            f'<div class="actions"><a class="hash" href="/sha256/{urllib.parse.quote(fname)}">SHA-256</a>'
            f'<a class="dl" href="{urllib.parse.quote(fname)}" download>下载</a></div></div>'
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
    parser.add_argument("--ip", default="", help="用于首页展示的局域网地址")
    args = parser.parse_args()

    base = os.path.dirname(os.path.abspath(__file__))
    apk_dir = os.path.abspath(args.dir) if args.dir else os.path.abspath(os.path.join(base, "..", "mobile"))

    # 类体无法直接引用外部局部变量（NameError），先设基类属性再继承
    ApkDownloadHandler.apk_dir = apk_dir
    ApkDownloadHandler.preferred_ip = (
        args.ip.split(",", 1)[0].strip()
        or _detect_lan_ip()
    )
    versions = [
        fname.removeprefix("LearnGraph-mobile-v").removesuffix("-native-arm64.apk")
        for fname in os.listdir(apk_dir)
        if re.fullmatch(r"LearnGraph-mobile-v\d+\.\d+\.\d+-native-arm64\.apk", fname)
    ]
    ApkDownloadHandler.current_version = max(
        versions,
        key=lambda value: tuple(map(int, value.split("."))),
        default="",
    )

    class Handler(ApkDownloadHandler):
        pass

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(("0.0.0.0", args.port), Handler) as httpd:
        print(f"LearnGraph APK 下载站启动: http://127.0.0.1:{args.port}/ (局域网: http://{ApkDownloadHandler.preferred_ip}:{args.port}/; 目录: {apk_dir})", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
