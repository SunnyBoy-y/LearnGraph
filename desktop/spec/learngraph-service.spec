# -*- mode: python ; coding: utf-8 -*-
# LearnGraph Windows sidecar — PyInstaller onedir 打包蓝图（Phase 1，尚未实际构建）
#
# 说明：
#   - 这是打包蓝图，实际构建需在冻结的 Python 3.11/3.12 venv 中完成（本机 3.14
#     仅用于开发验证）；依赖以 backend/uv.lock 为准。
#   - 产物：dist/LearnGraph-Service/LearnGraph-Service.exe，同目录含
#     frontend-dist/ 与 licenses/。同一入口 --role api|preview 分派两个进程。
#   - 按需调整 hiddenimports（uvicorn 动态导入、providers 延迟加载等）。
#   - 构建命令（在 backend 内）：
#       python -m PyInstaller desktop/spec/learngraph-service.spec --noconfirm

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]           # backend/
FRONTEND_DIST = ROOT.parent / "frontend" / "dist"    # 生产由 LEARNGRAPH_FRONTEND_DIST 指定

a = Analysis(
    [str(ROOT.parent / "desktop" / "service_entry.py")],  # 后续提供 --role 入口模块
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # 按路径读取的资源必须显式打包
        (str(ROOT / "app" / "skills"), "app/skills"),
        (str(ROOT / "sandbox"), "sandbox"),
        (str(FRONTEND_DIST), "frontend-dist"),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        # TODO: 冻结后端完整依赖后，按 pylint/collect_submodules 结果补全
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "notebook"],
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
