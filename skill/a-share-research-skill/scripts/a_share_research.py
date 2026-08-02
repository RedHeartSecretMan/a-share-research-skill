#!/usr/bin/env python3
"""CLI entry point for the bundled A-share research runtime."""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from a_share_research.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
