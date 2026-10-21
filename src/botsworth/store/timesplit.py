"""CONTRACT: pure time-split math for sessions and main turns (DM-40, DM-41, DM-50..DM-53; D54, D55).

Intervals are `(start_ms, end_ms)` with `end_ms >= start_ms`. A `TurnFact` is one main turn as the rules
need it. `waits` maps `(thread, substate)` to the intervals of tool_calls with that `user_wait`: `thread` is
`main` or `agent`, `substate` is `ask` or `permission`. `main_tool` and `agent_leaf` hold only calls with
`user_wait` NULL; `agent_leaf` also holds the background runs `[start_ms, bg_end_ms]` of agent calls (DM-41).
`main_bg` holds the background runs of main-transcript calls. `stalls` holds the intervals
`[queued, picked)` of the task notices (DM-41) that were picked up.

GUARANTEES:
- DM-50: `runs()` gives each instant inside the wall (the union of `wall_segments`) one
  `(state, substate)`, by the first rule that matches: `model` (a main llm_call) > `tool` (a main
  tool_call) > `agents` (an agent leaf or agent background run) > `tool`/`background` (a main background
  run, D55) > `you`/`ask` (main call) > `you`/`permission` (main call) >
  `you`/`ask` (agent call) > `you`/`permission` (agent call). An instant no rule covers belongs to
  an idle run, cut at every main-turn start. The part of the run inside a stall is `none`/`stalled` (D55);
  the rest is labelled by the turn that starts where the run ends
  (lowest `seq` at a shared start): no numbered turn starts at or before the run's start ->
  `none`/`before_first`; `human` turn with a SES-17 label -> `limit`; other `human` turn ->
  `you`/`reply`; `message` turn with origin `peer`, `coordinator`, `teammate` or `agent` ->
  `agentmsg`; else `none`/`none`. Adjacent runs with equal (state, substate) are joined. Intervals
  outside the wall never count.
- DM-51: `Split.session` sums to the wall. A turn's window is `[start, next main turn start)`, the last
  one ends at the wall end; the turn with the lowest `seq` at a shared start holds the window,
  the others get none.
  `Split.before_first` is the part of the wall before the first turn. Per (state, substate), `before_first`
  plus all windows equals `Split.session`.
- DM-53: `Split.spans[rowid]` counts the runs inside the turn's own `[start_ms, end_ms]`, plus the time of the
  span outside the wall (a resume gap, D48) as `none`/`none`; the parts sum to `end_ms - start_ms`.
  `clip()` gives the same figures for any range, so a range equal to the span gives the stored span.
- DM-52: `Split.stretches` holds every run of at least `MIN_STRETCH_MS` that no main-transcript call covers,
  cut at main-turn starts (each part kept when long enough); runs of `model`, `tool` (not `tool`/`background`)
  and of a user-wait main call give none. `turn_rowid` = the turn whose window holds the part start (None
  before the first turn).
- DM-40: `max_silence` = longest sub-interval of `[start, end]` with no timestamp from `ts_sorted`; the
  call's own start and end count as timestamps.
- `collect(rows, notices)`: a tool_call with `bg_end_ms` at or after its start adds `[start_ms, bg_end_ms]` to
  `main_bg` (main transcript) or `agent_leaf` (agent); `notices` are `(ts_ms, picked_ms)` pairs and every pair
  with `picked_ms` after `ts_ms` adds a stall.
EXPECTS: no inverted interval or turn; every `human` turn has a label; `turns` have distinct rowids;
  `ts_sorted` ascending.
FAILURE BEHAVIOR: a violated EXPECTS raises AssertionError.
DOES NOT: read or write the store; decide which ops are main, agent or tool, or what their `user_wait` is (the
  caller does); store `ts_list` (DM-13).
"""

from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from botsworth.store.intervals import Interval, merge

MIN_STRETCH_MS = 60_000
# SES-15 order, then the substate order of DM-50: the order of every `parts` list on the wire
PARTS: tuple[tuple[str, str | None], ...] = (
    ("model", None),
    ("tool", None),
    ("tool", "background"),
    ("agents", None),
    ("you", "ask"),
    ("you", "permission"),
    ("you", "reply"),
    ("agentmsg", None),
    ("limit", None),
    ("none", "stalled"),
    ("none", "before_first"),
    ("none", "none"),
)
LIMIT_PREFIXES = (  # SES-17
    "i hit my usage limit",
    "please continue from where you left off",
    "continue from where you left off",
)
AGENT_ORIGINS = frozenset({"peer", "coordinator", "teammate", "agent"})  # DM-50 rule 5 `agentmsg`
# rule 4: the order in which the user-wait calls cover an instant
_WAIT_ORDER = (("main", "ask"), ("main", "permission"), ("agent", "ask"), ("agent", "permission"))

