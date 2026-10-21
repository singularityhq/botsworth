# ruff: noqa: F811
"""Q3b use-case views (CLI-20, CLI-21, CLI-22, TST-7) on the QF store.

Expected numbers come from tests/fixtures/qstore/expected.json (cases 11, 13, 16, 10, 6) or are rebuilt from
raw `ops` rows in the test (an oracle written apart from the view's SQL), or from the other views
(`session_detail`, `session_turns`, `find`, `list_sessions`), never from the use-case view itself.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import fields

import pytest
from qstore import qstore  # noqa: F401 (the QF fixture)
from qstore_ids import (
    A0,
    AGENT_PERMISSION,
    ASK_USER,
    NESTED,
    PYTEST_OK,
    SAGENTS,
    SHARNESS,
    SLOW_BASH,
    SLOW_PROJECT,
    SSLOW1,
    STOOLS,
    parts,
)

from botsworth.query import NotFound, QueryError, wire
from botsworth.query.scope import Scope
from botsworth.query.views import explorer as ex
from botsworth.query.views import usecases as U
from botsworth.query.views.find import find
from botsworth.query.views.session import session_detail, session_turns
from botsworth.query.views.stretches import stretches_for

DAY = "2026-10-07"  # sslow1, sslow2 and the second day of s2day (Asia/Kolkata)
SLOW = Scope(projects=(SLOW_PROJECT,), from_day=DAY, to_day=DAY)


def key(session: str) -> str:
    return session + "/session/-"


def only(session_id: str) -> Scope:
    """A scope of exactly one session (its id is a `q` term)."""
    return Scope(q=session_id)


def rows(db, sql: str, *args) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def nearest_rank(values: list[int], p: int) -> int:
    ordered = sorted(values)
    return ordered[math.ceil(p * len(ordered) / 100) - 1]


# ── CLI-20: project_slow ─────────────────────────────────────────────────────


def test_slow_scenario(qstore):
    """Case 16: walls 60 and 30 min, a 25-min `you` stretch, a 40-min derived Bash."""
    r = U.project_slow(SLOW, db=qstore)
    assert r.current.n_sessions == 2
    assert r.current.wall_ms == 5_400_000
    assert sum(parts(r.current.time_split).values()) == r.current.wall_ms
    # CLI-20 scenario (D54): model 585 s, tool 2400 s (bash), you/ask 900 s, you/reply 1500 s, none/none 15 s
    assert parts(r.current.time_split) == {
        "model": 585_000,
        "tool": 2_400_000,
        "you/ask": 900_000,
        "you/reply": 1_500_000,
        "none/none": 15_000,
    }
    top = r.slowest_tools[0]
    assert top.row == wire.RowRef("tool", SLOW_BASH)
    assert (top.time_prov, top.max_silence_ms, top.dur_ms, top.command_key) == (
        "derived",
        1_800_000,
        2_400_000,
        "make bench",
    )
    assert top.summary and top.turn and top.display_id == "T1"
    # case 11: the Bash group has one derived call whose silence is 30 min (>= 5 min: no records)
    (bash,) = [g for g in r.tools if g.tool_name == "Bash"]
    assert (bash.command_key, bash.n, bash.n_derived, bash.n_no_records) == ("make bench", 1, 1, 1)
    assert [s.session for s in r.sessions] == [key("claude_code/sslow1"), key("claude_code/sslow2")]
    assert [s.wall_ms for s in r.sessions] == [3_600_000, 1_800_000]
    # the 25-min stretch is the longest wait of sslow2 and of the project
    assert r.sessions[1].longest_wait.dur_ms == 1_500_000
    assert r.waits[0].dur_ms == 1_500_000 and r.waits[0].session == key("claude_code/sslow2")
    assert r.waits[0].display_id == "T1"


def test_slow_split_sums_to_wall(qstore):
    scope = Scope(show_automated=True)
    r = U.project_slow(scope, n=100, db=qstore)
    assert r.current.n_sessions == 13
    assert sum(parts(r.current.time_split).values()) == r.current.wall_ms
    # each session row sums to its wall and the block is the sum of the rows
    for s in r.sessions:
        assert sum(parts(s.time_split).values()) == s.wall_ms
    assert sum(s.wall_ms for s in r.sessions) == r.current.wall_ms
    # the block's parts are the sessions' parts (QRY-29), per (state, substate)
    want: dict[str, int] = {}
    for s in r.sessions:
        for part, value in parts(session_detail(s.session, db=qstore).session.time_split).items():
            want[part] = want.get(part, 0) + value
    assert parts(r.current.time_split) == want
    assert not hasattr(r.current, "none_split")


def test_slow_previous_period_same_length(qstore):
    scope = Scope(from_day="2026-10-08", to_day="2026-10-09")
    r = U.project_slow(scope, db=qstore)
    prev = ex.period_summary(Scope(from_day="2026-10-06", to_day="2026-10-07"), db=qstore)
    listed = ex.list_sessions(Scope(from_day="2026-10-06", to_day="2026-10-07"), db=qstore, limit=1000).rows
    assert r.previous.n_sessions == prev.totals.n_sessions_period == len(listed)
    assert r.previous.wall_ms == sum(s.wall_ms for s in listed)
    assert r.previous.cost.cost_usd == pytest.approx(prev.totals.cost_usd)
    # a one-day scope compares with the day before it
    one = U.project_slow(Scope(from_day="2026-10-08", to_day="2026-10-08"), db=qstore)
    assert one.previous.n_sessions == len(
        ex.list_sessions(Scope(from_day="2026-10-07", to_day="2026-10-07"), db=qstore, limit=1000).rows
    )


def test_slow_previous_null_all_time_or_no_compare(qstore):
    assert U.project_slow(Scope(), db=qstore).previous is None
    assert U.project_slow(SLOW, compare=False, db=qstore).previous is None
    assert U.project_slow(SLOW, db=qstore).previous is not None


def test_slow_user_waits_excluded_and_counted(qstore):
    r = U.project_slow(SLOW, n=100, db=qstore)
    assert r.user_waits == wire.UserWaits(
        ask=wire.UserWaitCount(1, 900_000), permission=wire.UserWaitCount(0, 0)
    )
    # the store-wide count adds the forecaster's 4 min permission wait (an agent's Bash call)
    everything = U.project_slow(Scope(show_automated=True), n=100, db=qstore)
    assert everything.user_waits.permission == wire.UserWaitCount(1, 240_000)
    assert AGENT_PERMISSION not in [t.row.id for t in everything.slowest_tools]
    assert ASK_USER not in [t.row.id for t in r.slowest_tools]
    assert "AskUserQuestion" not in [g.tool_name for g in r.tools]
    # the group totals still hold the other call: 2 tool_calls in the sessions, 1 waits on the user
    assert sum(g.n for g in r.tools) == 1


def test_slow_tool_groups_percentiles_nearest_rank(qstore):
    r = U.project_slow(Scope(show_automated=True), n=100, db=qstore)
    raw = defaultdict(list)
    for o in rows(
        qstore,
        "SELECT tool_name, command_key, dur_ms FROM ops WHERE kind = 'tool_call' AND duplicate_of IS NULL "
        "AND user_wait IS NULL",
    ):
        raw[(o["tool_name"], o["command_key"])].append(o["dur_ms"])
    assert {(g.tool_name, g.command_key) for g in r.tools} == set(raw)
    for g in r.tools:
        durs = [d for d in raw[(g.tool_name, g.command_key)] if d is not None]
        assert g.n == len(raw[(g.tool_name, g.command_key)])
        assert g.dur_ms == sum(durs)
        assert (g.p50_ms, g.p90_ms, g.max_ms) == (nearest_rank(durs, 50), nearest_rank(durs, 90), max(durs))
    # ordered by dur_ms descending
    assert [g.dur_ms for g in r.tools] == sorted((g.dur_ms for g in r.tools), reverse=True)
    # n groups only
    assert len(U.project_slow(Scope(show_automated=True), n=2, db=qstore).tools) == 2


def test_slow_full_session_figures(qstore):
    """s2day runs 6 Oct 22:29 to 7 Oct 00:31 (IST): on 7 Oct it counts whole (QRY-9)."""
    r = U.project_slow(Scope(from_day=DAY, to_day=DAY), n=100, db=qstore)
    by_id = {s.session_id: s for s in r.sessions}
    assert by_id["s2day"].wall_ms == 7_265_000
    page = ex.list_sessions(Scope(from_day=DAY, to_day=DAY), db=qstore, limit=1000)
    assert sorted(by_id) == sorted(s.session_id for s in page.rows)
    assert r.current.wall_ms == sum(s.wall_ms for s in page.rows)
    assert r.current.cost.cost_usd == pytest.approx(sum(s.cost_usd for s in page.rows))
    assert by_id["s2day"].cost.cost_usd == pytest.approx(8.0)  # the full 8.00, not the 5.00 of 7 Oct


def test_slow_n_range(qstore):
    for bad in (0, 101):
        with pytest.raises(QueryError):
            U.project_slow(SLOW, n=bad, db=qstore)


def test_slow_empty_scope_is_zero(qstore):
    r = U.project_slow(Scope(q="no such session anywhere"), db=qstore)
    assert r.current.n_sessions == 0 and r.current.wall_ms == 0 and r.current.tok_total is None
    assert r.sessions == r.tools == r.slowest_tools == r.waits == r.models == []


# ── CLI-21: session_breakdown ────────────────────────────────────────────────

BREAKDOWN_SESSIONS = ["claude_code/" + s for s in ("sagents", "stools", "sslow1", "sstretch", "sskills")]


@pytest.mark.parametrize("session", BREAKDOWN_SESSIONS)
def test_breakdown_equals_composed_views(qstore, session):
    n = 4
    b = U.session_breakdown(key(session), n, db=qstore)
    d = session_detail(key(session), db=qstore).session
    s = b.session
    assert (s.session, s.session_id, s.name, s.project_name, s.provider) == (
        d.session, d.session_id, d.name, d.project_name, d.provider
    )  # fmt: skip
    assert (s.start_ms, s.end_ms, s.wall_ms, s.active_ms, s.tok_total) == (
        d.start_ms, d.end_ms, d.wall_ms, d.active_ms, d.tok_total
    )  # fmt: skip
    assert (s.cost.cost_usd, s.cost.cost_lower_bound, s.cost.n_unpriced) == (
        d.cost.cost_usd, d.cost.cost_lower_bound, d.cost.n_unpriced
    )  # fmt: skip
    assert s.time_split == d.time_split
    assert b.models == d.models
    turns = [
        t
        for t in session_turns(key(session), db=qstore).items
        if isinstance(t, wire.TurnRow) and t.kind != "harness"
    ]
    by_time = sorted((t for t in turns if t.wall_ms is not None), key=lambda t: -t.wall_ms)[:n]
    by_tok = sorted((t for t in turns if t.tok_total is not None), key=lambda t: -t.tok_total)[:n]
    assert b.turns_by_time == by_time and b.turns_by_tokens == by_tok
    for measure, got in (("time", b.rows_by_time), ("cost", b.rows_by_cost), ("tokens", b.rows_by_tokens)):
        assert got == find(key(session), "biggest", measure, limit=n, db=qstore).hits[:n]
    # CLI-21 (D54): the waits are the n longest hits of the `waits` finder
    hits = find(key(session), "waits", limit=1000, db=qstore).hits
    assert [w.dur_ms for w in b.waits] == sorted((h.value for h in hits), reverse=True)[:n]
    assert {(w.start_ms, w.dur_ms) for w in b.waits} <= {(h.start_ms, h.value) for h in hits}
    long = [x for x in stretches_for(_c(qstore), _root(qstore, session)) if x.dur_ms >= 120_000]
    assert [w for w in b.waits if w.row is None] == [
        wire.Wait(**{f.name: getattr(x, f.name) for f in fields(wire.Stretch)}, row=None)
        for x in sorted(long, key=lambda x: (-x.dur_ms, x.start_ms))
    ][: len([w for w in b.waits if w.row is None])]
    # the tool groups are the project-wide view over this session only
    assert b.tools == U.project_slow(only(session.split("/")[1]), n=n, db=qstore).tools
    assert b.freshness == session_detail(key(session), db=qstore).freshness


def _c(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def _root(db, session: str) -> int:
    return rows(db, "SELECT op_rowid FROM sessions WHERE session_id = ?", session.split("/")[1])[0][0]


def test_breakdown_excludes_harness_turns(qstore):
    session = "claude_code/" + SHARNESS.split("/")[1]
    items = session_turns(key(session), db=qstore).items
    assert any(isinstance(t, wire.TurnRow) and t.kind == "harness" for t in items)  # the fixture has one
    b = U.session_breakdown(key(session), 100, db=qstore)
    harness = {t.turn for t in items if isinstance(t, wire.TurnRow) and t.kind == "harness"}
    assert harness.isdisjoint(t.turn for t in b.turns_by_time + b.turns_by_tokens)
    assert all(t.display_id for t in b.turns_by_time)


def test_breakdown_agents_any_depth_by_cost(qstore):
    b = U.session_breakdown(key("claude_code/sagents"), 100, db=qstore)
    ids = [a.row.id for a in b.agents]
    assert NESTED in ids  # an agent started by an agent
    assert sorted(ids) == sorted(
        h.row.id for h in find(key("claude_code/sagents"), "agents", limit=100, db=qstore).hits
    )
    costs = [a.cost_usd for a in b.agents]
    assert costs == sorted(costs, reverse=True)
    top = b.agents[0]
    assert (top.row, top.depth, top.cost_usd) == (
        wire.RowRef("agent", A0),
        0,
        pytest.approx(2.5),
    )  # 2.00 + nested 0.50
    nested = next(a for a in b.agents if a.row.id == NESTED)
    assert nested.depth == 1 and nested.cost_usd == pytest.approx(0.5) and nested.n_llm_calls == 1
    assert len(U.session_breakdown(key("claude_code/sagents"), 2, db=qstore).agents) == 2


def test_breakdown_unknown_session_and_n(qstore):
    with pytest.raises(NotFound):
        U.session_breakdown("claude_code/nosuch/session/-", db=qstore)
    with pytest.raises(QueryError):
        U.session_breakdown(key("claude_code/sagents"), 0, db=qstore)


# ── CLI-22: top_tasks ────────────────────────────────────────────────────────


def owned_calls(db):
    return rows(
        db,
        "SELECT rowid, root_rowid, op_id, tok_total, cost_usd, pre FROM ops "
        "WHERE kind = 'llm_call' AND duplicate_of IS NULL",
    )


def totals(db, scope: Scope):
    roots = set(ex.in_scope_roots(_c(db), scope))
    calls = [c for c in owned_calls(db) if c["root_rowid"] in roots]
    return roots, calls, sum(c["tok_total"] or 0 for c in calls), sum(c["cost_usd"] or 0 for c in calls)


def split_oracle(db, roots, by: str) -> dict[str, float]:
    """tokens per key under `issuing_call_split`, from raw owned rows."""
    out: dict[str, float] = defaultdict(float)
    tools = rows(
        db,
        "SELECT rowid, root_rowid, parent_rowid, tool_name, command_key FROM ops "
        "WHERE kind = 'tool_call' AND duplicate_of IS NULL",
    )
    kids = defaultdict(list)
    for t in tools:
        if t["root_rowid"] in roots:
            kids[t["parent_rowid"]].append(t)
    for c in owned_calls(db):
        if c["root_rowid"] not in roots:
            continue
        mine = kids.get(c["rowid"], [])
        if not mine:
            if by == "tool":
                out["no tool"] += c["tok_total"] or 0
            continue
        for t in mine:
            k = t["tool_name"] if by == "tool" else t["command_key"]
            if k is not None:
                out[k] += (c["tok_total"] or 0) / len(mine)
    return dict(out)


def test_tasks_command_key_scenario(qstore):
    """CLI-22 scenario: the call with 1,000 tokens issued two `uv run pytest` and one Read."""
    t = U.top_tasks(only("stools"), "command_key", db=qstore)
    pytest_group = next(g for g in t.groups if g.key == "uv run pytest")
    assert pytest_group.tok_total == pytest.approx(666.67, abs=0.01)
    assert pytest_group.n == 2
    assert t.basis == "issuing_call_split"
    # the Read's third of the call (333.33) is in `unassigned`, together with the calls that issued no key
    _, calls, total, _ = totals(qstore, only("stools"))
    assert t.scope_totals.tok_total == total
    assert t.unassigned.tok_total >= 333.33 - 0.01
    assert sum(g.tok_total for g in t.groups) + t.unassigned.tok_total == pytest.approx(total)
    assert t.example.row if hasattr(t, "example") else True
    assert pytest_group.example.row.type == "tool"
    assert pytest_group.example.session == key("claude_code/stools")
    # under --by tool the Read has its group and the 2 Bash calls sit in Bash
    by_tool = U.top_tasks(only("stools"), "tool", db=qstore)
    read = next(g for g in by_tool.groups if g.key == "Read")
    assert read.tok_total >= 333.33 - 0.01


@pytest.mark.parametrize("by", ["tool", "command_key"])
def test_tasks_partition_issuing_split(qstore, by):
    for scope in (Scope(show_automated=True), only("stools"), only("sagents")):
        roots, _, total, cost = totals(qstore, scope)
        t = U.top_tasks(scope, by, n=500, db=qstore)
        oracle = split_oracle(qstore, roots, by)
        assert {g.key: g.tok_total for g in t.groups} == pytest.approx(oracle)
        assert sum(g.tok_total or 0 for g in t.groups) + t.unassigned.tok_total == pytest.approx(total)
        assert sum(g.cost_usd or 0 for g in t.groups) + t.unassigned.cost_usd == pytest.approx(cost)
        if by == "tool":
            assert t.unassigned.tok_total == pytest.approx(0)  # every call is in a tool group or "no tool"
        assert t.total_groups == len(t.groups)


def test_tasks_no_tool_group_and_basis_field(qstore):
    """D42: `tool` lists the calls that issued no tool_call as "no tool"; `command_key` does not."""
    scope = Scope(show_automated=True)
    roots, calls, _, _ = totals(qstore, scope)
    issuers = {
        r[0]
        for r in rows(
            qstore, "SELECT DISTINCT parent_rowid FROM ops WHERE kind = 'tool_call' AND duplicate_of IS NULL"
        )
    }
    quiet = [c for c in calls if c["rowid"] not in issuers]
    tool = U.top_tasks(scope, "tool", n=500, db=qstore)
    no_tool = next(g for g in tool.groups if g.key == "no tool")
    assert no_tool.n == len(quiet) and no_tool.dur_ms is None
    assert no_tool.tok_total == pytest.approx(sum(c["tok_total"] or 0 for c in quiet))
    assert "no tool" not in [g.key for g in U.top_tasks(scope, "command_key", n=500, db=qstore).groups]
    expected = {
        "tool": "issuing_call_split", "command_key": "issuing_call_split", "verb": "issuing_call_split",
        "skill": "turn_split", "agent_type": "nearest_engagement", "slash": "turn_attr",
    }  # fmt: skip
    for by, basis in expected.items():
        assert U.top_tasks(scope, by, db=qstore).basis == basis
    verb = U.top_tasks(scope, "verb", n=500, db=qstore)
    assert {g.key for g in verb.groups} <= {
        "run",
        "read",
        "edit",
        "write",
        "search",
        "fetch",
        "spawn",
        "skill",
        "ask-user",
        "other",
    }
    assert verb.unassigned.tok_total == pytest.approx(
        verb.scope_totals.tok_total - sum(g.tok_total for g in verb.groups)
    )


def test_tasks_partition_nearest_engagement(qstore):
    scope = Scope(show_automated=True)
    roots, calls, total, cost = totals(qstore, scope)
    t = U.top_tasks(scope, "agent_type", n=500, db=qstore)
    # oracle: walk up the nested set from every call
    engs = rows(qstore, "SELECT root_rowid, pre, post, agent_type FROM ops WHERE kind = 'engagement'")
    want: dict[str, float] = defaultdict(float)
    n_main = 0
    for c in calls:
        near = [e for e in engs if e["root_rowid"] == c["root_rowid"] and e["pre"] < c["pre"] <= e["post"]]
        if not near:
            want["(main)"] += c["tok_total"] or 0
            n_main += 1
        else:
            want[max(near, key=lambda e: e["pre"])["agent_type"] or "(none)"] += c["tok_total"] or 0
    assert {g.key: g.tok_total for g in t.groups} == pytest.approx(dict(want))
    assert next(g for g in t.groups if g.key == "(main)").n == n_main
    assert t.unassigned.tok_total == pytest.approx(0) and t.unassigned.cost_usd == pytest.approx(0)
    assert sum(g.tok_total for g in t.groups) == pytest.approx(total)
    assert sum(g.cost_usd for g in t.groups) == pytest.approx(cost)
    # n counts engagements (not calls) for an agent type; durations are engagement walls
    n_eng = len(engs)
    assert sum(g.n for g in t.groups if g.key != "(main)") == n_eng


def test_tasks_nested_agent_counts_in_its_own_group(qstore):
    """rn#0 (checker) runs inside ra#0 (reviewer): its 120 tokens are checker's, not reviewer's (CLI-22)."""
    by = {g.key: g for g in U.top_tasks(only("sagents"), "agent_type", n=500, db=qstore).groups}
    assert NESTED.endswith("rn#0") and A0.endswith("ra#0")
    assert by["checker"].tok_total == pytest.approx(120)  # req_rn0
    assert by["reviewer"].tok_total == pytest.approx(240)  # req_ra0 and req_ra1, not req_rn0
    assert by["checker"].n == 1 and by["reviewer"].n == 2  # engagements ra#0, ra#1 / rn#0
    assert by["reviewer"].dur_ms == pytest.approx(270_000 + 75_000)  # their walls


