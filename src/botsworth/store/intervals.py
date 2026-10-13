"""CONTRACT: pure interval math for container time metrics (design §4.1 inv 4, §4.6, D19).

An interval is `(start_ms, end_ms)` with `end_ms >= start_ms`; lengths are ms.

GUARANTEES:
- `merge(intervals)` returns the sorted, disjoint union; touching endpoints
  (`a.end == b.start`) merge and never count as overlap.
- `union_ms(intervals)` = total length of the union; `max_parallel(intervals)`
  = the most intervals open at one instant (half-open: an interval ending at t
  and one starting at t are not parallel); zero-length intervals never count.
- `busy_sum_ms(durations)` = Σ of the durations (non-additive time is never
  summed elsewhere).
- `blocked_idle(own, child)` -> `(blocked_ms, idle_ms)`: over each gap between
  consecutive merged `own` intervals, `blocked` = length of the gap covered by
  the union of `child` intervals, `idle` = the rest. A resume gap is such a gap
  with no child activity, so it is idle, never active.
- `wall_ms(segments, leaf_intervals)` = `union_ms` of the subtree's container
  `segments` (session + engagement files, D19) together with its leaf
  intervals, so resume gaps between segments are outside wall and
  `active_ms <= wall_ms` always holds.
- F3 (design `approaches.md`) values are reproduced: wall 50,000, busy 73,000,
  active 48,000, blocked 40,000, idle 2,000, max_parallel 2.
EXPECTS: no interval with `end < start` (the caller drops inverted intervals).
FAILURE BEHAVIOR: an inverted interval raises AssertionError.
DOES NOT: read the store; decide which intervals belong to a container.
"""

from __future__ import annotations

from collections.abc import Iterable

Interval = tuple[int, int]


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for a, b in sorted(intervals):
        assert b >= a, f"inverted interval {(a, b)}"
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def union_ms(intervals: Iterable[Interval]) -> int:
    return sum(b - a for a, b in merge(intervals))


def max_parallel(intervals: Iterable[Interval]) -> int:
    edges: list[tuple[int, int]] = []
    for a, b in intervals:
        assert b >= a, f"inverted interval {(a, b)}"
        if b > a:
            edges += ((a, 1), (b, -1))
    edges.sort()  # at equal t, -1 sorts before +1: touching intervals are not parallel
    n = peak = 0
    for _, d in edges:
        n += d
        peak = max(peak, n)
    return peak


def busy_sum_ms(durations: Iterable[int]) -> int:
    return sum(durations)


def _covered(merged: list[Interval], a: int, b: int) -> int:
    return sum(min(b, y) - max(a, x) for x, y in merged if y > a and x < b)


def blocked_idle(own: Iterable[Interval], child: Iterable[Interval]) -> tuple[int, int]:
    mine = merge(own)
    others = merge(child)
    blocked = idle = 0
    for (_, gap_start), (gap_end, _) in zip(mine, mine[1:], strict=False):
        covered = _covered(others, gap_start, gap_end)
        blocked += covered
        idle += gap_end - gap_start - covered
    return blocked, idle


def wall_ms(segments: Iterable[Interval], leaf_intervals: Iterable[Interval]) -> int:
    return union_ms([*segments, *leaf_intervals])
