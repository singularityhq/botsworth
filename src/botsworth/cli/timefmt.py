"""CONTRACT: the time rendering of the v4 CLI output (D50, Q31 option A).

GUARANTEES:
- `render(data, tz)` returns a copy of the output dict (`asdict` of a wire type) with, at every depth:
  each timestamp field `<x>_ms` in `ISO_FIELDS` followed by a sibling `<x>` = ISO-8601 with the offset of
  `tz`, second precision (`2026-10-07T01:02:29+05:30`); each duration field in `DURATION_FIELDS`
  (including `ms`, the duration of a `time_split` part) followed by `<field>_h` = `human(ms)`. A null
  stays null. The raw `*_ms` values are kept. The top level gains `"tz": tz` as its first key.
- `human(ms)`: `0s` under 1 s; `45s`; `12m 05s`; `2h 15m`; `3d 4h`. It truncates to the shown units; only
  the seconds of `12m 05s` are zero padded.
- The three sets are explicit lists, not a guess by name suffix: a test walks every `*_ms` field of
  `botsworth.query.wire` and fails on one that is in none of `ISO_FIELDS`, `DURATION_FIELDS`, `NESTED_FIELDS`.
EXPECTS: `tz` is an IANA zone name; `data` has no `tz` key and no key equal to a sibling this module adds.
FAILURE BEHAVIOR: a sibling name already in the output raises `ValueError` (a wire change to escalate, not to
  work around).
DOES NOT: read the store, change a wire type, or touch a field it does not list.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

ISO_FIELDS = frozenset(
    ("last_ingest_ms", "started_ms", "ended_ms", "start_ms", "end_ms", "from_ms", "to_ms", "tail_from_ms")
)
DURATION_FIELDS = frozenset(
    (
        "model_ms", "ms", "wall_ms", "lost_ms", "active_ms", "dur_ms", "waiting_ms",
        "run_ms", "ttft_ms", "ttft_ms_mean", "max_silence_ms", "p50_ms", "p90_ms", "max_ms", "bin_ms",
    )
)  # fmt: skip
NESTED_FIELDS = frozenset(("state_ms",))  # a `TimeSplit` object, not a number


def human(ms: float) -> str:
    s = int(ms // 1000)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m"
    d, h = divmod(h, 24)
    return f"{d}d {h}h"


def iso(ms: int, tz: str) -> str:
    return datetime.fromtimestamp(ms // 1000, ZoneInfo(tz)).isoformat(timespec="seconds")


def _node(obj: Any, tz: str) -> Any:
    if isinstance(obj, list):
        return [_node(x, tz) for x in obj]
    if not isinstance(obj, dict):
        return obj
    result: dict[str, Any] = {}
    for k, raw in obj.items():
        v = result[k] = _node(raw, tz)
        is_number = isinstance(v, int | float) and not isinstance(v, bool)
        if k in ISO_FIELDS and (v is None or is_number):
            _add(result, obj, k[: -len("_ms")], None if v is None else iso(v, tz))
        elif k in DURATION_FIELDS and (v is None or is_number):
            _add(result, obj, f"{k}_h", None if v is None else human(v))
    return result


def _add(result: dict[str, Any], original: dict[str, Any], key: str, value: Any) -> None:
    if key in original:
        raise ValueError(f"output already has the key {key!r} that the CLI time rendering adds")
    result[key] = value


def render(data: dict[str, Any], tz: str) -> dict[str, Any]:
    if "tz" in data:
        raise ValueError("output already has the key 'tz' that the CLI time rendering adds")
    return {"tz": tz} | _node(data, tz)
