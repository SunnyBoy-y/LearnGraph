"""LearnGraph desktop sidecar entry.

Single packaged executable serving both roles:
    LearnGraph-Service.exe --role api --port 52683
    LearnGraph-Service.exe --role preview --port 52684

The desktop Supervisor spawns this with explicit --role/--port. The api role
also locates the built frontend (PyInstaller resource dir, onedir sibling, or
repo checkout) and points LEARNGRAPH_FRONTEND_DIST at it so FastAPI serves the
SPA. Requires no Python/Node on the user machine once packaged.
"""

from __future__ import annotations

import argparse
import os
import sys


def _frontend_dist_dir() -> str | None:
    # 1) PyInstaller bundled resource (spec datas).
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = os.path.join(meipass, "frontend-dist")
        if os.path.isdir(candidate):
            return candidate
    # 2) onedir sibling (packaged layout: LearnGraph-Service/frontend-dist).
    here = os.path.dirname(os.path.abspath(sys.executable))
    candidate = os.path.join(here, "frontend-dist")
    if os.path.isdir(candidate):
        return candidate
    # 3) repo checkout (source debug mode).
    candidate = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend",
        "dist",
    )
    if os.path.isdir(candidate):
        return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="LearnGraph desktop sidecar")
    parser.add_argument("--role", choices=["api", "preview"], default="api")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.role == "preview":
        target = "app.preview:preview_app"
    else:
        target = "app.main:app"
        dist = _frontend_dist_dir()
        if dist:
            os.environ.setdefault("LEARNGRAPH_FRONTEND_DIST", dist)
        else:
            print("[sidecar] warning: frontend dist not found; UI will 404", flush=True)

    # Workers must stay 1: SQLite + embedded schedulers are single-process.
    import uvicorn

    uvicorn.run(
        target,
        host="127.0.0.1",
        port=args.port,
        workers=1,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
