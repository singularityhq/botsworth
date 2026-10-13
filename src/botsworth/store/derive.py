"""CONTRACT: pure derivations of the store fold (DM-10, DM-11, DM-24, DM-41, DM-44, DM-45).

GUARANTEES:
- `first_ask_ok(label)` (DM-11): after whitespace collapse the label has >= 4 words, does not start with
  `/`, `<`, `[`, `~/` or `#`, and is not a UUID prefix (`^[0-9a-f]{8}-[0-9a-f]{4}-`). None is not a first ask.
- `title(adapter_title, adapter_source, ask_labels)` (DM-10) -> `(display_title, title_source)`. First
  non-empty wins: the adapter title with source `rename`, then with source `generated`; the first label of
  `ask_labels` (human asks in time order) that passes `first_ask_ok` -> `first_ask`; the adapter title with
  source `thread_title` when it passes `first_ask_ok` -> `first_ask` (D43); else `(None, "fallback")`.
  Display titles are whitespace-collapsed and clipped to 160 characters (DM-9).
- `display_ix(turns)` (DM-24): `turns` = `(turn_kind, start_ms, seq)` of the main turns, any order; returns
  one entry per input position: 1, 2, 3 ... for kinds `human`/`message` ordered by `(start_ms, seq)`,
  None for any other kind. A null `start_ms` takes the start of the nearest earlier turn by `seq` that has
  one (before any: the earliest possible), so it keeps its place in `seq` order.
- `activity_bins(first_ms, last_ms, calls)` (DM-44) -> `(bin_ms, tokens)`; `calls` = `(start_ms, tok_total)`
  pairs. `bin_ms` = max(60 000, ceil(span / 96) rounded up to a whole minute); bins = ceil(span / bin_ms)
  (1 for a zero span); a null `tok_total` adds 0; a call that starts exactly at `last_ms` falls in the last
  bin. No calls -> `(bin_ms, [])`.
- `attr_walk(rows)` (DM-45) -> `{op_id: turn_rowid | None}` for every owned leaf (`llm_call`/`tool_call` with
  `duplicate_of` None): walk up `parent` to the nearest main turn (`kind == "turn"`, `agent_id` None) ->
  its `rowid`; at an engagement continue from its `caused_by` tool_call; an engagement with `caused_by`
  None, or reaching the session root, gives None.
- `bucket15(ms)` (DM-41) -> `ms - ms % 900000`: the epoch-aligned 15-minute bucket start; no zone is used.
EXPECTS: `attr_walk` rows carry `op_id, rowid, kind, parent, caused_by, agent_id, duplicate_of`.
FAILURE BEHAVIOR: `attr_walk` raises `AttributionError` when a walk revisits an engagement, when a
  `caused_by` is not a `tool_call` or is not among the rows, or when a parent is missing.
DOES NOT: read or write the store, compute time splits (`timesplit`) or projects.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from botsworth.store import refs

MAX_BINS = 96
MIN_BIN_MS = 60_000
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-")
_NOT_ASK_PREFIX = ("/", "<", "[", "~/", "#")
_MIN_WORDS = 4
BUCKET_MS = 900_000  # DM-41: the one rollup_time bucket size, 15 minutes


class AttributionError(ValueError):
    """The attributed-turn walk of DM-45 cannot end at a turn or at a session-level leaf."""


def _collapse(text: str) -> str:
    return " ".join(text.split())


def first_ask_ok(label: str | None) -> bool:
    if label is None:
        return False
    text = _collapse(label)
    return len(text.split()) >= _MIN_WORDS and not text.startswith(_NOT_ASK_PREFIX) and not _UUID.match(text)


def title(
    adapter_title: str | None, adapter_source: str | None, ask_labels: Iterable[str | None]
) -> tuple[str | None, str]:
    named = _collapse(adapter_title) if adapter_title else ""
    if named and adapter_source in ("rename", "generated"):
        return refs.clip_preview(named), adapter_source
    for label in ask_labels:
        if first_ask_ok(label):
            return refs.clip_preview(_collapse(label)), "first_ask"
    if named and adapter_source == "thread_title" and first_ask_ok(named):
        return refs.clip_preview(named), "first_ask"
    return None, "fallback"


def display_ix(turns: Sequence[tuple[str | None, int | None, int]]) -> list[int | None]:
    floor = -(2**62)
    eff: dict[int, int] = {}
    last = floor
    for i in sorted(range(len(turns)), key=lambda k: turns[k][2]):
        start = turns[i][1]
        last = start if start is not None else last
        eff[i] = last
    numbered = sorted(
        (i for i, t in enumerate(turns) if t[0] in ("human", "message")), key=lambda i: (eff[i], turns[i][2])
    )
    out: list[int | None] = [None] * len(turns)
    for n, i in enumerate(numbered, 1):
        out[i] = n
    return out


def activity_bins(
    first_ms: int, last_ms: int, calls: Iterable[tuple[int, int | None]]
) -> tuple[int, list[int]]:
    span = max(last_ms - first_ms, 0)
    per_bin = -(-span // MAX_BINS)
    bin_ms = max(MIN_BIN_MS, -(-per_bin // MIN_BIN_MS) * MIN_BIN_MS)  # whole minutes, rounded up
    n_bins = max(1, -(-span // bin_ms))
    bins = [0] * n_bins
    seen = False
    for start, tok in calls:
        seen = True
        bins[min((start - first_ms) // bin_ms, n_bins - 1)] += tok or 0
    return bin_ms, bins if seen else []


def attr_walk(rows: Iterable[Mapping]) -> dict[str, int | None]:
    # No time-precedence check on `caused_by` (DM-45 wording deviation): measured on the v6 store, 95 of 6,527
    # engagements have a cause whose start_ms is later than the engagement's (62 SendMessage/content_hash,
    # 33 Agent/id). The walk ends by revisit detection and by the kind check.
    by_id = {r["op_id"]: r for r in rows}
    resolved: dict[str, int | None] = {}  # container op_id -> the turn its leaves belong to

    def answer(op_id: str) -> int | None:
        path: list[str] = []
        engagements: set[str] = set()
        cur: str | None = op_id
        result: int | None = None
        while cur is not None:
            if cur in resolved:
                result = resolved[cur]
                break
            r = by_id.get(cur)
            if r is None:
                raise AttributionError(f"{cur}: op not in the bundle")
            if r["kind"] == "turn" and r["agent_id"] is None:
                result = r["rowid"]
                path.append(cur)
                break
            if r["kind"] == "engagement":
                if cur in engagements:
                    raise AttributionError(f"{cur}: engagement revisited by the attributed-turn walk")
                engagements.add(cur)
                path.append(cur)
                cause = r["caused_by"]
                if cause is None:
                    break
                if cause not in by_id or by_id[cause]["kind"] != "tool_call":
                    raise AttributionError(f"{cur}: caused_by {cause} is not a tool_call of the bundle")
                cur = cause
                continue
            path.append(cur)
            cur = r["parent"]  # a session root has no parent: the walk ends with no turn
        for p in path:
            resolved[p] = result
        return result

    return {
        r["op_id"]: answer(r["parent"])
        for r in by_id.values()
        if r["kind"] in ("llm_call", "tool_call") and r["duplicate_of"] is None and r["parent"] is not None
    }


def bucket15(ms: int) -> int:
    return ms - ms % BUCKET_MS
