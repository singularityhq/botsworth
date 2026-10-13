"""CONTRACT: Claude Code transcript lines and the requestId usage rule (design §4.4).

GUARANTEES:
- `read(path)` returns every JSON-object line as `Line(n, rec, h)` in file
  order: `n` 1-based, `h` = `pointer.content_hash` of the raw line bytes
  (newline included, as the resolver hashes them). Blank lines are skipped;
  unparsable or non-object lines are counted, never fatal.
- `select_final(group)` applies the session-viz-proto grouper rule to the
  assistant records of one requestId in file order: `<synthetic>` records
  dropped (all synthetic -> None); final = the last record with a
  `stop_reason`, else the last record with `cut_off = True`; a final record
  with zero usage loses to the records that carry usage (the rule is re-run
  over those). Usage is read from that ONE record, never summed.
- `tokens(usage)` maps one usage object to the canonical `*_tok` fields:
  `cache_write_tok` = the flat `cache_creation_input_tokens`; the 5m/1h tiers
  from `cache_creation`, or None for both when the tiers are absent or both
  zero while the flat total is positive (the Pricer then prices the flat
  total at the 5m rate); `reasoning_tok` from `thinking_tokens` (inside
  `out_tok`); `context_tokens` = in + cache_read + cache_write.
- `ts_ms` converts an ISO timestamp to integer epoch ms (None for none).
EXPECTS: Claude Code JSONL transcripts.
FAILURE BEHAVIOR: a malformed timestamp string raises ValueError (corrupt
  source, surfaced rather than guessed).
DOES NOT: attribute records to transcripts (attribution.py) or build ops.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from botsworth import pointer

SYNTHETIC = "<synthetic>"
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


@dataclass(slots=True)
class Line:
    n: int
    rec: dict[str, Any]
    h: str
    copy: bool = False  # uuid owned by an earlier transcript of the bundle (attribution.py)


def read(path: Path) -> tuple[list[Line], int]:
    lines: list[Line] = []
    bad = 0
    with path.open("rb") as f:
        for n, raw in enumerate(f, start=1):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                bad += 1  # FALLBACK-OK: build-plan §3 adapters FAILURE (counted, never fatal)
                continue
            if not isinstance(rec, dict):
                bad += 1
                continue
            lines.append(Line(n, rec, pointer.content_hash(raw)))
    return lines, bad


def ts_ms(value: str | None) -> int | None:
    if not value:
        return None  # FALLBACK-OK: design §4.6 time missing
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def message(rec: dict[str, Any]) -> dict[str, Any]:
    msg = rec.get("message")
    return (
        msg if isinstance(msg, dict) else {}
    )  # FALLBACK-OK: design §4.4 records without a message carry no call


def is_synthetic(rec: dict[str, Any]) -> bool:
    return message(rec).get("model") == SYNTHETIC


def _has_usage(rec: dict[str, Any]) -> bool:
    usage = message(rec).get("usage") or {}  # FALLBACK-OK: design §4.4 absent usage = zero-usage stub
    return any(usage.get(k) for k in USAGE_KEYS)


def _last_finished(group: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    finished = [r for r in group if message(r).get("stop_reason")]
    return (finished[-1], False) if finished else (group[-1], True)


def select_final(group: list[dict[str, Any]]) -> tuple[dict[str, Any], bool] | None:
    real = [r for r in group if not is_synthetic(r)]
    if not real:
        return None
    final, cut_off = _last_finished(real)
    if not _has_usage(final):
        with_usage = [r for r in real if _has_usage(r)]
        if with_usage:
            final, cut_off = _last_finished(with_usage)
    return final, cut_off


def tokens(usage: dict[str, Any]) -> dict[str, int | None]:
    flat = (
        usage.get("cache_creation_input_tokens") or 0
    )  # FALLBACK-OK: design §4.8 absent count = 0 in a present usage
    tiers = usage.get("cache_creation")
    t5 = t1 = None
    if isinstance(tiers, dict):
        t5 = tiers.get("ephemeral_5m_input_tokens") or 0  # FALLBACK-OK: design §4.8 tier present
        t1 = tiers.get("ephemeral_1h_input_tokens") or 0
        if t5 == 0 and t1 == 0 and flat:
            t5 = t1 = None  # tiers say nothing about a positive flat total: priced flat at 5m (§4.7)
    n_in = usage.get("input_tokens") or 0
    n_read = usage.get("cache_read_input_tokens") or 0
    return {
        "in_tok": n_in,
        "out_tok": usage.get("output_tokens") or 0,
        "cache_read_tok": n_read,
        "cache_write_tok": flat,
        "cache_write_5m_tok": t5,
        "cache_write_1h_tok": t1,
        "reasoning_tok": usage.get("thinking_tokens"),
        "context_tokens": n_in + n_read + flat,
    }
