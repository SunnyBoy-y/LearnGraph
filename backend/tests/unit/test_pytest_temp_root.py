"""Exercise tmp_path setup in a fresh checkout with no ignored temp directory."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize("explicit_basetemp", [False, True])
def test_tmp_path_in_clean_checkout(tmp_path, explicit_basetemp):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    shutil.copyfile(Path(__file__).parents[1] / "conftest.py", checkout / "conftest.py")
    (checkout / "test_scratch.py").write_text(
        "def test_scratch(tmp_path):\n"
        "    target = tmp_path / 'probe.txt'\n"
        "    target.write_text('ok')\n"
        "    assert target.read_text() == 'ok'\n",
        encoding="utf-8",
    )
    command = [sys.executable, "-m", "pytest", "-q", f"--confcutdir={checkout}"]
    if explicit_basetemp:
        command.append(f"--basetemp={checkout / 'explicit-tmp'}")
    result = subprocess.run(
        command, cwd=checkout, capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / ("explicit-tmp" if explicit_basetemp else ".pytest-tmp")).is_dir()
    assert (checkout / ".pytest-tmp").exists() is not explicit_basetemp
