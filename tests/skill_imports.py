from __future__ import annotations

import sys
from pathlib import Path


def add_skill_scripts_to_path() -> None:
    scripts = (
        Path(__file__).resolve().parents[1] / "skill" / "a-share-research" / "scripts"
    )
    sys.path.insert(0, str(scripts))