Part = tuple[str, str | None]
Waits = Mapping[tuple[str, str], Iterable[Interval]]


class TurnFact(NamedTuple):
    """One main turn with a start. `end_ms` is None for a turn without one (no span); `seq` is
    `COALESCE(ops.seq, ops.pre)`; `numbered` = the turn has a `display_ix`."""

    rowid: int
    start_ms: int
    end_ms: int | None
    seq: int
    kind: str | None
    origin: str | None
    label: str | None
    numbered: bool


class Run(NamedTuple):
    start_ms: int
    end_ms: int
    state: str
    substate: str | None


class Stretch(NamedTuple):
    turn_rowid: int | None
    state: str
    substate: str | None
    start_ms: int
    end_ms: int


class Split(NamedTuple):
    session: dict[Part, int]
    before_first: dict[Part, int]
    windows: dict[int, dict[Part, int]]
    spans: dict[int, dict[Part, int]]
    stretches: list[Stretch]


class Inputs(NamedTuple):
    """The arguments of `runs()` and `split()` after the wall."""

    wall: list[Interval]
    main_llm: list[Interval]
    main_tool: list[Interval]
    agent_leaf: list[Interval]
    waits: dict[tuple[str, str], list[Interval]]
    turns: list[TurnFact]
    main_bg: list[Interval]
    stalls: list[Interval]


def collect(rows: Iterable[Mapping[str, Any]], notices: Iterable[tuple[int, int | None]] = ()) -> Inputs:
    """The inputs of the split from the ops of one session. Load (folded rows) and the query layer (stored
    rows) both call this, so they feed `runs()` and `split()` the same facts. A row has `kind`, `start_ms`,
    `end_ms`, `agent_id`, `duplicate_of`, `n_inverted`, `segments` (a list, or its JSON text), `user_wait`,
    `bg_end_ms`, and for a turn `rowid`, `seq`, `pre`, `turn_kind`, `turn_origin`, `label`, `display_ix`.

    The wall is the union of the containers' segments (a container without segments counts as its own
    `[start_ms, end_ms]`) and the intervals of the non-duplicate (owned, DM-45), non-inverted leaves.
    A main turn without a start is no fact (it starts no run)."""
    wall: list[Interval] = []
    llm: list[Interval] = []
    tool: list[Interval] = []
    agents: list[Interval] = []
    main_bg: list[Interval] = []
    waits: dict[tuple[str, str], list[Interval]] = defaultdict(list)
    turns: list[TurnFact] = []
    for r in rows:
        start, end = r["start_ms"], r["end_ms"]
        if r["kind"] not in ("llm_call", "tool_call"):
            segs = r["segments"]
            if isinstance(segs, str):
                segs = json.loads(segs)
            if segs:
                wall += [(a, b) for a, b in segs if b >= a]
            elif start is not None and end is not None and end >= start:
                wall.append((start, end))
            if r["kind"] == "turn" and r["agent_id"] is None and start is not None:
                kind, display_ix = r["turn_kind"], r["display_ix"]
                assert (kind in ("human", "message")) == (display_ix is not None), (
                    f"main turn {r['rowid']}: display_ix does not match kind {kind!r} (DM-24)"
                )
                assert kind != "human" or r["label"] is not None, (
                    f"human turn {r['rowid']} has no label (DM-24)"
                )
                seq = r["pre"] if r["seq"] is None else r["seq"]
                turns.append(
                    TurnFact(
                        r["rowid"],
                        start,
                        end,
                        seq,
                        kind,
                        r["turn_origin"],
                        r["label"],
                        display_ix is not None,
                    )
                )
            continue
        if r["duplicate_of"] is not None or r["n_inverted"] or start is None:
            continue
        main = r["agent_id"] is None
        bg_end = r["bg_end_ms"]
        if bg_end is not None and bg_end >= start:  # DM-41: the run lasts until the notice was queued
            (main_bg if main else agents).append((start, bg_end))  # also for a call with no end of its own
        if end is None:
            continue
        iv = (start, end)
        wall.append(iv)
        if r["user_wait"] is not None:
            assert r["user_wait"] in ("ask", "permission"), f"unknown user_wait {r['user_wait']!r}"
            waits["main" if main else "agent", r["user_wait"]].append(iv)
        elif not main:
            agents.append(iv)
        elif r["kind"] == "llm_call":
            llm.append(iv)
        else:
            tool.append(iv)
    turns.sort(key=lambda t: (t.start_ms, t.seq))
    stalls = [(queued, picked) for queued, picked in notices if picked is not None and picked > queued]
    return Inputs(
        merge(wall), merge(llm), merge(tool), merge(agents), dict(waits), turns, merge(main_bg), merge(stalls)
    )


