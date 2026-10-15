"""Private bounded BSE document parser; no source text leaves the process."""

from __future__ import annotations

import contextlib
import importlib
import importlib.metadata
import io
import json
import logging
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    scripts = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(scripts))
    from _runtime_path import add_bundled_runtime, bundled_runtime_path
    from a_share_research.bse_membership_event import (
        MAX_PDF_BYTES,
        MAX_PDF_PAGES,
        MAX_TEXT_BYTES,
        _ProofFailure,
        _parse_material_pages,
    )

    if len(sys.argv) != 4 or not add_bundled_runtime():
        return 1
    runtime = bundled_runtime_path()
    if not any(
        d.metadata.get("Name", "").lower() == "pypdf" and d.version == "6.10.2"
        for d in importlib.metadata.distributions(path=[str(runtime)])
    ):
        return 1
    logging.disable(logging.CRITICAL)
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        library: Any = importlib.import_module("pypdf")
        if not Path(library.__file__).resolve().is_relative_to(runtime.resolve()):
            return 1
        payload = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
        if len(payload) > MAX_PDF_BYTES or not payload.startswith(b"%PDF-"):
            return 1
        reader = library.PdfReader(io.BytesIO(payload), strict=True)
        if reader.is_encrypted:
            return 1
        pages: list[str] = []
        size = 0
        for index, page in enumerate(reader.pages):
            if index >= MAX_PDF_PAGES:
                break
            text = page.extract_text(extraction_mode="layout")
            size += len(text.encode("utf-8"))
            if size > MAX_TEXT_BYTES:
                return 1
            pages.append(text)
        try:
            fields = _parse_material_pages(sys.argv[1], sys.argv[2], sys.argv[3], pages)
        except _ProofFailure as error:
            fields = {"failure": error.code}
    sys.stdout.write(json.dumps(fields, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(1) from None
