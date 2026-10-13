# ruff: noqa: F811
"""Q2b turn views (views/turn.py): QRY-31, QRY-32, QRY-32a; TST-3 item 5 (rows, histogram, span parts).

Expected values come from the fixture timeline (tests/fixtures/qstore, qstore_ids.py), never from the code.
"""

from __future__ import annotations

import pytest
from qstore import qstore  # noqa: F401 (the QF fixture)
from qstore_ids import A0, A1, B0, SAGENTS, SSKILLS, SSTRETCH, STOOLS, turn

from botsworth.query import NotFound, QueryError, _conn, wire
from botsworth.query.views import rows as R
from botsworth.query.views import turn as V
from botsworth.query.views.session import session_detail

ALL = [
    "s2day", "sauto", "sunpriced", "szero", "sagents", "sharness", "sstretch", "stools", "sskills", "sslow1",
    "sslow2", "sfork", "somp", "scodex",
]  # fmt: skip


def sid(key: str) -> str:
    return key.split("/")[1]


def main_turns(db) -> list[str]:
    """Every main turn op_id of the store (harness turns included)."""
    with _conn(db) as conn:
        return [
            r[0]
            for r in conn.execute(
                "SELECT t.op_id FROM ops t JOIN ops s ON s.rowid = t.parent_rowid "
                "WHERE t.kind = 'turn' AND s.kind = 'session' ORDER BY t.rowid"
            )
        ]


def rows_of(db, turn_id: str, **kw) -> wire.TurnRows:
    return V.turn_rows(turn_id, db=db, **kw)


def money_sum(rows: list, kinds: tuple) -> float:
    return sum(r.cost_usd for r in rows if isinstance(r, kinds) and r.cost_usd is not None)


# ── QRY-31 ───────────────────────────────────────────────────────────────────


def test_calls_plus_agents_equal_turn_cost(qstore):
    seen_agent_turns = 0
    for t in main_turns(qstore):
        got = rows_of(qstore, t)
        total = money_sum(got.rows, (wire.CallRow, wire.AgentRow))
        want = got.turn.cost_usd if got.turn.cost_usd is not None else 0.0
        assert total == pytest.approx(want), t
        seen_agent_turns += any(isinstance(r, wire.AgentRow) for r in got.rows)
        assert not [
            r for r in got.rows if isinstance(r, (wire.ToolRow, wire.GapRow)) and hasattr(r, "cost_usd")
        ]
    assert seen_agent_turns >= 3  # sagents T1 and T2, sstretch


def test_tools_with_no_engagement_are_tool_rows(qstore):
    t0 = rows_of(qstore, turn(STOOLS, 0)).rows
    assert [r.type for r in t0 if r.type != "call"] == ["tool", "tool", "tool"]
    t1 = rows_of(qstore, turn(SAGENTS, 0)).rows
    # the two spawn calls caused engagements: they are agent rows, never tool rows
    assert not [r for r in t1 if r.type == "tool"]
    assert sorted(r.caused_by for r in t1 if r.type == "agent") == [
        "claude_code/sagents/tool_call/toolu_spawnA",
        "claude_code/sagents/tool_call/toolu_spawnB",
    ]
    with _conn(qstore) as conn:  # every owned tool_call of the fixture's main calls is a tool or an agent row
        causing = {
            r[0] for r in conn.execute("SELECT DISTINCT caused_by_rowid FROM ops WHERE kind = 'engagement'")
        }
        for t in main_turns(qstore):
            got = rows_of(qstore, t).rows
            ids = {r.id for r in got if r.type == "tool"}
            agents = {r.caused_by for r in got if r.type == "agent"}
            for r in conn.execute(
                "SELECT o.op_id, o.rowid FROM ops o JOIN ops c ON c.rowid = o.parent_rowid "
                "JOIN ops tt ON tt.rowid = c.parent_rowid "
                "WHERE o.kind = 'tool_call' AND o.duplicate_of IS NULL AND tt.op_id = ?",
                (t,),
            ):
                assert (r["op_id"] in agents) == (r["rowid"] in causing), r["op_id"]
                assert (r["op_id"] in ids) == (r["rowid"] not in causing), r["op_id"]


