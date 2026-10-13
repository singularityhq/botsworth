"""store.load: atomic replace, invariants 1-4 and 6, closure, rollup_time, D19 segments."""

import pytest
from store_builders import T0, B, H, owners_ok, put, row, store

from botsworth.store import load
from botsworth.stream import StreamDefects

ADDITIVE_SQL = [
    ("incl_in_tok", "in_tok"),
    ("incl_out_tok", "out_tok"),
    ("incl_cache_read_tok", "cache_read_tok"),
    ("incl_tok_total", "tok_total"),
    ("incl_cost_usd", "cost_usd"),
]


def nested():
    """main turn -> llm -> Agent tool -> engagement -> turn -> 2 llm calls (one unpriced, estimated time)."""
    b = B("n", start=0, end=1000)
    t = b.turn("main#0", start=0, end=1000)
    c = b.llm("r0", t, 0, 100, tokens=(10, 20, 30, 40), cost=2.0)
    spawn = b.tool("t0", c, 100, 900, tool_name="Agent")
    e = b.engagement("a#0", spawn, "a", start=110, end=800, agent_type="coder")
    t2 = b.turn("a#0", e, start=110, end=800, agent="a")
    b.llm("ra", t2, 110, 400, tokens=(1, 2, 3, 4), cost=0.5, agent="a")
    b.llm("rb", t2, 500, 800, tokens=(5, 6, 0, 0), cost=None, agent="a", time_prov="estimated")
    b.llm("r1", t, 950, 1000, tokens=(7, 8, 0, 0), cost=1.0)
    b.step(t, c, "say")
    b.step(t, spawn, "spawn")
    return b


def test_inclusive_equals_children_plus_own_everywhere(tmp_path):
    conn = store(tmp_path)
    put(conn, nested())
    for incl, own in ADDITIVE_SQL:
        bad = conn.execute(
            f"SELECT p.op_id FROM ops p WHERE coalesce(p.{incl},0)"
            f" != coalesce((SELECT sum(c.{incl}) FROM ops c WHERE c.parent_rowid=p.rowid),0)"
            f" + (CASE WHEN p.kind IN ('llm_call','tool_call')"
            f" AND p.duplicate_of IS NULL THEN coalesce(p.{own},0) ELSE 0 END)"
        ).fetchall()
        assert bad == [], incl
    s = row(conn, "claude_code/n/session/-")
    assert s["incl_tok_total"] == 100 + 10 + 11 + 15
    assert s["incl_cost_usd"] == 3.5 and s["cost_lower_bound"] == 1
    assert (s["incl_tok_prov"], s["incl_time_prov"]) == ("present", "estimated")  # weakest of the subtree
    assert (s["n_llm_calls"], s["n_tool_calls"], s["n_engagements"], s["n_steps"]) == (4, 1, 1, 2)
    eng = row(conn, "claude_code/n/engagement/a#0")
    assert eng["path_sig"] == "main/coder/engagement"
    assert row(conn, "claude_code/n/llm_call/ra")["path_sig"] == "main/coder/m1"


def test_time_metrics_on_nested_tree(tmp_path):
    conn = store(tmp_path)
    put(conn, nested())
    t = row(conn, "claude_code/n/turn/main#0")
    # own: r0 [0,100], t0 [100,900], r1 [950,1000]; agent: ra [110,400], rb [500,800]
    assert t["busy_sum_ms"] == 100 + 800 + 50 + 290 + 300
    assert t["active_ms"] == 950 and t["wall_ms"] == 1000 and t["max_parallel"] == 2
    assert (t["blocked_ms"], t["idle_ms"]) == (0, 50)  # gap 900..950 has no agent activity
    a = row(conn, "claude_code/n/turn/a#0")
    assert (a["active_ms"], a["blocked_ms"], a["idle_ms"]) == (590, 0, 100)
    for r in conn.execute("SELECT busy_sum_ms, active_ms, wall_ms FROM ops"):
        assert r["busy_sum_ms"] >= r["active_ms"] <= r["wall_ms"]


