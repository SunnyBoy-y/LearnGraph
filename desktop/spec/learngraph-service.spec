# -*- mode: python ; coding: utf-8 -*-
# LearnGraph Windows sidecar — PyInstaller onedir 打包
#
# 产物：dist/LearnGraph-Service/LearnGraph-Service.exe，同目录含 frontend-dist/。
# 同一入口 --role api|preview 分派两个进程；用户机器无需 Python/Node。
# 构建命令（在仓库根或 backend 内）：
#   backend/.venv/Scripts/python.exe -m PyInstaller desktop/spec/learngraph-service.spec --noconfirm

import PyInstaller.utils.hooks

from pathlib import Path

SPEC_DIR = Path(SPECPATH).resolve()                # <repo>/desktop/spec
ROOT = SPEC_DIR.parents[1] / "backend"             # parents: [0]=desktop, [1]=repo
FRONTEND_DIST = SPEC_DIR.parents[1] / "frontend" / "dist"

# uvicorn loads "app.main:app" / "app.preview:preview_app" by string; the
# static import chain from those modules is then tracked by PyInstaller.
# Collect the whole app package to cover lazy/dynamic imports (providers,
# skills, routers) without guessing.
app_modules = PyInstaller.utils.hooks.collect_submodules("app")

hiddenimports = [
    "app.main",
    "app.preview",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    # keyring picks its Windows backend dynamically.
    "keyring.backends.Windows",
    *app_modules,
]

a = Analysis(
    [str(ROOT.parent / "desktop" / "service_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Path-read resources must be packaged explicitly.
        (str(ROOT / "app" / "skills"), "app/skills"),
        (str(ROOT / "sandbox"), "sandbox"),
        (str(FRONTEND_DIST), "frontend-dist"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "notebook", "jupyter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LearnGraph-Service",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LearnGraph-Service",
)