def test_tasks_skill_two_skills_split_evenly(qstore):
    t = U.top_tasks(only("sskills"), "skill", n=500, db=qstore)
    assert t.basis == "turn_split"
    got = {g.key: g for g in t.groups}
    exp = {
        r["op_id"].rsplit("#", 1)[1]: r
        for r in rows(
            qstore,
            "SELECT op_id, attr_tok_total FROM ops WHERE kind='turn' AND op_id LIKE '%sskills%'",
        )
    }
    t0, t1, t2 = exp["0"], exp["1"], exp["2"]
    # main#1 loaded demo:alpha and demo:beta (half each); main#2 loaded demo:alpha; main#0 is the slash turn
    assert got["demo:beta"].tok_total == pytest.approx(t1["attr_tok_total"] / 2)
    assert got["demo:beta"].cost_usd == pytest.approx(0.30)
    assert got["demo:alpha"].tok_total == pytest.approx(t1["attr_tok_total"] / 2 + t2["attr_tok_total"])
    assert got["demo:alpha"].cost_usd == pytest.approx(0.30 + 0.20)
    assert got["mad:bootstrap"].tok_total == pytest.approx(t0["attr_tok_total"])
    assert got["mad:bootstrap"].cost_usd == pytest.approx(0.50)
    assert got["demo:alpha"].n == 2 and got["demo:beta"].n == 1 and got["mad:bootstrap"].n == 1
    # the partition is a lower bound: the three turns' attributed tokens are all there
    assert sum(g.tok_total for g in t.groups) + t.unassigned.tok_total == pytest.approx(
        t.scope_totals.tok_total
    )
    assert sum(g.tok_total for g in t.groups) <= t.scope_totals.tok_total + 1e-9
    assert got["demo:alpha"].example.row.type in ("tool", "turn")