def test_nested_sets_and_rollup_time_match_the_tree(tmp_path):
    conn = store(tmp_path)
    put(conn, nested())
    n_pairs = conn.execute(  # (ancestor, descendant) pairs incl. self, from the DM-15 intervals
        "SELECT count(*) FROM ops a JOIN ops d"
        " ON d.root_rowid = a.root_rowid AND d.pre BETWEEN a.pre AND a.post"
    ).fetchone()[0]
    assert n_pairs == conn.execute("SELECT sum(depth + 1) FROM ops").fetchone()[0]
    s = row(conn, "claude_code/n/session/-")
    got = conn.execute(
        "SELECT sum(cost_usd), sum(tok_total), sum(n_llm_calls), sum(n_unpriced) FROM rollup_time"
        " WHERE root_rowid=?",
        (s["rowid"],),
    ).fetchone()
    assert tuple(got) == (3.5, s["incl_tok_total"], 4, 1)
    starts = [r[0] for r in conn.execute("SELECT bucket_start_ms FROM rollup_time")]
    assert starts and all(t % 900_000 == 0 and t <= T0 + 3_600_000 for t in starts)
    agent_types = {r[0] for r in conn.execute("SELECT agent_type FROM rollup_time WHERE n_llm_calls > 0")}
    assert agent_types == {None, "coder"}


def test_resume_segments_d19(tmp_path):
    # omp-style: session file has two segments 27 h apart; the agent file one segment.
    b = B(
        "r",
        provider="omp",
        start=0,
        end=27 * H + 10_000,
        segments=[[T0, T0 + 10_000], [T0 + 27 * H, T0 + 27 * H + 10_000]],
    )
    t1 = b.turn("main#0", start=0, end=10_000, segments=[[T0, T0 + 10_000]])
    c = b.llm("r0", t1, 0, 1_000)
    sp = b.tool("t0", c, 1_000, 2_000, tool_name="task")
    e = b.engagement("x#0", sp, "x", start=1_000, end=8_000, segments=[[T0 + 1_000, T0 + 8_000]])
    b.llm("rx", b.turn("x#0", e, start=1_000, end=8_000, agent="x"), 1_500, 7_000, agent="x")
    t2 = b.turn("main#1", start=27 * H, end=27 * H + 10_000, segments=[[T0 + 27 * H, T0 + 27 * H + 10_000]])
    b.llm("r1", t2, 27 * H + 500, 27 * H + 9_000)
    conn = store(tmp_path)
    put(conn, b)
    s = row(conn, b.session)
    assert s["wall_ms"] == 20_000  # the 27 h gap is outside wall
    assert s["active_ms"] == 7_000 + 8_500  # r0, t0 and the agent's rx overlap into [0, 7 s]
    # main transcript gap 2 s .. 27 h + 0.5 s: the agent covers 5 s of it, the resume gap is idle
    assert s["blocked_ms"] == 5_000 and s["idle_ms"] == 27 * H + 500 - 2_000 - 5_000
    assert s["active_ms"] <= s["wall_ms"]


def test_inverted_leaf_dropped_from_time_and_survives_refold(tmp_path):
    late = B("late", start=1000, end=2000)
    t = late.turn("main#0", start=1000, end=2000)
    late.llm("bad", t, 1500, 1200)  # end < start
    late.llm("shared@late", t, 1600, 1700, occ="shared")
    conn = store(tmp_path)
    (r,) = put(conn, late)
    assert r.n_inverted == 1
    bad = row(conn, late.id("llm_call", "bad"))
    assert (bad["end_ms"], bad["dur_ms"], bad["time_prov"], bad["n_inverted"]) == (
        bad["start_ms"],
        None,
        "missing",
        1,
    )
    early = B("early", start=0, end=100)
    early.llm("shared@early", early.turn("main#0"), 10, 20, occ="shared")
    (r2,) = put(conn, early)  # flips "shared" -> refolds "late" from stored rows
    assert r2.refolded == ["claude_code/late"]
    s = row(conn, late.session)
    assert s["n_inverted"] == 1 and s["incl_time_prov"] == "missing" and s["active_ms"] == 0


