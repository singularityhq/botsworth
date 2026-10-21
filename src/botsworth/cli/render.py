"""CONTRACT: the `--table` output of the CLI (CLI-2).

GUARANTEES:
- `render_table(cmd, data)` returns lines until the 2 KB budget (`TABLE_BYTES`), then one line
  `... N more lines (use --json)`. The legacy commands keep their column layouts; the v4 commands print
  `key: value` lines and one line of scalar fields per list item.
- A v4 table shows the readable sibling (`<x>` of `<x>_ms`, `<x>_ms_h`, `ms_h` of a `time_split` part)
  and leaves out the raw number it belongs to (D50, `cli.timefmt`).
- A `TimeSplit` (`{parts: [...]}`, D54) prints as `key=[state/substate=duration ...]`; the substate `none` of
  state `none` is left out of the name.
- Output is for people: its text and layout are not stable (CLI-11).
DOES NOT: read the store, or decide what a command returns.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, is_dataclass
from typing import Any

TABLE_BYTES = 2048
_MS_KEYS = ("wall_ms", "active_ms", "busy_sum_ms", "dur_ms", "llm_ms", "tool_ms", "idle_ms")
_LINE_CHARS = 200
V4_COMMANDS = frozenset(
    ("status", "period", "days", "sessions", "facets", "search", "session", "turns", "turn", "detail", "find")
    + ("slow", "breakdown", "tasks")  # Q3b, CLI-23
)


def _cell(v: Any, key: str = "") -> str:
    if v is None:
        return "-"
    if isinstance(v, float) and "cost" in key:
        return f"${v:.4f}"
    if isinstance(v, int | float) and key in _MS_KEYS:
        return f"{v / 1000:.1f}s"
    if isinstance(v, dict):
        return " ".join(f"{k}={_cell(x, k)}" for k, x in v.items())
    return str(v)


def _row_line(row: dict[str, Any]) -> str:
    parts = []
    if "op" in row:
        op = row["op"]
        parts += [op.get("op_id", "?"), op.get("kind", ""), op.get("label", "")[:60]]
    if "key" in row:
        parts.append(_cell(row["key"]))
    if row.get("bucket") is not None:
        parts.append(time.strftime("%Y-%m-%d %H:%M", time.gmtime(row["bucket"] / 1000)))
    for kind in ("event", "step"):
        if kind in row:
            rec = row[kind]
            parts.append(" ".join(_cell(rec.get(k)) for k in ("kind", "verb", "target", "ts_ms") if k in rec))
    if row.get("metrics"):
        parts.append(_cell(row["metrics"]))
    if row.get("dist"):
        dist = row["dist"].items()
        parts.append(" ".join(f"{m}[p50={_cell(d['p50'], m)} p90={_cell(d['p90'], m)}]" for m, d in dist))
    if row.get("counts"):
        parts.append(f"n={row['counts']['n']}")
    if row.get("top_child"):
        parts.append(f"top_child={row['top_child'].get('op_id')}")
    return "  ".join(p for p in parts if p)


def _raw_time(k: str, d: dict[str, Any]) -> bool:
    """True for a raw time field whose readable sibling is in the same object."""
    return f"{k}_h" in d or (k.endswith("_ms") and k[: -len("_ms")] in d)


def _scalars(v: dict[str, Any], prefix: str = "", depth: int = 2) -> list[str]:
    out = []
    for k, x in v.items():
        if _raw_time(k, v):
            continue
        if _is_split(x):
            out.append(f"{prefix}{k}=[{_split_text(x)}]")
        elif isinstance(x, dict) and depth > 1:
            out += _scalars(x, f"{prefix}{k}.", depth - 1)
        elif not isinstance(x, dict | list):
            out.append(f"{prefix}{k}={_cell(x, k)}")
    return out


def _is_split(x: Any) -> bool:
    """A `TimeSplit`: `{parts: [{state, substate, ms}, ...]}` (D54)."""
    return isinstance(x, dict) and set(x) <= {"parts"} and isinstance(x.get("parts"), list)


def _split_text(x: dict[str, Any]) -> str:
    """`you/ask=5m 00s model=1m 02s`: each part's state, substate and readable duration."""
    parts = []
    for p in x["parts"]:
        name = p["state"] if p["substate"] in (None, p["state"]) else f"{p['state']}/{p['substate']}"
        parts.append(f"{name}={p.get('ms_h', p['ms'])}")
    return " ".join(parts)


def _v4_lines(data: Any) -> list[str]:
    """Scalars as `key: value`; each list of objects as a heading and one line per item."""
    lines = []
    for k, x in data.items():
        if _raw_time(k, data):
            continue
        if isinstance(x, list) and x and all(isinstance(i, dict) for i in x):
            lines.append(f"{k} ({len(x)}):")
            lines += [("  " + " ".join(_scalars(i)))[:_LINE_CHARS] for i in x]
        elif _is_split(x):
            lines.append(f"{k}: {_split_text(x)}"[:_LINE_CHARS])
        elif isinstance(x, dict):
            lines.append((f"{k}: " + " ".join(_scalars(x)))[:_LINE_CHARS])
        elif not isinstance(x, list):
            lines.append(f"{k}: {_cell(x, k)}"[:_LINE_CHARS])
    return lines


def _lines(cmd: str, data: Any) -> list[str]:
    if cmd in V4_COMMANDS:
        return _v4_lines(asdict(data) if is_dataclass(data) else data)
    if cmd == "paths":
        return [f"{k}: {v}" for k, v in data.items()]
    if cmd == "outline":
        return [
            "  ".join(
                _cell(r.get(k), k)
                for k in ("display_id", "verb", "kind", "target", "outcome", "dur_ms", "cost_usd")
                if r.get(k) is not None
            )
            for r in data["rows"]
        ]
    if cmd == "source":
        return [data["text"], f"-- {data['total_bytes']} bytes total, drift={data['drift']}"]
    if cmd == "show":
        op = data["op"]
        return [f"{k}: {_cell(v, k)}" for k, v in op.items() if v not in (None, "", 0)]
    if cmd == "steps":
        return [
            f"{s['step_id']}  {s['verb']}  {s['target']}  → {s['outcome']}  {_cell(s['dur_ms'], 'dur_ms')}"
            for s in data["steps"]
        ]
    if cmd == "timeline":
        return [_row_line({"op": op}) for op in [data["root"], *data["ops"]]]
    if isinstance(data, dict) and "rows" in data:
        lines = [_row_line(r) for r in data["rows"]]
        if data.get("scope_totals"):
            lines.insert(0, "total  " + _cell(data["scope_totals"]))
        return lines
    return [json.dumps(data, default=str)]


def render_table(cmd: str, data: Any, limit: int = TABLE_BYTES) -> str:
    """Lines until the byte budget; then a note on how many were left out."""
    out: list[str] = []
    used = 0
    lines = _lines(cmd, data)
    for i, line in enumerate(lines):
        size = len(line.encode()) + 1
        if used + size > limit - 64:
            out.append(f"... {len(lines) - i} more lines (use --json)")
            break
        out.append(line)
        used += size
    return "\n".join(out)