def test_rows_are_in_start_order(qstore):
    for t in main_turns(qstore):
        starts = [r.start_ms for r in rows_of(qstore, t).rows if r.type != "agent"]
        assert starts == sorted(starts), t


def test_gap_rows_attached(qstore):
    got = rows_of(qstore, turn(SAGENTS, 0)).rows
    gaps = [r for r in got if r.type == "gap"]
    # DM-52: stretches of 60 s or more attached by SES-37; both states are rows (QRY-31)
    assert [(g.id, g.state, g.dur_ms) for g in gaps] == [
        ("gap-1791450060000", "agents", 690_000),
        ("gap-1791450750000", "none", 2_250_000),
    ]
    assert all(g.turn == turn(SAGENTS, 0) for g in gaps)
    # T2 starts at 1791453000000: the stretches after it belong to T2 (and "agents"/"none" both appear)
    t2 = [r for r in rows_of(qstore, turn(SAGENTS, 1)).rows if r.type == "gap"]
    assert [g.state for g in t2] == ["none", "agents", "none", "agents"]
    # gap rows sit in time position among the calls
    starts = [r.start_ms for r in rows_of(qstore, turn(SAGENTS, 1)).rows if r.type != "agent"]
    assert starts == sorted(starts)
    # a gap row of a long stretch, with the labels of QRY-41
    t_s = [r for r in rows_of(qstore, turn(SSTRETCH, 2)).rows if r.type == "gap"]
    assert t_s and all(g.dur_ms >= 60_000 for g in t_s)


def test_range_clock_rule_figures(qstore):
    got = rows_of(qstore, turn(SAGENTS, 0), from_ms=1791450000000, to_ms=1791450300000)
    r = got.range
    assert (r.from_ms, r.to_ms) == (1791450000000, 1791450300000)
    # calls that START in the range: c1 0.1, ra0 2.0, rn0 0.5, rb0 0.4 (rb1 starts an hour later)
    assert r.cost.cost_usd == pytest.approx(3.0)
    assert r.cost_main == pytest.approx(0.1) and r.cost_agents == pytest.approx(2.9)
    by_row = {a.row: a for a in r.agents}
    assert by_row[A0].cost_usd == pytest.approx(2.5) and (
        by_row[A0].n_calls_in_range,
        by_row[A0].n_calls,
    ) == (2, 2)
    assert by_row[B0].cost_usd == pytest.approx(0.4) and (
        by_row[B0].n_calls_in_range,
        by_row[B0].n_calls,
    ) == (1, 2)
    assert by_row[B0].tok_total == 120
    # DM-50 over the range: model 5-15 s, tool 15-17 s, agents 60-300 s, nothing 0-5 s and 17-60 s
    assert r.time_split == wire.TimeSplit(10_000, 2_000, 240_000, 48_000)
    # the whole turn is untouched by the range: rows and header stay
    assert got.turn.cost_usd == pytest.approx(3.2)
    # a range after every call: no cost, but the figures exist
    late = rows_of(qstore, turn(SAGENTS, 0), from_ms=1791450800000, to_ms=1791452000000).range
    assert late.cost.cost_usd is None and late.cost_main is None and late.cost_agents is None
    assert [a.n_calls_in_range for a in late.agents] == [0, 0]


def test_range_none_without_params(qstore):
    assert rows_of(qstore, turn(SAGENTS, 0)).range is None


def test_from_without_to_and_inverted_range(qstore):
    with pytest.raises(QueryError, match="together"):
        V.turn_rows(turn(SAGENTS, 0), from_ms=5, db=qstore)
    with pytest.raises(QueryError, match="together"):
        V.turn_rows(turn(SAGENTS, 0), to_ms=5, db=qstore)
    with pytest.raises(QueryError, match="before"):
        V.turn_rows(turn(SAGENTS, 0), from_ms=9, to_ms=5, db=qstore)