def test_tasks_slash_key_first_word(qstore):
    t = U.top_tasks(only("sskills"), "slash", n=500, db=qstore)
    assert [g.key for g in t.groups] == ["/mad:bootstrap"]  # the label is "/mad:bootstrap ARGS"
    g = t.groups[0]
    assert g.n == 1 and g.cost_usd == pytest.approx(0.50) and g.example.row.type == "turn"
    assert g.dur_ms == pytest.approx(
        rows(qstore, "SELECT span_wall_ms FROM ops WHERE kind='turn' AND op_id LIKE '%sskills%main#0'")[0][0]
    )
    assert t.basis == "turn_attr"
    assert t.unassigned.tok_total == pytest.approx(t.scope_totals.tok_total - g.tok_total)


def test_tasks_order_and_share(qstore):
    scope = Scope(show_automated=True)
    for by in U.BY:
        for measure, attr in (
            ("tokens", "tok_total"),
            ("cost", "cost_usd"),
            ("time", "dur_ms"),
            ("count", "n"),
        ):
            t = U.top_tasks(scope, by, measure, n=500, db=qstore)
            vals = [getattr(g, attr) for g in t.groups]
            have = [v for v in vals if v is not None]
            assert vals[: len(have)] == have  # nulls last
            ordered = sorted(t.groups, key=lambda g: (-(getattr(g, attr) or 0), g.key))
            nonnull = [g for g in ordered if getattr(g, attr) is not None]
            assert [g.key for g in nonnull] == [g.key for g in t.groups[: len(nonnull)]], (by, measure)
            total = {
                "tokens": t.scope_totals.tok_total,
                "cost": t.scope_totals.cost.cost_usd,
                "time": t.scope_totals.dur_ms,
            }.get(measure)
            for g in t.groups:
                v = getattr(g, attr)
                if measure == "count" or v is None:
                    assert g.share is None
                else:
                    assert g.share == pytest.approx(v / total)
    assert U.top_tasks(scope, "tool", n=2, db=qstore).total_groups > 2
    assert len(U.top_tasks(scope, "tool", n=2, db=qstore).groups) == 2


