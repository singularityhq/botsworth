"""CONTRACT: the Review (QRY-42, D56): causes of wall time and the findings built on them.

Spec ids: QRY-42, DM-56 (causes), DM-50 [D56] (test and polling substates), DM-52 (stretches), API-33.

GUARANTEES:
- `build(conn, roots, with_sessions)` returns a `wire.Review` over the sessions `roots`. `causes` lists the
  eight DM-56 causes in order, each the sum of its DM-50 parts over the sessions' stored `time_split` rows
  of scope `session`, so they sum to `wall_ms` (asserted per session). `lost_ms` = the `stalled` part.
  `test_runs` is `common.test_runs`, the CLI-20 block of the same sessions. `n_unmeasured` counts the
  sessions whose provider records no task notices (not `claude_code`, DM-41): stalls are not measured there.
- `findings`: one per cause among `stalled`, `tests`, `polling`, `you`, `message` with at least 2 min
  (`FINDING_MIN_MS`); lost causes first, then `ms` descending, then DM-56 order.
  The stored stretches have a 60 s floor (DM-52); a finding counts only stretches of at least 2 min, and
  `n`, `longest` and `threads` use those. `n` and `longest` of `stalled`, `you` and `message` come from the
  stored stretches of that cause (a main-thread wait on the user is a tool row and has no stretch, DM-52,
  so `you` can have
  `ms` and `n` 0); `longest.row` is the `gap-<start_ms>` RowRef. For `tests` and `polling` they come from the
  owned tool_calls with `command_class` `test` / `wait`: `n` counts them, `longest` is the one with the
  longest run (to `bg_end_ms` for a background run, else `dur_ms`), `row` its tool RowRef. A main-transcript
  background run of class `wait` is no candidate for `longest` (DM-50 counts it as `tool`/`background`,
  working; it still counts in `n`). `longest` is the longest over all in-scope sessions; ties go to the
  earlier start, then the session op_id (then the call op_id).
- `threads` (`stalled` only): the sorted distinct names of the threads whose `notice` event
  `[ts_ms, picked_ms)` overlaps a counted stalled stretch of the same session: the `agent_name` of an
  engagement of that `agent_id` (the first by start with a name), else the `agent_id`; `main` when the
  notice has no `agent_id`. A `killed` or `stopped` notice is ignored (DM-41 [D56]). `[]` for the other
  causes.
- `sessions` (when `with_sessions`): the 3 sessions with the most `ms` of the cause (ms above 0; ties by
  op_id); `[]` otherwise.
- `session_review(session)` and `scope_review(scope)` wrap `build` with `freshness` as `wire.ReviewReport`;
  a scope uses `in_scope_roots` (QRY-5, QRY-7, QRY-9: full sessions), the same sessions as `slow`'s `current`.
EXPECTS: schema v12 filled by the load (`time_split`, `stretches`, `command_class`, `bg_end_ms`, `notice`
  events); a connection with `row_factory = sqlite3.Row`.
FAILURE BEHAVIOR: unknown session -> `NotFound`; a stored part that is in no cause, or a session whose parts
  do not sum to `wall_ms`, asserts.
DOES NOT: estimate anything (no re-run, speed-up or edit rule, DM-56), write, or hold prose.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from botsworth.query import Db, _conn, shapes, wire
from botsworth.query.scope import Scope
from botsworth.query.views import common
from botsworth.query.views.explorer import in_scope_roots
from botsworth.query.views.stretches import stretches_for_roots
from botsworth.store import notices

FINDING_MIN_MS = 120_000
NOTICE_PROVIDERS = frozenset({"claude_code"})  # DM-41: Codex and omp record no task notices
TOP_SESSIONS = 3
CAUSES = ("stalled", "tests", "polling", "you", "message", "limit", "working", "idle")  # DM-56 order
LOST = frozenset({"stalled"})
CAUSE_OF: dict[tuple[str, str | None], str] = {
    ("none", "stalled"): "stalled",
    ("tool", "test"): "tests",
    ("agents", "test"): "tests",
    ("tool", "wait"): "polling",
    ("agents", "wait"): "polling",
    ("you", "ask"): "you",
    ("you", "permission"): "you",
    ("you", "reply"): "you",
    ("agentmsg", None): "message",
    ("limit", None): "limit",
    ("model", None): "working",
    ("tool", None): "working",
    ("tool", "background"): "working",
    ("agents", None): "working",
    ("none", "none"): "idle",
    ("none", "before_first"): "idle",
}
_STRETCH_CAUSE = {("none", "stalled"): "stalled", ("agentmsg", None): "message"}  # `you`: any substate
_TOOL_CLASS = {"tests": "test", "polling": "wait"}
_FINDING_CAUSES = ("stalled", "tests", "polling", "you", "message")

_SESSIONS = """
SELECT s.op_rowid, o.op_id, s.session_id, s.provider, s.display_title, s.title_source,
       p.name AS project_name, s.wall_ms
