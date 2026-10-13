"""CONTRACT: find omp bundles under the sessions root.

GUARANTEES:
- One `BundleRef` per top-level transcript `<root>/<project-dir>/<name>.jsonl`;
  `files` = that transcript first, then every nested `*.jsonl` under
  `<root>/<project-dir>/<name>/` (child agents, any depth), sorted by path.
- `bundle_id` = `session_id` = the first `session` header's `id`.
- Refs are ordered by (header timestamp, path).
EXPECTS: `root` is omp's sessions directory (`pointer.DEFAULT_ROOTS["omp"]`).
FAILURE BEHAVIOR: a top-level file without a session header is skipped (it
  cannot be named); nothing else is opened beyond the header.
DOES NOT: parse records beyond the header; link children to causes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from botsworth.adapters import BundleRef
from botsworth.adapters.omp.records import iso_ms


def _header(path: Path) -> dict | None:
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and r.get("type") == "session" and isinstance(r.get("id"), str):
                return r
    return None


def discover(root: Path) -> Iterator[BundleRef]:
    found = []
    for top in sorted(Path(root).glob("*/*.jsonl")):
        header = _header(top)
        if header is None:
            continue  # FALLBACK-OK: CONTRACT failure behavior, an unnamed file is not a bundle
        nested = sorted(top.with_suffix("").rglob("*.jsonl")) if top.with_suffix("").is_dir() else []
        ref = BundleRef(provider="omp", bundle_id=header["id"], session_id=header["id"], files=(top, *nested))
        found.append(((iso_ms(header.get("timestamp")) or 0, str(top)), ref))
    for _, ref in sorted(found, key=lambda x: x[0]):
        yield ref
