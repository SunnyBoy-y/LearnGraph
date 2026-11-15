"""Server-owned launcher. Original Skill files are never rewritten.

The sandbox public argv contract starts only at the chat workspace root.
This launcher changes cwd inside the already isolated process, not Docker's
workdir, preserving the root-only workdir/symlink boundary.
"""
LAUNCHER_BYTES = b'''from pathlib import Path
import os
import shutil
import sys

workspace = Path.cwd().resolve(strict=True)
root_path = Path(sys.argv[1])
script_path = Path(sys.argv[2])
if root_path.is_absolute() or script_path.is_absolute() or ".." in root_path.parts or ".." in script_path.parts:
    raise SystemExit("Invalid Skill execution path")
root = (workspace / root_path).resolve(strict=True)
script = (root / script_path).resolve(strict=True)
if not root.is_relative_to(workspace) or not script.is_relative_to(root) or not script.is_file():
    raise SystemExit("Skill execution path escapes its package")
# Never use a symlink as the package cwd; container workdir remains workspace.
for parent in [workspace / root_path, *(workspace / root_path).parents]:
    if parent == workspace:
        break
    if parent.is_symlink():
        raise SystemExit("Skill package root must not contain symlinks")
env = dict(os.environ)
env["LEARNGRAPH_SKILL_ROOT"] = str(root)
env["LEARNGRAPH_WORKSPACE_ROOT"] = str(workspace)
env["LEARNGRAPH_OUTPUT_DIR"] = str(workspace / "outputs")
os.chdir(root)
if script.suffix.lower() == ".py":
    env["PYTHONPATH"] = str(root)
    executable = sys.executable
elif script.suffix.lower() in {".js", ".mjs", ".cjs"}:
    executable = shutil.which("node")
    if not executable:
        raise SystemExit("Node runtime is unavailable")
else:
    raise SystemExit("Unsupported Skill script type")
os.execve(executable, [executable, str(script), *sys.argv[3:]], env)
'''
