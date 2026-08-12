#!/usr/bin/env python3
"""Repository-only entry point for bundle and direct maintenance tests."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY_ROOT / "skill" / "a-share-research" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.cli import _maintenance_main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(_maintenance_main())
