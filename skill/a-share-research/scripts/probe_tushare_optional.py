#!/usr/bin/env python3
"""Run the opt-in, redacted Tushare capability probe."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SKILL_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_SCRIPTS))

from a_share_research.identity_sources import UrlLibTransport  # noqa: E402
from a_share_research.tushare_optional import TushareOptionalProbe  # noqa: E402


def main() -> int:
    result = TushareOptionalProbe(transport=UrlLibTransport()).probe()
    print(json.dumps(result.to_public_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