def test_tasks_null_cost_lower_bound(qstore):
    """sunpriced: calls cost 1.00, null and 2.00 (QRY-11)."""
    t = U.top_tasks(only("sunpriced"), "tool", db=qstore)
    g = t.groups[0]
    assert g.key == "no tool" and g.n == 3
    assert (g.cost_usd, g.cost_lower_bound, g.n_unpriced) == (3.0, True, 1)
    assert t.scope_totals.cost == wire.Money(3.0, True, 1)
    assert t.unassigned.cost_usd == pytest.approx(0)
    clean = U.top_tasks(only("sskills"), "tool", db=qstore)
    assert all(not g.cost_lower_bound and g.n_unpriced == 0 for g in clean.groups)
    agent = U.top_tasks(only("sunpriced"), "agent_type", db=qstore)
    assert (agent.groups[0].key, agent.groups[0].cost_lower_bound) == ("(main)", True)


def test_tasks_duplicates_excluded(qstore):
    """sfork copies a call and two tool_calls of stools; owned leaves only (QRY-12)."""
    scope = Scope(show_automated=True)
    t = U.top_tasks(scope, "command_key", n=500, db=qstore)
    sleep = next(g for g in t.groups if g.key == "sleep")
    assert sleep.n == 1  # the interrupted `sleep` call; the fork's copy is not counted
    assert next(g for g in t.groups if g.key == "uv run pytest").n == 2
    owned = rows(
        qstore, "SELECT COUNT(*), SUM(tok_total) FROM ops WHERE kind='llm_call' AND duplicate_of IS NULL"
    )[0]
    assert t.scope_totals.tok_total == owned[1]
    by_tool = U.top_tasks(scope, "tool", n=500, db=qstore)
    n_owned_tools = rows(qstore, "SELECT COUNT(*) FROM ops WHERE kind='tool_call' AND duplicate_of IS NULL")[
        0
    ][0]
    assert sum(g.n for g in by_tool.groups if g.key != "no tool") == n_owned_tools
    assert PYTEST_OK  # the fixture id exists


def test_tasks_validation(qstore):
    with pytest.raises(QueryError):
        U.top_tasks(Scope(), "bogus", db=qstore)
    with pytest.raises(QueryError):
        U.top_tasks(Scope(), "tool", "words", db=qstore)
    for bad in (0, 501):
        with pytest.raises(QueryError):
            U.top_tasks(Scope(), "tool", n=bad, db=qstore)


def test_tasks_empty_scope(qstore):
    t = U.top_tasks(Scope(q="no such session anywhere"), "command_key", db=qstore)
    assert t.groups == [] and t.total_groups == 0
    assert t.scope_totals.tok_total is None and t.unassigned.tok_total is None


def test_views_are_not_stubs():
    assert not any(getattr(f, "__stub__", False) for f in (U.project_slow, U.session_breakdown, U.top_tasks))
    assert STOOLS and SAGENTS and SSLOW1
