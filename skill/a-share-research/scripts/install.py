#!/usr/bin/env python3
"""Install locked core dependencies into this copied Skill and check them."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _runtime_path import UnsafeBundledRuntimeError, bundled_runtime_path


def _report_health(skill_root: Path) -> int:
    health = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(skill_root / "scripts" / "installation_health.py"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    sys.stdout.write(health.stdout)
    sys.stderr.write(health.stderr)
    return health.returncode


def main() -> int:
    if sys.version_info < (3, 12):
        sys.stderr.write("error: Python 3.12 or later is required\n")
        return 1
    skill_root = Path(__file__).resolve().parents[1]
    requirements = skill_root / "requirements.lock"
    try:
        runtime = bundled_runtime_path()
    except UnsafeBundledRuntimeError:
        return _report_health(skill_root)
    runtime.mkdir(exist_ok=True)
    try:
        runtime = bundled_runtime_path()
    except UnsafeBundledRuntimeError:
        return _report_health(skill_root)
    installed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--quiet",
            "--no-input",
            "--no-warn-script-location",
            "--upgrade",
            "--no-deps",
            "--requirement",
            str(requirements),
            "--target",
            str(runtime),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if installed.returncode != 0 or installed.stderr.strip():
        sys.stderr.write("error: core dependency installation failed\n")
        return 1
    return _report_health(skill_root)


if __name__ == "__main__":
    raise SystemExit(main())