def test_rejected_bundle_keeps_previous_rows(tmp_path):
    conn = store(tmp_path)
    put(conn, nested())
    before = conn.execute("SELECT count(*), sum(incl_cost_usd) FROM ops").fetchone()
    deep = nested()
    parent = deep.session
    for i in range(33):
        parent = deep.turn(f"deep#{i}", parent)
    with pytest.raises(load.BundleInvalid, match="depth > 32"):
        put(conn, deep)
    cyc = nested()
    cyc.ops[1].parent = cyc.ops[2].op_id  # turn <-> llm cycle, unreachable from the root
    with pytest.raises(load.BundleInvalid, match="cycle"):
        put(conn, cyc)
    dangling = nested()
    dangling.ops[-1].parent = "nowhere"
    with pytest.raises(StreamDefects):
        put(conn, dangling)
    assert tuple(conn.execute("SELECT count(*), sum(incl_cost_usd) FROM ops").fetchone()) == tuple(before)
    assert not conn.in_transaction


def test_op_id_held_by_another_bundle_is_rejected(tmp_path):
    conn = store(tmp_path)
    put(conn, nested())
    other = nested().bundle()
    other.bundle_id = "elsewhere"  # same op_ids, different bundle file
    with pytest.raises(load.BundleInvalid):
        put(conn, other)
    assert conn.execute("SELECT count(DISTINCT bundle) FROM ops_v").fetchone()[0] == 1


def test_reload_replaces_and_reuses_rowids(tmp_path):
    conn = store(tmp_path)
    put(conn, nested())
    ids = dict(conn.execute("SELECT op_id, rowid FROM ops"))
    counts = [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("ops", "steps")]
    nested_sets = conn.execute("SELECT op_id, pre, post FROM ops ORDER BY rowid").fetchall()
    put(conn, nested())
    assert dict(conn.execute("SELECT op_id, rowid FROM ops")) == ids
    assert [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("ops", "steps")] == counts
    assert conn.execute("SELECT op_id, pre, post FROM ops ORDER BY rowid").fetchall() == nested_sets
    owners_ok(conn)


def test_session_metadata_copied_to_sessions_row(tmp_path):
    b = B("m", title="Fix cost", git_branch="main", harness_version="2.1", first_line_ms=T0 + 3)
    b.llm("r0", b.turn("main#0"), 0, 5)
    conn = store(tmp_path)
    put(conn, b)
    s = conn.execute("SELECT title, git_branch, harness_version, first_line_ms FROM sessions").fetchone()
    assert tuple(s) == ("Fix cost", "main", "2.1", T0 + 3)


def test_rollup_provenance_counts_equal_the_op_scan(tmp_path):
    """v6: n_tok_missing and n_time_* per bucket equal a count over owned llm_calls."""
    b = nested()
    t = b.turn("main#1", start=2000, end=3000)
    b.llm("rm", t, 2000, 2100, tokens=(None, None, None, None), cost=None, tok_prov="missing")
    b.llm("rd", t, 2100, 2200, time_prov="derived")
    b.llm("rx", t, 2200, 2300, time_prov="missing")
    conn = store(tmp_path)
    put(conn, b)
    scan = conn.execute(
        "SELECT sum(tok_prov = 'missing' OR tok_total IS NULL),"
        " sum(dur_ms IS NOT NULL AND time_prov = 'derived'),"
        " sum(dur_ms IS NOT NULL AND time_prov = 'estimated'),"
        " sum(dur_ms IS NULL OR time_prov = 'missing')"
        " FROM ops WHERE kind = 'llm_call' AND duplicate_of IS NULL AND start_ms IS NOT NULL"
    ).fetchone()
    assert tuple(scan) == (1, 1, 1, 1)
    got = conn.execute(
        "SELECT sum(n_tok_missing), sum(n_time_derived), sum(n_time_estimated), sum(n_time_missing)"
        " FROM rollup_time"
    ).fetchone()
    assert tuple(got) == tuple(scan)
    tools = conn.execute("SELECT sum(n_tok_missing) FROM rollup_time WHERE n_llm_calls = 0").fetchone()[0]
    assert tools in (None, 0)  # counts cover llm_calls only
