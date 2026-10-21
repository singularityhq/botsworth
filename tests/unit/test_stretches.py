# ruff: noqa: F811
"""Q2a stretch labelling and attachment (views/stretches.py): QRY-41, SES-15..19, SES-37, DM-52, DM-55.

Expected values come from tests/fixtures/qstore/expected.json (hand-derived), never from the code.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest
from qstore import expected, qstore  # noqa: F401 (the QF fixture)
from qstore_ids import SAGENTS, SHARNESS, SSTRETCH, turn

from botsworth.query import _conn, wire
from botsworth.query.views import stretches as S
from botsworth.query.views.common import lookup_session


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1000)


def root(conn: sqlite3.Connection, key: str) -> int:
    return lookup_session(conn, key.split("/")[1])["rowid"]


# ── stored state and substate ────────────────────────────────────────────────


def test_state_and_substate_are_the_stored_ones(qstore):
    want = expected()["case_8_sstretch"]["b3"]["stretches"]
    with _conn(qstore) as conn:
        got = S.stretches_for(conn, root(conn, SSTRETCH))
    assert [(s.state, s.substate) for s in got] == [
        ("you", "reply"),
        ("none", "none"),
        ("limit", None),
        ("agentmsg", None),
        ("agents", None),
        ("you", "permission"),
        ("agents", None),
    ]
    assert [(s.state, s.substate) for s in got] == [(w["state"], w["substate"]) for w in want]
    for s, w in zip(got, want, strict=True):
        assert (s.start_ms, s.end_ms) == (ms(w["start"]), ms(w["end"]))
        assert s.turn == turn(SSTRETCH, int(w["turn"].split("#")[1]))
        assert s.dur_ms == s.end_ms - s.start_ms


def test_module_decides_no_label():
    assert not hasattr(S, "_label") and not hasattr(S, "is_limit_text")  # the load decided them (DM-50)
    assert not hasattr(wire.Stretch, "label")


def test_attached_turn_is_latest_numbered_at_or_before_start():
    a, b = {"start_ms": 100}, {"start_ms": 500}
    assert S._attached([a, b], 100) == a
    assert S._attached([a, b], 499) == a
    assert S._attached([a, b], 500) == b
    assert S._attached([a, b], 99) == a  # SES-37: before the first numbered turn -> the first one
    assert S._attached([], 99) is None


def test_next_turn_only_when_adjacent(qstore):
    with _conn(qstore) as conn:
        got = S.stretches_for(conn, root(conn, SSTRETCH))
    you, wait, limit, agentmsg, agents1, permission, agents2 = got
    assert [s.next_turn is None for s in got] == [False, True, False, False, True, True, True]
    assert you.next_turn.turn == turn(SSTRETCH, 1)
    assert (you.next_turn.kind, you.next_turn.origin, you.next_turn.display_id) == ("human", "typed", "T2")
    assert wait.next_turn is None  # 22 min of nothing; the ask T2 started 22 min earlier
    assert limit.next_turn.label.startswith(expected()["case_8_sstretch"]["limit_label_prefix"])
    assert limit.next_turn.display_id == "T3"
    assert (agentmsg.next_turn.kind, agentmsg.next_turn.origin) == ("message", "peer")


def test_attached_turn_ses37_skips_harness(qstore):
    b3 = expected()["case_7_sharness"]["b3"]
    with _conn(qstore) as conn:
        r = root(conn, SHARNESS)
        got = S.stretches_for(conn, r)
        stored = [
            x[0]
            for x in conn.execute(
                "SELECT turn_rowid FROM stretches WHERE root_rowid = ? ORDER BY start_ms", (r,)
            )
        ]
        harness = {
            x[0]
            for x in conn.execute(
                "SELECT rowid FROM ops WHERE root_rowid = ? AND turn_kind = 'harness'", (r,)
            )
        }
    assert [s.start_ms for s in got] == [ms(w["start"]) for w in b3["stretches"]]
    # DM-52 stores the turn whose window holds the stretch, harness turns included (turn-list indexes)
    assert [w["turn"] for w in b3["stretches"]] == [1, 2, 3, 4]
    assert set(stored) & harness, "the stored turn_rowid names harness turns; QRY-41 must not read it"
    # SES-37: the latest numbered turn: main#0 (T1) then main#2 (T2) for the three that follow
    assert [s.turn for s in got] == [turn(SHARNESS, n) for n in (0, 2, 2, 2)]
    assert [s.next_turn.kind for s in got] == ["human", "harness", "harness", "human"]
    assert [s.next_turn.display_id for s in got] == ["T2", None, None, "T3"]  # harness: QRY-2


def test_running_lists_overlapping_engagements_max_8(qstore, monkeypatch):
    with _conn(qstore) as conn:
        got = S.stretches_for(conn, root(conn, SAGENTS))
        agents = [s for s in got if s.state == "agents"]
        idle = [s for s in got if s.state in ("none", "you")]
        assert [s.running for s in idle] == [[]] * len(idle)
        eng = lambda k: f"claude_code/sagents/engagement/{k}"  # noqa: E731
        # (id, whole cost): ra#0 2.00 + its nested rn#0 0.50; B#0 0.40; C#0 0.30 (case 6 attr note)
        first = [(r.row, r.cost_usd) for r in agents[0].running]
        # start order: ra#0 09:01:00, rb#0 09:02:30, rc#0 09:03:00, rn#0 09:04:30
        assert first == [(eng("ra#0"), 2.5), (eng("rb#0"), 0.4), (eng("rc#0"), 0.3), (eng("rn#0"), 0.5)]
        assert [[r.row for r in s.running] for s in agents[1:]] == [[eng("ra#1")], [eng("rb#1")]]
        assert agents[1].running[0].cost_usd == pytest.approx(1.0)
        assert (agents[1].running[0].start_ms, agents[1].running[0].end_ms) == (
            ms("2026-10-08T10:00:00Z"),
            ms("2026-10-08T10:01:15Z"),
        )
        assert S.MAX_RUNNING == 8
        monkeypatch.setattr(S, "MAX_RUNNING", 2)
        capped = [s for s in S.stretches_for(conn, root(conn, SAGENTS)) if s.state == "agents"][0]
        assert len(capped.running) == 2  # the first two by start time


def test_you_stretch_from_an_agent_call_lists_the_running_engagement(qstore):
    # D54: the permission wait of the forecaster's Bash call (04:20-04:24) has no main-thread call, so it is a
    # stretch; `running` names the agent that waits
    with _conn(qstore) as conn:
        got = S.stretches_for(conn, root(conn, SSTRETCH))
    permission = next(s for s in got if s.substate == "permission")
    assert [r.row for r in permission.running] == ["claude_code/sstretch/engagement/rd#0"]
    assert permission.running[0].cost_usd == pytest.approx(0.8)  # the whole engagement: 0.40 + 0.40
    for s in got:
        if s.state not in ("agents",) and s is not permission:
            assert s.running == []


def test_main_thread_wait_makes_no_stretch(qstore):
    with _conn(qstore) as conn:
        assert S.stretches_for(conn, root(conn, "claude_code/sslow1")) == []  # the 15-min ask is a tool row


def test_batched_equals_single(qstore):
    with _conn(qstore) as conn:
        roots = [r[0] for r in conn.execute("SELECT rowid FROM sessions ORDER BY rowid")]
        batched = S.stretches_for_roots(conn, roots)
        assert set(batched) == set(roots)
        for r in roots:
            assert batched[r] == S.stretches_for(conn, r), r
        assert any(batched.values())


def test_range_and_floor_filters(qstore):
    limit_start, limit_end = ms("2026-10-10T02:49:00Z"), ms("2026-10-10T03:51:00Z")
    with _conn(qstore) as conn:
        r = root(conn, SSTRETCH)
        inside = S.stretches_for(conn, r, from_ms=limit_start + 1000, to_ms=limit_end - 1000)
        assert [s.state for s in inside] == ["limit"]  # overlap keeps the whole stretch, unclipped
        assert (inside[0].start_ms, inside[0].end_ms) == (limit_start, limit_end)
        assert [s.state for s in S.stretches_for(conn, r, min_ms=1_400_000)] == ["you", "limit", "agents"]
        with pytest.raises(AssertionError):
            S.stretches_for(conn, r, min_ms=59_999)