def test_turn_header_fields(qstore):
    h = rows_of(qstore, turn(SAGENTS, 0)).turn
    assert (h.cost.main, h.cost.agents) == (pytest.approx(0.1), pytest.approx(3.1))
    assert h.cost.cost_usd == pytest.approx(3.2) and h.cost_main == pytest.approx(0.1)
    assert [(m.model, m.n_calls) for m in h.models_full] == [
        ("synth-model-1", 5)
    ]  # calls attributed to the turn
    assert h.models_full[0].cost_usd == pytest.approx(3.2)
    assert h.text_src.startswith("src://claude_code/") and "#L1-L1" in h.text_src
    assert h.n_turns_in_session == 2 and h.text_bytes is None
    assert (h.display_id, h.kind, h.n_agents) == ("T1", "human", 3)
    # a turn with no call: cost and parts are null (QRY-11)
    with _conn(qstore) as conn:
        empty = conn.execute(
            "SELECT t.op_id FROM ops t JOIN ops s ON s.rowid = t.parent_rowid WHERE t.kind = 'turn' "
            "AND s.kind = 'session' AND t.attr_n_llm_calls = 0 LIMIT 1"
        ).fetchone()
    e = rows_of(qstore, empty[0]).turn
    assert (e.cost.cost_usd, e.cost.main, e.cost.agents) == (None, None, None)
    # harness turns count in no "Turn N of M"
    harness = rows_of(qstore, turn("claude_code/sharness", 1)).turn
    assert harness.kind == "harness" and harness.display_id is None
    assert (
        harness.n_turns_in_session == session_detail(sid("claude_code/sharness"), db=qstore).session.n_turns
    )


def test_non_main_turn_and_unknown_ids(qstore):
    with pytest.raises(QueryError, match="not a main turn"):
        V.turn_rows(A0, db=qstore)
    with pytest.raises(QueryError, match="not a main turn"):
        V.turn_rows("claude_code/sagents/turn/ra#0", db=qstore)  # an agent's own turn
    with pytest.raises(NotFound):
        V.turn_rows("claude_code/sagents/turn/main#99", db=qstore)


def test_turn_accepts_op_id_and_display_id(qstore):
    by_op = rows_of(qstore, turn(SAGENTS, 0))
    assert V.turn_rows("sagents:T1", db=qstore) == by_op  # QRY-2 display id with its session
    assert V.histogram("sagents:T1", db=qstore) == V.histogram(turn(SAGENTS, 0), db=qstore)


def test_stretch_free_turn_split_equals_span_wall(qstore):
    free = 0
    for t in main_turns(qstore):
        got = rows_of(qstore, t)
        h = got.turn
        if h.start_ms is None or h.end_ms is None or h.wall_ms is None:
            continue
        sp = h.time_split
        assert sp.model_ms + sp.tool_ms + sp.agents_ms + sp.none_ms == h.wall_ms, t
        # the range split over the turn's own span rebuilds the stored span figures (DM-53) from the leaves
        rng = V.turn_rows(t, from_ms=h.start_ms, to_ms=h.end_ms, db=qstore).range
        assert rng.time_split == sp, t
        free += not any(r.type == "gap" for r in got.rows)
    assert free >= 5


# ── QRY-32 ───────────────────────────────────────────────────────────────────


