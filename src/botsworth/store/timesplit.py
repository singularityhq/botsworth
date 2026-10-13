"""CONTRACT: pure time-split math for sessions and main turns (DM-40, DM-50..DM-53).

Intervals are `(start_ms, end_ms)` with `end_ms >= start_ms`. `turns` are the main turns by start:
`(rowid, start_ms, end_ms, turn_kind)`.

GUARANTEES:
- DM-50: inside the wall (the union of `wall_segments`) each instant has one state by priority:
  `model` (a main llm_call covers it) > `tool` (a main tool_call, async included) > `agents` (an agent
  leaf covers it) > `none`. Intervals outside the wall never count.
- DM-51: `Split.model_ms/tool_ms/agents_ms/none_ms` sum to the wall. A turn's window is
  `[start, next main turn start)`, the last one ends at the wall end; `Split.before_first` is the part of the
  wall before the first turn. Per state, `before_first` plus all turn windows equals the session figure.
- DM-53: `TurnFigures.span_*` count the states inside the turn's own `[start_ms, end_ms]` (not widened over
  agents that end later). `span_wall_ms = end - start`. The four span states always sum to it. D48: time
  in the span outside the wall (a resume gap between segments) counts as `span_none_ms`. DM-51 figures and
  the per-turn windows are unchanged, and DM-52 stretches never include a resume gap.
- DM-52: `Split.stretches` holds every maximal run of state `none` or `agents`, cut at main-turn starts,
  each part kept when >= `MIN_STRETCH_MS`. `turn_rowid` = the turn whose window holds the part start (None
  before the first turn). `next_turn_kind` = kind of the turn starting exactly at the part end, else None.
- DM-40: `max_silence` = longest sub-interval of `[start, end]` with no timestamp from `ts_sorted`; the
  call's own start and end count as timestamps.
EXPECTS: no inverted interval; `turns` sorted by start; `ts_sorted` ascending. Turns that share a start: the
last of them holds the window, the stretches and `next_turn_kind`; the earlier ones get an empty window.
FAILURE BEHAVIOR: a violated EXPECTS raises AssertionError.
DOES NOT: read or write the store; decide which ops are main, agent or tool (the caller does); store
`ts_list` (DM-13); label a stretch "waiting for you" (readers use `next_turn_kind`).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

from botsworth.store.intervals import Interval, merge

MIN_STRETCH_MS = 60_000
STATES = ("model", "tool", "agents", "none")


class Stretch(NamedTuple):
    turn_rowid: int | None
    state: str
    start_ms: int
    end_ms: int
    next_turn_kind: str | None


@dataclass
class Figures:
    model_ms: int = 0
    tool_ms: int = 0
    agents_ms: int = 0
    none_ms: int = 0

    def add(self, state: str, ms: int) -> None:
        setattr(self, f"{state}_ms", getattr(self, f"{state}_ms") + ms)


@dataclass
class TurnFigures:
    window: Figures = field(default_factory=Figures)
    span_wall_ms: int = 0
    span_model_ms: int = 0
    span_tool_ms: int = 0
    span_agents_ms: int = 0
    span_none_ms: int = 0


@dataclass
class Split:
    model_ms: int
    tool_ms: int
    agents_ms: int
    none_ms: int
    before_first: Figures
    turns: dict[int, TurnFigures]
    stretches: list[Stretch]


def _covers(merged: list[Interval], starts: list[int], t: int) -> bool:
    """True when the half-open instant [t, t+) lies in the merged union (t is a segment start)."""
    i = bisect_right(starts, t) - 1
    return i >= 0 and merged[i][0] <= t < merged[i][1]


def _runs(
    wall: list[Interval], llm: list[Interval], tool: list[Interval], agents: list[Interval]
) -> list[tuple[int, int, str]]:
    """Elementary state runs inside the wall, adjacent equal states joined."""
    sets = [(m, [a for a, _ in m]) for m in (llm, tool, agents)]
    wall_starts = [a for a, _ in wall]
    bounds = sorted({t for m in (wall, llm, tool, agents) for a, b in m for t in (a, b)})
    out: list[tuple[int, int, str]] = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        if not _covers(wall, wall_starts, a):
            continue
        state = "none"
        for (m, s), name in zip(sets, ("model", "tool", "agents"), strict=True):
            if _covers(m, s, a):
                state = name
                break
        if out and out[-1][2] == state and out[-1][1] == a:
            out[-1] = (out[-1][0], b, state)
        else:
            out.append((a, b, state))
    return out


def split(
    wall_segments: Iterable[Interval],
    main_llm: Iterable[Interval],
    main_tool: Iterable[Interval],
    agent_leaf: Iterable[Interval],
    turns: Sequence[tuple[int, int, int, str | None]],
) -> Split:
    wall = merge(wall_segments)
    runs = _runs(wall, merge(main_llm), merge(main_tool), merge(agent_leaf))
    starts = [t[1] for t in turns]
    assert starts == sorted(starts), "turns must be sorted by start"
    for _, s, e, _ in turns:
        assert e >= s, f"inverted turn {(s, e)}"

    total = Figures()
    before = Figures()
    figs = {t[0]: TurnFigures() for t in turns}
    for a, b, state in runs:
        total.add(state, b - a)
        # cut the run at every turn start; each piece belongs to one window
        cuts = [a, *dict.fromkeys(starts[bisect_right(starts, a) : bisect_left(starts, b)]), b]
        for x, y in zip(cuts, cuts[1:], strict=False):
            i = bisect_right(starts, x) - 1
            (before if i < 0 else figs[turns[i][0]].window).add(state, y - x)
    for rowid, s, e, _ in turns:
        f = figs[rowid]
        f.span_wall_ms = e - s
        for a, b, state in runs:
            ov = min(b, e) - max(a, s)
            if ov > 0 and state != "none":
                setattr(f, f"span_{state}_ms", getattr(f, f"span_{state}_ms") + ov)
        # D48: `none` is the rest of the span, so wall `none` and a resume gap outside the wall both land here
        f.span_none_ms = f.span_wall_ms - f.span_model_ms - f.span_tool_ms - f.span_agents_ms

    stretches: list[Stretch] = []
    kind_at = {s: k for _, s, _, k in turns}
    for a, b, state in runs:
        if state not in ("none", "agents"):
            continue
        cuts = [a, *dict.fromkeys(starts[bisect_right(starts, a) : bisect_left(starts, b)]), b]
        for x, y in zip(cuts, cuts[1:], strict=False):
            if y - x < MIN_STRETCH_MS:
                continue
            i = bisect_right(starts, x) - 1
            # FALLBACK-OK: DM-52 (next_turn_kind is NULL when no main turn starts at end_ms)
            stretches.append(Stretch(None if i < 0 else turns[i][0], state, x, y, kind_at.get(y)))
    return Split(total.model_ms, total.tool_ms, total.agents_ms, total.none_ms, before, figs, stretches)


def max_silence(tool_calls: Iterable[tuple[str, int, int]], ts_sorted: Sequence[int]) -> dict[str, int]:
    """`tool_calls` = `(op_id, start_ms, end_ms)` of derived-duration calls."""
    out: dict[str, int] = {}
    for op_id, start, end in tool_calls:
        assert end >= start, f"inverted tool_call {op_id}"
        lo = bisect_right(ts_sorted, start - 1)  # first ts >= start
        hi = bisect_right(ts_sorted, end)
        pts = [start, *ts_sorted[lo:hi], end]
        out[op_id] = max(b - a for a, b in zip(pts, pts[1:], strict=False))
    return out