def is_limit_text(label: str) -> bool:
    """SES-17: the text, trimmed and lower-cased, starts with a usage-limit prefix."""
    return label.strip().lower().startswith(LIMIT_PREFIXES)


def ordered(acc: Mapping[Part, int]) -> list[tuple[str, str | None, int]]:
    """`(state, substate, ms)` of the parts above 0 ms, in wire order."""
    assert set(acc) <= set(PARTS), f"unknown part in {sorted(map(str, acc))}"
    return [(s, sub, acc[s, sub]) for s, sub in PARTS if acc.get((s, sub), 0) > 0]


def _covers(merged: list[Interval], starts: list[int], t: int) -> bool:
    """True when the half-open instant [t, t+) lies in the merged union (t is a segment start)."""
    i = bisect_right(starts, t) - 1
    return i >= 0 and merged[i][0] <= t < merged[i][1]


def _idle_label(first_numbered: int | None, start: int, nxt: TurnFact | None) -> tuple[str, str | None]:
    if first_numbered is None or start < first_numbered:
        return "none", "before_first"
    if nxt is None:
        return "none", "none"
    if nxt.kind == "human":
        assert nxt.label is not None  # checked in collect()
        return ("limit", None) if is_limit_text(nxt.label) else ("you", "reply")
    if nxt.kind == "message" and nxt.origin in AGENT_ORIGINS:
        return "agentmsg", None
    return "none", "none"


def _segments(
    wall: list[Interval],
    main_llm: Iterable[Interval],
    main_tool: Iterable[Interval],
    agent_leaf: Iterable[Interval],
    waits: Waits,
    turns: Sequence[TurnFact],
    main_bg: Iterable[Interval] = (),
    stalls: Iterable[Interval] = (),
) -> list[tuple[int, int, str, str | None, bool]]:
    """Labelled runs `(start, end, state, substate, main_call)`; `main_call` is True for a user-wait
    covered by a main-transcript call. Adjacent runs are joined only when all four labels are equal."""
    sets: list[tuple[list[Interval], list[int], str, str | None, bool]] = []
    rules = [
        (main_llm, "model", None, False),
        (main_tool, "tool", None, False),
        (agent_leaf, "agents", None, False),
        (main_bg, "tool", "background", False),  # DM-50 rule 3a [D55]
        # FALLBACK-OK: DM-50 (a wait category with no call covers nothing)
        *((waits.get(key, ()), "you", key[1], key[0] == "main") for key in _WAIT_ORDER),
    ]
    for ivs, state, sub, main in rules:
        m = merge(ivs)
        sets.append((m, [a for a, _ in m], state, sub, main))
    wall_starts = [a for a, _ in wall]
    stall = merge(stalls)
    stall_starts = [a for a, _ in stall]
    turn_at: dict[int, TurnFact] = {}
    for t in turns:
        cur = turn_at.get(t.start_ms)
        if cur is None or t.seq < cur.seq:
            turn_at[t.start_ms] = t
    numbered = [t.start_ms for t in turns if t.numbered]
    first_numbered = min(numbered, default=None)
    bounds = sorted({t for m in (wall, *(s[0] for s in sets)) for a, b in m for t in (a, b)} | set(turn_at))
    out: list[tuple[int, int, str, str | None, bool]] = []
    idle: list[int] | None = None  # [start, end] of the open idle run

    def close_idle() -> None:
        nonlocal idle
        if idle is None:
            return
        state, sub = _idle_label(first_numbered, idle[0], turn_at.get(idle[1]))
        at = idle[0]
        for x, y in stall[max(bisect_right(stall_starts, idle[0]) - 1, 0) :]:  # DM-50 rule 5 [D55]
            if x >= idle[1]:
                break
            x, y = max(x, idle[0]), min(y, idle[1])
            if y <= x:
                continue
            if x > at:
                _append(out, (at, x, state, sub, False))
            _append(out, (x, y, "none", "stalled", False))
            at = y
        if at < idle[1]:
            _append(out, (at, idle[1], state, sub, False))
        idle = None

    for a, b in zip(bounds, bounds[1:], strict=False):
        if not _covers(wall, wall_starts, a):
            close_idle()
            continue
        hit = next(((s[2], s[3], s[4]) for s in sets if _covers(s[0], s[1], a)), None)
        if hit is None:
            if idle is not None and idle[1] == a and a not in turn_at:
                idle[1] = b
            else:
                close_idle()
                idle = [a, b]
            continue
        close_idle()
        _append(out, (a, b, *hit))
    close_idle()
    return out


def _append(out: list, seg: tuple[int, int, str, str | None, bool]) -> None:
    if out and out[-1][1] == seg[0] and out[-1][2:] == seg[2:]:
        out[-1] = (out[-1][0], seg[1], *seg[2:])
    else:
        out.append(seg)


