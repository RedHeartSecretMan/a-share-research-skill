#!/usr/bin/env python3
"""Report installed core runtime health without contacting any data source."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _runtime_path import add_bundled_runtime  # noqa: E402

add_bundled_runtime()


def _incomplete_health() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": "installation_incomplete",
        "dependencies": [],
        "operations": [],
        "source_policy": {
            "source_accessibility": "not_checked",
            "credentials": "not_checked",
            "evidence_adoption": "not_checked",
        },
    }


def main() -> int:
    try:
        from a_share_research.installation_health import check_installation

        health = check_installation()
    except Exception:
        health = _incomplete_health()
    sys.stdout.write(
        json.dumps(
            health,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    return 0 if health["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
