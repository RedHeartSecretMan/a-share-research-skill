#!/usr/bin/env python3
"""Opt-in live diagnostic for close-source contracts; never run in normal CI."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY_ROOT / "skill" / "a-share-research-skill" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a_share_research.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["close", *sys.argv[1:]]))
