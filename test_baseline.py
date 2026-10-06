from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def run_script(script: str, workdir: Path) -> str:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, str(ROOT / script)],
        cwd=workdir,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout
    return result.stdout


def test_core_workflow(tmp_path: Path) -> None:
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    core_output = run_script("test_unit.py", core_dir)
    assert "所有测试通过" in core_output, core_output


def test_group_allocation_workflow(tmp_path: Path) -> None:
    group_dir = tmp_path / "group"
    group_dir.mkdir()
    group_output = run_script("test_group_allocation.py", group_dir)
    assert "所有测试通过" in group_output, group_output