def runs(
    wall_segments: Iterable[Interval],
    main_llm: Iterable[Interval],
    main_tool: Iterable[Interval],
    agent_leaf: Iterable[Interval],
    waits: Waits,
    turns: Sequence[TurnFact],
    main_bg: Iterable[Interval] = (),
    stalls: Iterable[Interval] = (),
) -> list[Run]:
    """DM-50: the (state, substate) runs inside the wall, adjacent equal runs joined."""
    return _join(
        _segments(merge(wall_segments), main_llm, main_tool, agent_leaf, waits, turns, main_bg, stalls)
    )


def _join(segs: Iterable[tuple[int, int, str, str | None, bool]]) -> list[Run]:
    out: list[Run] = []
    for a, b, state, sub, _ in segs:
        if out and out[-1].end_ms == a and out[-1].state == state and out[-1].substate == sub:
            out[-1] = Run(out[-1].start_ms, b, state, sub)
        else:
            out.append(Run(a, b, state, sub))
    return out


def clip(
    rs: Sequence[Run], lo: int, hi: int, fill_outside: bool = False, ends: Sequence[int] | None = None
) -> dict[Part, int]:
    """Time per (state, substate) of the runs inside `[lo, hi)`. With `fill_outside`, the part of the range
    that no run covers (outside the wall, D48) counts as `none`/`none`, so the parts sum to `hi - lo`.
    `ends` = the runs' end times, for a caller that clips many ranges."""
    acc: dict[Part, int] = {}
    i = bisect_right([r.end_ms for r in rs] if ends is None else ends, lo)  # the first run ending after lo
    covered = 0
    while i < len(rs) and rs[i].start_ms < hi:
        r = rs[i]
        ms = min(r.end_ms, hi) - max(r.start_ms, lo)
        if ms > 0:  # an empty range inside a run has no time
            acc[r.state, r.substate] = acc.get((r.state, r.substate), 0) + ms
            covered += ms
        i += 1
    if fill_outside and hi - lo > covered:
        acc["none", "none"] = acc.get(("none", "none"), 0) + hi - lo - covered
    return acc


def _add(acc: dict[Part, int], part: Part, ms: int) -> None:
    acc[part] = acc.get(part, 0) + ms


def split(
    wall_segments: Iterable[Interval],
    main_llm: Iterable[Interval],
    main_tool: Iterable[Interval],
    agent_leaf: Iterable[Interval],
    waits: Waits,
    turns: Sequence[TurnFact],
    main_bg: Iterable[Interval] = (),
    stalls: Iterable[Interval] = (),
) -> Split:
    wall = merge(wall_segments)
    for t in turns:
        assert t.end_ms is None or t.end_ms >= t.start_ms, f"inverted turn {t.rowid}"
    assert len({t.rowid for t in turns}) == len(turns), "duplicate turn rowid"
    segs = _segments(wall, main_llm, main_tool, agent_leaf, waits, turns, main_bg, stalls)
    joined = _join(segs)

    holders: dict[int, TurnFact] = {}  # DM-51: the lowest seq at a shared start holds the window
    for t in turns:
        cur = holders.get(t.start_ms)
        if cur is None or t.seq < cur.seq:
            holders[t.start_ms] = t
    starts = sorted(holders)

    session: dict[Part, int] = {}
    before: dict[Part, int] = {}
    windows: dict[int, dict[Part, int]] = {h.rowid: {} for h in holders.values()}

    def cut(a: int, b: int) -> Iterable[tuple[int, int, int]]:
        """`(x, y, i)`: the pieces of [a, b) between turn starts; `i` indexes `starts` (-1: before all)."""
        edges = [a, *starts[bisect_right(starts, a) : bisect_left(starts, b)], b]
        for x, y in zip(edges, edges[1:], strict=False):
            yield x, y, bisect_right(starts, x) - 1

    for r in joined:
        for x, y, i in cut(r.start_ms, r.end_ms):
            _add(session, (r.state, r.substate), y - x)
            _add(before if i < 0 else windows[holders[starts[i]].rowid], (r.state, r.substate), y - x)

    spans = {
        t.rowid: clip(joined, t.start_ms, t.end_ms, fill_outside=True) for t in turns if t.end_ms is not None
    }

    stretches: list[Stretch] = []
    for a, b, state, sub, main in segs:  # joined on all four labels, so a main call's wait stays apart
        if (
            state == "model" or (state, sub) == ("tool", None) or main
        ):  # D55: `tool`/`background` is a stretch
            continue
        for x, y, i in cut(a, b):
            if y - x >= MIN_STRETCH_MS:
                stretches.append(Stretch(None if i < 0 else holders[starts[i]].rowid, state, sub, x, y))
    return Split(session, before, windows, spans, stretches)


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
