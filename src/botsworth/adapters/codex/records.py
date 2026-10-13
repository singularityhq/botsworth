"""CONTRACT: Codex rollout records -> timestamps, token counts, call identity (design §4.4).

GUARANTEES:
- `read_lines(path)` returns the file's complete lines (bytes, 1-based by list
  index + 1) and whether a trailing partial line was dropped.
- `generation(rec)` classifies the first record: `v0` (no `type`/`payload`;
  no timestamps or token events, skipped), `v2` (records carry `ordinal`), else `v1`.
- `TokenTracker.feed(total, last)` applies the extract-codex rule: a
  `token_count` is one llm_call only when `total_token_usage` changes; its
  tokens are `last_token_usage`; a decrease of any total starts a new segment
  (`reset`). Child threads inherit the parent's total as baseline, so usage
  is the sum of `last` values, never the final total.
- `tokens(usage)` maps a Codex usage object to canonical `*_tok` fields:
  `in_tok = input_tokens - cached_input_tokens`, `cache_read_tok = cached`,
  `cache_write_tok = cache_write_input_tokens` (0 when absent; tiers missing,
  design §4.8), `out_tok = output_tokens` (reasoning included),
  `reasoning_tok`, `context_tokens = input_tokens + cache_write`.
- `fingerprint(total, last)` is the occurrence key of a derived call: a hash of
  both usage objects, identical in a fork copy and its source.
EXPECTS: usage objects shaped like `token_count.info.*_token_usage`.
FAILURE BEHAVIOR: none here; malformed lines are counted by the caller.
DOES NOT: price; build ops.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")


def ms(ts: str | None) -> int | None:
    if not ts:
        return None
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)


def read_lines(path: Path) -> tuple[list[bytes], bool]:
    lines = path.read_bytes().split(b"\n")
    partial = lines.pop() != b""  # the last element is b"" iff the file ends with a newline
    return lines, partial


def generation(rec: dict[str, Any]) -> str:
    if "type" not in rec or "payload" not in rec:
        return "v0"
    return "v2" if "ordinal" in rec else "v1"


def _vec(usage: dict[str, Any]) -> dict[str, int]:
    return {k: usage.get(k) or 0 for k in FIELDS}  # FALLBACK-OK: design §4.4 absent counter = 0


class TokenTracker:
    def __init__(self) -> None:
        self.prev: dict[str, int] | None = None

    def feed(self, total: dict[str, Any], last: dict[str, Any]) -> str:
        """-> "dup" (no change), "call", or "reset" (a decrease: new segment)."""
        cur = _vec(total)
        if self.prev is not None and cur == self.prev:
            return "dup"
        kind = "call"
        if self.prev is not None and any(cur[k] < self.prev[k] for k in FIELDS):
            kind = "reset"
        self.prev = cur
        return kind


def tokens(usage: dict[str, Any]) -> dict[str, int]:
    u = _vec(usage)
    write = usage.get("cache_write_input_tokens") or 0  # FALLBACK-OK: design §4.8 Codex write 0 so far
    return {
        "in_tok": u["input_tokens"] - u["cached_input_tokens"],
        "cache_read_tok": u["cached_input_tokens"],
        "cache_write_tok": write,
        "out_tok": u["output_tokens"],
        "reasoning_tok": u["reasoning_output_tokens"],
        "context_tokens": u["input_tokens"] + write,
    }


def fingerprint(total: dict[str, Any], last: dict[str, Any]) -> str:
    data = json.dumps([_vec(total), _vec(last)], sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()[:16]
