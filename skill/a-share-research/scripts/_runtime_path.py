"""Locate the dependency runtime installed inside this Skill artifact."""

from __future__ import annotations

import sys
from pathlib import Path


class UnsafeBundledRuntimeError(RuntimeError):
    """The artifact-local dependency directory is not a real local directory."""


def bundled_runtime_path() -> Path:
    """Return the local runtime path, rejecting links or path escapes."""

    skill_root = Path(__file__).resolve().parents[1]
    runtime = Path(__file__).resolve().parents[1] / ".runtime"
    if runtime.is_symlink():
        raise UnsafeBundledRuntimeError("bundled runtime must not be a symlink")
    if runtime.exists():
        if not runtime.is_dir():
            raise UnsafeBundledRuntimeError("bundled runtime must be a directory")
        try:
            runtime.resolve(strict=True).relative_to(skill_root.resolve(strict=True))
        except (OSError, ValueError) as error:
            raise UnsafeBundledRuntimeError(
                "bundled runtime must remain inside the Skill artifact"
            ) from error
        if runtime.resolve(strict=True).parent != skill_root.resolve(strict=True):
            raise UnsafeBundledRuntimeError(
                "bundled runtime must be a direct Skill child"
            )
    return runtime


def add_bundled_runtime() -> bool:
    try:
        runtime = bundled_runtime_path()
    except UnsafeBundledRuntimeError:
        return False
    if runtime.is_dir():
        sys.path.insert(0, str(runtime))
        return True
    return False
