"""Test helper: a v4 CLI output without the readable time that `cli.timefmt` adds (D50)."""

from __future__ import annotations

from typing import Any

from botsworth.cli.timefmt import ISO_FIELDS


def plain(obj: Any) -> Any:
    """`obj` without `tz` at the top, the ISO siblings of timestamp fields and the `_h` siblings."""
    if isinstance(obj, list):
        return [plain(x) for x in obj]
    if not isinstance(obj, dict):
        return obj
    drop = {f"{k}_h" for k in obj} | {k[: -len("_ms")] for k in obj if k in ISO_FIELDS}
    return {k: plain(v) for k, v in obj.items() if k not in drop}


def plain_top(obj: dict[str, Any]) -> Any:
    return plain({k: v for k, v in obj.items() if k != "tz"})
