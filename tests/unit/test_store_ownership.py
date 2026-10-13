"""Invariant 5: one owner per (provider, kind, occ_key), independent of load order (design §4.1, §4.4)."""

import itertools

import pytest
from store_builders import T0, B, owners_ok, put, row, store


def shared(sid, start, keys=("k1", "k2"), cost=1.0, **kw):
    b = B(sid, start=start, end=start + 100, **kw)
    t = b.turn("main#0", start=start, end=start + 100)
    for i, k in enumerate(keys):
        b.llm(f"{k}@{sid}", t, start + i * 10, start + i * 10 + 5, cost=cost, occ=k)
    return b


def dump(conn):
    rows = conn.execute(
        "SELECT op_id, duplicate_of, incl_cost_usd, incl_tok_total, n_duplicates, n_llm_calls,"
        " active_ms, wall_ms FROM ops ORDER BY op_id"
    ).fetchall()
    roll = conn.execute(
        "SELECT o.op_id, r.bucket_start_ms, r.n_llm_calls, r.cost_usd FROM rollup_time r"
        " JOIN ops o ON o.rowid = r.root_rowid ORDER BY 1, 2"
    ).fetchall()
    return [tuple(r) for r in rows], [tuple(r) for r in roll]


def test_result_independent_of_load_order(tmp_path):
    bundles = {"a": shared("a", 0), "b": shared("b", 1000, keys=("k1", "k3")), "c": shared("c", 500)}
    results = set()
    for i, perm in enumerate(itertools.permutations("abc")):
        d = tmp_path / str(i)
        d.mkdir()
        conn = store(d)
        put(conn, *(bundles[x] for x in perm))
        owners_ok(conn)
        results.add(repr(dump(conn)))
    assert len(results) == 1
    conn = store(tmp_path / "0")
    assert row(conn, "claude_code/a/llm_call/k1@a")["duplicate_of"] is None
    assert row(conn, "claude_code/b/llm_call/k1@b")["duplicate_of"] == "claude_code/a/llm_call/k1@a"
    assert row(conn, "claude_code/c/llm_call/k2@c")["duplicate_of"] == "claude_code/a/llm_call/k2@a"
    assert row(conn, "claude_code/b/llm_call/k3@b")["duplicate_of"] is None  # only b has k3


def test_I5_later_owner_flips_and_recomputes_both_roots(tmp_path):
    conn = store(tmp_path)
    late, early = shared("late", 1000), shared("early", 0)
    put(conn, late)
    assert row(conn, late.session)["incl_cost_usd"] == 2.0
    (result,) = put(conn, early)
    assert (result.flips, result.refolded) == (2, ["claude_code/late"])
    s_late = row(conn, late.session)
    assert (s_late["incl_cost_usd"], s_late["n_duplicates"], s_late["n_llm_calls"]) == (None, 2, 0)
    assert row(conn, early.session)["incl_cost_usd"] == 2.0
    late_roll = conn.execute("SELECT count(*) FROM rollup_time WHERE root_rowid=?", (s_late["rowid"],))
    assert late_roll.fetchone()[0] == 0
    sess = conn.execute("SELECT incl_cost_usd, n_duplicates FROM sessions WHERE session_id='late'").fetchone()
    assert tuple(sess) == (None, 2)
    owners_ok(conn)


def test_copies_repoint_to_the_newest_best_owner(tmp_path):
    conn = store(tmp_path)
    put(conn, shared("c", 2000), shared("b", 1000), shared("a", 0))
    for sid in "bc":
        assert (
            row(conn, f"claude_code/{sid}/llm_call/k1@{sid}")["duplicate_of"] == "claude_code/a/llm_call/k1@a"
        )
    owners_ok(conn)


def test_reload_without_a_key_promotes_the_best_remaining_copy(tmp_path):
    conn = store(tmp_path)
    put(conn, shared("a", 0), shared("b", 1000))
    assert row(conn, "claude_code/b/session/-")["incl_cost_usd"] is None
    (result,) = put(conn, shared("a", 0, keys=("k2",)))
    assert result.promotions == 1 and result.refolded == ["claude_code/b"]
    assert row(conn, "claude_code/b/llm_call/k1@b")["duplicate_of"] is None
    assert row(conn, "claude_code/b/session/-")["incl_cost_usd"] == 1.0
    owners_ok(conn)