def test_agent_rows_depth_paging_total(qstore):
    page = V.agent_rows(A0, db=qstore)
    assert (page.total, page.offset, page.limit) == (2, 0, 500)
    assert [(r.type, r.depth) for r in page.rows] == [
        ("call", 1),
        ("agent", 1),
    ]  # req_ra0, then the nested row
    nested = page.rows[1]
    inner = V.agent_rows(nested.id, db=qstore)
    assert [(r.type, r.depth) for r in inner.rows] == [("call", 2)]
    # paging: total stays, offset and limit slice
    first = V.agent_rows(A0, offset=0, limit=1, db=qstore)
    second = V.agent_rows(A0, offset=1, limit=1, db=qstore)
    assert (first.total, second.total) == (2, 2)
    assert [r.id for r in first.rows + second.rows] == [r.id for r in page.rows]
    assert V.agent_rows(A0, offset=5, db=qstore).rows == []
    # any engagement of the row, and its causing call, name the same row; n_rows = total
    assert (
        V.agent_rows("claude_code/sagents/engagement/rb#1", db=qstore).rows
        == V.agent_rows(B0, db=qstore).rows
    )
    assert V.agent_rows("claude_code/sagents/tool_call/toolu_spawnB", db=qstore).total == 2
    row = next(r for r in rows_of(qstore, turn(SAGENTS, 0)).rows if r.type == "agent" and r.id == A0)
    assert row.n_rows == page.total
    # the cycle reply: A#1 is its own row in the later turn, with its own rows
    assert [r.id for r in V.agent_rows(A1, db=qstore).rows] == ["claude_code/sagents/llm_call/req_ra1"]
    # limit and offset bounds
    for bad in (0, 2001):
        with pytest.raises(QueryError, match="limit"):
            V.agent_rows(A0, limit=bad, db=qstore)
    with pytest.raises(QueryError, match="offset"):
        V.agent_rows(A0, offset=-1, db=qstore)
    assert V.agent_rows(A0, limit=2000, db=qstore).limit == 2000


def test_agent_rows_unattributed_engagement_is_not_found(qstore):
    with pytest.raises(NotFound):
        V.agent_rows("claude_code/sagents/engagement/rc#0", db=qstore)  # caused_by NULL (DM-45)
    with pytest.raises(QueryError, match="not an engagement"):
        V.agent_rows(turn(SAGENTS, 0), db=qstore)


def test_depth_above_three_reports_three(qstore):
    with _conn(qstore) as conn:
        root = conn.execute("SELECT root_rowid FROM ops WHERE op_id = ?", (A0,)).fetchone()[0]
        forest = R.Forest(conn, root)
        turn_rowid = conn.execute("SELECT rowid FROM ops WHERE op_id = ?", (turn(SAGENTS, 0),)).fetchone()[0]
        builder = R.RowBuilder(conn, root, forest, R.scan_turn(conn, forest, turn_rowid))
        cause = conn.execute("SELECT caused_by_rowid FROM ops WHERE op_id = ?", (A0,)).fetchone()[0]
        deep = builder.build(builder.group_entries(cause), 7)
    assert {r.depth for r in deep} == {3}


# ── QRY-32a ──────────────────────────────────────────────────────────────────


def histogram(db, scope, **kw) -> wire.Histogram:
    return V.histogram(scope, db=db, **kw)


def test_histogram_session_sum_equals_cost(qstore):
    for name in ALL:
        d = session_detail(name, db=qstore).session
        h = histogram(qstore, name)
        assert len(h.bins) == 600
        assert sum(b.cost_usd for b in h.bins) == pytest.approx(d.cost.cost_usd or 0.0), name
        assert sum(b.tok_total for b in h.bins) == (d.tok_total or 0), name
        assert h.tail_from_ms is None and h.start_ms == d.start_ms
        assert not any(hasattr(b, "by_model") for b in h.bins)  # a session bin has no by_model key


