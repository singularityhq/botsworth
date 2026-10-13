"""CONTRACT: engagement links — content-hash matching and the cycle rule (design §4.5, D14, D18).

GUARANTEES:
- `body_hash(text)` = sha256 hex of the stripped text, None for blank text.
  Sender and arrival bodies are compared only through this hash.
- `latest_preceding(candidates, start_ms)` keeps the candidates whose send
  started at or before `start_ms` and, of those, the ones with the latest
  start. It resolves a body sent several times to the send that came last
  before the arrival. With no start, or no candidate at or before it, the
  candidates are returned unchanged (the caller links only a unique one).
- `apply_cycle_rule(engagements, by_id, root_id)` re-parents every engagement
  whose cause has, among its engagement ancestors, an engagement of the same
  agent (or that sits on a raw link loop): the new parent is the parent of
  that agent's `#0` engagement (its spawn call), or `root_id` when the agent
  has no `#0` or the `#0` is itself on the loop. `caused_by` is kept;
  `caused_by_basis` gets the `+cycle` suffix. Ancestors are settled before
  descendants, so the walk sees final parents. Returns the number of
  re-parented engagements.
EXPECTS: `by_id` holds every op named by a `parent` or `caused_by`; every
  engagement carries `agent_id`, `engagement_ix` and a `caused_by_basis`.
FAILURE BEHAVIOR: a `caused_by`/`parent` missing from `by_id` -> KeyError
  (a bundle defect, never silently skipped).
DOES NOT: decide which sends are candidates (provider tree); reject bundles
  (the caller checks depth and reachability after the rule).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence

from botsworth.model import CYCLE_SUFFIX, Kind, Op


def body_hash(text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    return hashlib.sha256(text.strip().encode()).hexdigest()


def latest_preceding[T](
    candidates: Sequence[T], start_ms: int | None, start_of: Callable[[T], int | None]
) -> list[T]:
    if start_ms is None:
        return list(candidates)
    preceding = [c for c in candidates if start_of(c) is not None and start_of(c) <= start_ms]
    if not preceding:
        return list(candidates)
    latest = max(start_of(c) for c in preceding)
    return [c for c in preceding if start_of(c) == latest]


def apply_cycle_rule(engagements: list[Op], by_id: dict[str, Op], root_id: str) -> int:
    first = {e.agent_id: e for e in engagements if e.engagement_ix == 0}
    done: set[str] = set()
    active: set[str] = set()
    moved = 0

    def settle(eg: Op) -> None:
        nonlocal moved
        if eg.op_id in done:
            return
        active.add(eg.op_id)
        cycle, node = False, eg.caused_by
        while node is not None and not cycle:
            op = by_id[node]
            if op.kind == Kind.ENGAGEMENT:
                if op.op_id in active:
                    cycle = True  # raw link loop: a cycle, never recurse into it
                    break
                settle(op)
                cycle = op.agent_id == eg.agent_id
            node = op.parent
        if cycle:
            spawn = first.get(eg.agent_id)
            if spawn is None or spawn is eg or spawn.op_id in active:
                eg.parent = root_id
            else:
                settle(spawn)
                eg.parent = spawn.parent
            eg.caused_by_basis = f"{eg.caused_by_basis}{CYCLE_SUFFIX}"
            moved += 1
        active.discard(eg.op_id)
        done.add(eg.op_id)

    for eg in engagements:
        if eg.caused_by is not None:
            settle(eg)
    return moved