def within_session():
    """One session, one key seen by main (depth 0), a fork at depth 1, and a nested agent at depth 2."""
    b = B("s", start=0, end=100)
    t = b.turn("main#0")
    b.llm("deep", b.turn("x#0", agent="x"), 5, 6, occ="k", spawn_depth=2, agent="x")
    b.llm("fork", b.turn("f#0", agent="f"), 1, 2, occ="k", spawn_depth=1, is_fork=True, agent="f")
    b.llm("norm", b.turn("n#0", agent="n"), 3, 4, occ="k", spawn_depth=1, agent="n")
    return b, t


def test_rank_spawn_depth_then_non_fork_then_first_ts(tmp_path):
    b, t = within_session()
    conn = store(tmp_path)
    put(conn, b)
    assert (
        row(conn, b.id("llm_call", "norm"))["duplicate_of"] is None
    )  # depth 1 non-fork beats fork and depth 2
    b2, t2 = within_session()
    b2.llm("main", t2, 50, 51, occ="k", spawn_depth=0)  # latest, but the main transcript
    (tmp_path / "2").mkdir()
    conn = store(tmp_path / "2")
    put(conn, b2)
    assert row(conn, b2.id("llm_call", "main"))["duplicate_of"] is None
    assert row(conn, b2.id("llm_call", "norm"))["duplicate_of"] == b2.id("llm_call", "main")


def test_adapter_hint_mismatch_is_counted_not_obeyed(tmp_path):
    conn = store(tmp_path)
    put(conn, shared("late", 1000))
    early = shared("early", 0)
    early.ops[2].duplicate_of = "claude_code/late/llm_call/k1@late"  # wrong hint: early session owns
    (result,) = put(conn, early)
    assert result.disagreements == 1
    assert row(conn, early.ops[2].op_id)["duplicate_of"] is None


@pytest.mark.parametrize("untimed_first", [True, False])
def test_missing_timestamps_rank_last(tmp_path, untimed_first):
    a = B("a", start=None, end=None)
    a.llm("x@a", a.turn("main#0", start=None, end=None), None, None, occ="x")
    b = shared("b", 0, keys=("x",))
    conn = store(tmp_path)
    put(conn, *((a, b) if untimed_first else (b, a)))
    assert row(conn, "claude_code/b/llm_call/x@b")["duplicate_of"] is None
    owners_ok(conn)


def test_first_line_ms_ranks_before_start_ms(tmp_path):
    # D23: a resumed copy shares the original's start_ms; the first timestamped line decides
    orig = shared("orig", 0, first_line_ms=T0 + 10)
    copy = shared("copy", 0, first_line_ms=T0 + 20)
    conn = store(tmp_path)
    put(conn, copy, orig)
    assert row(conn, "claude_code/orig/llm_call/k1@orig")["duplicate_of"] is None
    assert (
        row(conn, "claude_code/copy/llm_call/k1@copy")["duplicate_of"] == "claude_code/orig/llm_call/k1@orig"
    )
    nulls = shared("nofl", -5)  # null first_line_ms falls back to start_ms: earlier than both
    put(conn, nulls)
    assert (
        row(conn, "claude_code/orig/llm_call/k1@orig")["duplicate_of"] == "claude_code/nofl/llm_call/k1@nofl"
    )


def test_events_on_copied_ops_flagged_and_follow_flips(tmp_path):
    late, early = shared("late", 1000), shared("early", 0)
    late.event("api_error", op_id=late.id("llm_call", "k1@late"), ts=1000)
    late.event("api_error", ts=1001, in_copied_op=True)
    conn = store(tmp_path)
    put(conn, late)
    assert [r[0] for r in conn.execute("SELECT copied FROM events ORDER BY event_id")] == [0, 1]
    put(conn, early)  # flip: late's op becomes a copy -> its event is copied
    assert [r[0] for r in conn.execute("SELECT copied FROM events ORDER BY event_id")] == [1, 1]


def test_leaves_without_occ_key_use_duplicate_of_fallback(tmp_path):
    a = shared("a", 0)
    b = shared("b", 1000)
    for x in (a, b):
        for o in x.ops:
            o.occ_key = None
    b.ops[2].duplicate_of = a.ops[2].op_id  # adapter-declared copy
    conn = store(tmp_path)
    ra, rb = put(conn, b, a)
    assert (ra.missing_occ_key, rb.missing_occ_key) == (2, 2)
    assert row(conn, b.ops[2].op_id)["duplicate_of"] == a.ops[2].op_id
    assert row(conn, b.ops[3].op_id)["duplicate_of"] is None
    owners_ok(conn)