def test_histogram_main_cost_le_cost_and_sums_to_main_thread(qstore):
    for name in ALL:
        d = session_detail(name, db=qstore).session
        h = histogram(qstore, name)
        assert sum(b.main_cost_usd for b in h.bins) == pytest.approx(d.cost.main or 0.0), name
        assert all(b.main_cost_usd <= b.cost_usd + 1e-12 for b in h.bins), name
    # sagents: the main-thread calls c1, c2 and c2b cost 0.3 of 4.7; the agents' calls are in cost_usd only
    h = histogram(qstore, "sagents")
    assert sum(b.main_cost_usd for b in h.bins) == pytest.approx(0.3)
    assert sum(b.cost_usd for b in h.bins) == pytest.approx(4.7)
    # turn scope: the turn's own main-thread calls
    t = histogram(qstore, turn(SAGENTS, 0))
    assert sum(b.main_cost_usd for b in t.bins) == pytest.approx(0.1)
    assert sum(b.cost_usd for b in t.bins) == pytest.approx(3.2)


def test_histogram_turn_includes_agents(qstore):
    # TRN-22: the bars include the calls of every agent the turn caused
    h = histogram(qstore, turn(SAGENTS, 0), bins=12)
    assert sum(b.cost_usd for b in h.bins) == pytest.approx(3.2)
    assert sum(b.cost_usd for b in h.bins) > 0.1 * 10  # far more than the main thread's 0.1
    # the next turn holds the reply A#1 and its own main calls only
    h2 = histogram(qstore, turn(SAGENTS, 1))
    assert sum(b.cost_usd for b in h2.bins) == pytest.approx(1.2)


def test_turn_histogram_tail_zone_sum_equals_turn_cost(qstore):
    checked = 0
    for t in main_turns(qstore):
        with _conn(qstore) as conn:
            row = conn.execute(
                "SELECT start_ms, end_ms, attr_cost_usd FROM ops WHERE op_id = ?", (t,)
            ).fetchone()
        if row["start_ms"] is None:
            continue
        h = histogram(qstore, t)
        assert len(h.bins) == 120 and h.start_ms == row["start_ms"]
        assert sum(b.cost_usd for b in h.bins) == pytest.approx(row["attr_cost_usd"] or 0.0), t
        checked += 1
    assert checked >= 20
    t1 = histogram(qstore, turn(SAGENTS, 0))
    assert t1.tail_from_ms == 1791450017000  # the turn's end_ms: agents run on to 1791454050000
    assert t1.start_ms + t1.bin_ms * len(t1.bins) >= 1791454050000
    assert histogram(qstore, turn("claude_code/s2day", 0)).tail_from_ms is None  # no agent outlives it


def test_histogram_by_model_turn_only(qstore):
    h = histogram(qstore, turn(SAGENTS, 0), bins=12)
    assert all(isinstance(b, wire.HistBinByModel) for b in h.bins)
    for b in h.bins:
        assert sum(c for c, _ in b.by_model.values()) == pytest.approx(b.cost_usd)
        assert sum(t for _, t in b.by_model.values()) == b.tok_total
    assert isinstance(h.marks, wire.HistMarks) and not hasattr(h.marks, "turn_starts")
    s = histogram(qstore, sid(SAGENTS))
    assert all(isinstance(b, wire.HistBin) for b in s.bins) and isinstance(s.marks, wire.SessionHistMarks)


def test_state_ms_per_bin_sums_to_bin_overlap(qstore):
    # sagents has no resume gap: every bin's states sum to the bin's overlap with the range
    h = histogram(qstore, "sagents", bins=7)
    first, last = h.start_ms, 1791454060000
    for i, b in enumerate(h.bins):
        lo, hi = first + i * h.bin_ms, min(first + (i + 1) * h.bin_ms, last)
        s = b.state_ms
        assert s.model_ms + s.tool_ms + s.agents_ms + s.none_ms == max(hi - lo, 0), i
    # a brushed range clips the bins at its end
    r = histogram(qstore, "sagents", bins=4, from_ms=1791450000000, to_ms=1791450100000)
    assert r.start_ms == 1791450000000 and r.bin_ms == 25_000
    assert [sum(vars(b.state_ms).values()) for b in r.bins] == [25_000] * 4
    # the stored split is rebuilt: over the whole range the states equal sessions.model_ms..none_ms (DM-51)
    for name in ALL:
        d = session_detail(name, db=qstore).session
        h = histogram(qstore, name)
        got = [
            sum(getattr(b.state_ms, k) for b in h.bins)
            for k in ("model_ms", "tool_ms", "agents_ms", "none_ms")
        ]
        want = [d.time_split.model_ms, d.time_split.tool_ms, d.time_split.agents_ms, d.time_split.none_ms]
        assert got == want, name
        assert sum(got) == d.wall_ms, name  # a resume gap (sfork) is outside the wall, so not in any state