FROM sessions s JOIN ops o ON o.rowid = s.op_rowid LEFT JOIN projects p ON p.project = s.project
WHERE s.op_rowid IN (SELECT value FROM json_each(?))
"""
_CALLS = """
SELECT o.op_id AS op_id, o.start_ms AS start_ms, r.op_id AS session, s.session_id AS session_id,
       NOT (o.command_class = 'wait' AND o.agent_id IS NULL AND o.bg_end_ms IS NOT NULL) AS candidate,
       CASE WHEN o.bg_end_ms IS NOT NULL THEN o.bg_end_ms - o.start_ms ELSE o.dur_ms END AS run_ms,
       t.op_id AS turn, t.display_ix AS display_ix
FROM ops o JOIN ops r ON r.rowid = o.root_rowid JOIN sessions s ON s.op_rowid = o.root_rowid
     LEFT JOIN ops t ON t.rowid = o.attr_turn_rowid
WHERE o.kind = 'tool_call' AND o.duplicate_of IS NULL AND o.command_class = ?
  AND o.root_rowid IN (SELECT value FROM json_each(?))
"""
_NAMES = """
SELECT root_rowid, agent_id, agent_name FROM ops
WHERE kind = 'engagement' AND agent_name IS NOT NULL AND root_rowid IN (SELECT value FROM json_each(?))
ORDER BY start_ms, op_id
"""


def _display(ix: int | None) -> str | None:
    return None if ix is None else f"T{ix}"


def _stretch_evidence(
    by_root: dict[int, list[wire.Stretch]], op_ids: dict[int, str], session_ids: dict[int, str], cause: str
) -> tuple[int, wire.Evidence | None, dict[int, list[wire.Stretch]]]:
    """`(n, longest, the counted stretches by root)` of a stretch cause."""
    counted: dict[int, list[wire.Stretch]] = {}
    best: tuple[int, int, str, int, wire.Stretch] | None = None
    for root, sts in by_root.items():
        hits = [
            s
            for s in sts
            if s.dur_ms >= FINDING_MIN_MS
            # FALLBACK-OK: QRY-42 — a stretch of another cause is no evidence of this one
            and (s.state == "you" if cause == "you" else _STRETCH_CAUSE.get((s.state, s.substate)) == cause)
        ]
        counted[root] = hits
        for s in hits:
            key = (-s.dur_ms, s.start_ms, op_ids[root], root, s)
            if best is None or key[:3] < best[:3]:
                best = key
    n = sum(len(v) for v in counted.values())
    if best is None:
        return n, None, counted
    s, root = best[4], best[3]
    gap = wire.RowRef("gap", f"gap-{s.start_ms}")
    return (
        n,
        wire.Evidence(op_ids[root], session_ids[root], gap, s.turn, None, s.start_ms, s.end_ms, s.dur_ms),
        counted,
    )


def build(conn: sqlite3.Connection, roots: Sequence[int], with_sessions: bool) -> wire.Review:
    ids = json.dumps(list(roots))
    rows = conn.execute(_SESSIONS, (ids,)).fetchall()
    assert len(rows) == len(roots), "a root is not a session"
    parts = common.session_parts(conn, roots)
    per_session: dict[int, dict[str, int]] = {}
    for r in rows:
        acc = parts[r["op_rowid"]]
        assert r["wall_ms"] is not None, f"session {r['op_id']} has no wall_ms (DM-51)"
        assert sum(acc.values()) == r["wall_ms"], f"session {r['op_id']}: time_split does not sum to wall_ms"
        by_cause = dict.fromkeys(CAUSES, 0)
        for part, ms in acc.items():
            assert part in CAUSE_OF, f"time_split part {part} is in no cause (DM-56)"
            by_cause[CAUSE_OF[part]] += ms
        per_session[r["op_rowid"]] = by_cause
    totals = {c: sum(v[c] for v in per_session.values()) for c in CAUSES}
    op_ids = {r["op_rowid"]: r["op_id"] for r in rows}
    session_ids = {r["op_rowid"]: r["session_id"] for r in rows}

    stretches = stretches_for_roots(conn, [r["op_rowid"] for r in rows])
    findings: list[wire.Finding] = []
    for cause in _FINDING_CAUSES:
        if totals[cause] < FINDING_MIN_MS:
            continue
        threads: list[str] = []
        if cause in _TOOL_CLASS:
            n, longest = _call_evidence(conn, ids, _TOOL_CLASS[cause])
        else:
            n, longest, counted = _stretch_evidence(stretches, op_ids, session_ids, cause)
            if longest is not None:
                longest = _with_display(conn, longest)
            if cause == "stalled":
                threads = _threads(conn, roots, ids, counted)
        top: list[wire.FindingSession] = []
        if with_sessions:
            ranked = sorted(
                (r for r in rows if per_session[r["op_rowid"]][cause] > 0),
                key=lambda r: (-per_session[r["op_rowid"]][cause], r["op_id"]),
            )[:TOP_SESSIONS]
            top = [
                wire.FindingSession(
                    r["op_id"], r["session_id"], common.session_name(r)[0], per_session[r["op_rowid"]][cause]
                )
                for r in ranked
            ]
        findings.append(wire.Finding(cause, totals[cause], cause in LOST, n, longest, threads, top))
    findings.sort(key=lambda f: (not f.lost, -f.ms, CAUSES.index(f.cause)))
    return wire.Review(
        wall_ms=sum(r["wall_ms"] for r in rows),
        lost_ms=sum(totals[c] for c in LOST),
        n_sessions=len(rows),
        n_unmeasured=sum(r["provider"] not in NOTICE_PROVIDERS for r in rows),
        causes=[wire.CausePart(c, totals[c]) for c in CAUSES],
        findings=findings,
        test_runs=common.test_runs(conn, roots),
    )


def _with_display(conn: sqlite3.Connection, ev: wire.Evidence) -> wire.Evidence:
    if ev.turn is None:
        return ev
    ix = conn.execute("SELECT display_ix FROM ops WHERE op_id = ?", (ev.turn,)).fetchone()["display_ix"]
    return wire.Evidence(
        ev.session, ev.session_id, ev.row, ev.turn, _display(ix), ev.start_ms, ev.end_ms, ev.dur_ms
    )


def _call_evidence(conn: sqlite3.Connection, ids: str, klass: str) -> tuple[int, wire.Evidence | None]:
    calls = conn.execute(_CALLS, (klass, ids)).fetchall()
    timed = [c for c in calls if c["candidate"] and c["run_ms"] is not None]
    if not timed:
        return len(calls), None
    c = min(timed, key=lambda c: (-c["run_ms"], c["start_ms"], c["session"], c["op_id"]))
    ev = wire.Evidence(
        c["session"], c["session_id"], wire.RowRef("tool", c["op_id"]), c["turn"], _display(c["display_ix"]),
        c["start_ms"], c["start_ms"] + c["run_ms"], c["run_ms"],
    )  # fmt: skip
    return len(calls), ev


def _threads(
    conn: sqlite3.Connection, roots: Sequence[int], ids: str, counted: dict[int, list[wire.Stretch]]
) -> list[str]:
    agent_names: dict[tuple[int, str], str] = {}
    for r in conn.execute(_NAMES, (ids,)):
        agent_names.setdefault((r["root_rowid"], r["agent_id"]), r["agent_name"])
    out: set[str] = set()
    for n in notices.stalling(conn, roots):
        if n.picked_ms is None or not any(
            n.ts_ms < s.end_ms and n.picked_ms > s.start_ms for s in counted.get(n.root_rowid, ())
        ):
            continue
        # FALLBACK-OK: QRY-42 — a thread with no named engagement is shown by its agent_id
        out.add("main" if n.agent_id is None else agent_names.get((n.root_rowid, n.agent_id), n.agent_id))
    return sorted(out)


def session_review(session: str, db: Db = None) -> wire.ReviewReport:
    """QRY-42 for one session. API-33 `GET /api/sessions/{session}/review`."""
    with _conn(db) as conn:
        root = common.lookup_session(conn, session)["op_rowid"]
        return _report(conn, build(conn, [root], with_sessions=False))


def scope_review(scope: Scope, db: Db = None) -> wire.ReviewReport:
    """QRY-42 for the in-scope sessions. API-33 `GET /api/explorer/review`."""
    with _conn(db) as conn:
        return _report(conn, build(conn, in_scope_roots(conn, scope), with_sessions=True))


def _report(conn: sqlite3.Connection, r: wire.Review) -> wire.ReviewReport:
    fresh = wire.Freshness(**shapes.freshness(conn))
    return wire.ReviewReport(
        fresh, r.wall_ms, r.lost_ms, r.n_sessions, r.n_unmeasured, r.causes, r.findings, r.test_runs
    )