def test_marks_turn_starts_and_skill_loads(qstore):
    h = histogram(qstore, sid(SSKILLS))
    assert [(m.display_id, m.turn) for m in h.marks.turn_starts] == [
        ("T1", turn(SSKILLS, 0)),
        ("T2", turn(SSKILLS, 1)),
        ("T3", turn(SSKILLS, 2)),
    ]
    assert [m.start_ms for m in h.marks.turn_starts] == sorted(m.start_ms for m in h.marks.turn_starts)
    assert [(m.skill, m.row.type) for m in h.marks.skill_loads] == [
        ("mad:bootstrap", "turn"),  # the slash event of turn 0 has no step: the turn is the anchor (DM-30)
        ("demo:alpha", "tool"),
        ("demo:beta", "tool"),
        ("demo:alpha", "tool"),
    ]
    t = histogram(qstore, turn(SSKILLS, 1))
    assert [m.skill for m in t.marks.skill_loads] == ["demo:alpha", "demo:beta"]
    assert t.marks.skill_loads[0].row.id == "claude_code/sskills/tool_call/toolu_sk1"
    # harness turns tick without a display id
    hs = histogram(qstore, "sharness")
    assert any(m.display_id is None for m in hs.marks.turn_starts)
    # a range keeps the marks inside it
    later = histogram(
        qstore, sid(SSKILLS), from_ms=t.marks.skill_loads[0].start_ms, to_ms=h.start_ms + 10_000_000
    )
    assert [m.skill for m in later.marks.skill_loads] == ["demo:alpha", "demo:beta", "demo:alpha"]


def test_histogram_stretches_in_range(qstore):
    h = histogram(qstore, "sagents")
    assert [(s.state, s.dur_ms) for s in h.stretches][:2] == [("agents", 690_000), ("none", 2_250_000)]
    narrow = histogram(qstore, "sagents", from_ms=1791450800000, to_ms=1791452000000)
    assert [(s.state, s.start_ms) for s in narrow.stretches] == [
        ("none", 1791450750000)
    ]  # overlaps, not clipped


def test_bins_bounds_400(qstore):
    for bad in (0, 1001, -3):
        with pytest.raises(QueryError, match="bins"):
            V.histogram("sagents", bins=bad, db=qstore)
    assert len(V.histogram("sagents", bins=1, db=qstore).bins) == 1
    assert len(V.histogram("sagents", bins=1000, db=qstore).bins) == 1000
    with pytest.raises(QueryError, match="together"):
        V.histogram("sagents", from_ms=1, db=qstore)
    with pytest.raises(QueryError, match="not a session or a main turn"):
        V.histogram(A0, db=qstore)
    with pytest.raises(NotFound):
        V.histogram("nosuchsession-1234", db=qstore)


def test_zero_length_turn_histogram(qstore):
    # a turn whose start equals its end still gets `bins` bins of at least 1 ms
    with _conn(qstore) as conn:
        z = conn.execute(
            "SELECT t.op_id FROM ops t JOIN ops s ON s.rowid = t.parent_rowid WHERE t.kind = 'turn' "
            "AND s.kind = 'session' AND t.start_ms = t.end_ms LIMIT 1"
        ).fetchone()
    assert z is not None
    h = histogram(qstore, z[0], bins=10)
    assert len(h.bins) == 10 and h.bin_ms == 1
