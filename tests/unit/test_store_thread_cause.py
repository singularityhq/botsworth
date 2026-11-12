"""Schema v13: thread, position, gap and the cause edge of every llm_call (data-model.md §2.2, DM-45)."""

import pytest
from store_builders import B, put, row, store

from botsworth import stream
from botsworth.store import derive, load

COLUMNS = ("thread_rowid", "thread_pos", "gap_ms", "caused_by_rowid", "caused_by_basis")


def facts(conn, op_id):
    r = row(conn, op_id)
    return {c: r[c] for c in COLUMNS} | {"caused_by": r["caused_by"]}


def thread_session(sid="s", provider="claude_code"):
    """Main thread c1, c2, c3 over turns t1, t2 and an agent run a1, a2 that c1's tool k1 started.

    c1 1000-2000, k1 2000-3000 (spawns the agent), c2 5000-6000, c3 8500-9000; agent calls a1 2100-2200 and
    a2 2300-2400 under the agent turn `ta` of engagement e1.
    """
    b = B(sid, provider=provider, start=0, end=20_000)
    t1 = b.turn("t1", start=0, end=7000, turn_kind="human")
    c1 = b.llm("c1", t1, 1000, 2000)
    k1 = b.tool("k1", c1, 2000, 3000)
    e1 = b.engagement("e1", k1, "a1", start=2100, end=2400, caused_by=k1, caused_by_basis="id")
    ta = b.turn("ta", e1, start=2100, end=2400, agent="a1", turn_kind="message", turn_origin="agent")
    b.llm("a1", ta, 2100, 2200, agent="a1")
    b.llm("a2", ta, 2300, 2400, agent="a1")
    b.llm("c2", t1, 5000, 6000)
    t2 = b.turn("t2", start=8000, end=9500, turn_kind="human")
    b.llm("c3", t2, 8500, 9000)
    return b


def id_of(b, kind, key):
    return b.id(kind, key)


def test_thread_is_nearest_engagement_ancestor_else_session(tmp_path):
    conn = store(tmp_path)
    b = thread_session()
    put(conn, b)
    session = row(conn, b.session)["rowid"]
    e1 = row(conn, b.id("engagement", "e1"))["rowid"]
    in_main = ("c1", "c2", "c3")
    assert {row(conn, b.id("llm_call", k))["thread_rowid"] for k in in_main} == {session}
    assert {row(conn, b.id("llm_call", k))["thread_rowid"] for k in ("a1", "a2")} == {e1}
    assert row(conn, b.session)["thread_rowid"] == session
    # the engagement sits in the thread of the call that spawned it; it opens a thread, it is not in it
    assert row(conn, b.id("engagement", "e1"))["thread_rowid"] == session
    assert row(conn, b.id("tool_call", "k1"))["thread_rowid"] == session


def test_thread_pos_counts_calls_in_the_thread_by_start_then_rowid(tmp_path):
    conn = store(tmp_path)
    b = thread_session()
    put(conn, b)
    pos = {k: row(conn, b.id("llm_call", k))["thread_pos"] for k in ("c1", "c2", "c3", "a1", "a2")}
    assert pos == {"c1": 1, "c2": 2, "c3": 3, "a1": 1, "a2": 2}
    assert row(conn, b.id("turn", "t1"))["thread_pos"] == 0  # no call at or before the turn's start
    assert row(conn, b.id("turn", "t2"))["thread_pos"] == 2  # c1 and c2 start before 8000
    assert row(conn, b.id("turn", "ta"))["thread_pos"] == 0  # a1 starts at the same ms, higher rowid


def test_tool_call_gets_the_position_of_the_call_that_issued_it(tmp_path):
    conn = store(tmp_path)
    b = thread_session()
    put(conn, b)
    k1 = row(conn, b.id("tool_call", "k1"))
    assert (k1["thread_pos"], k1["gap_ms"]) == (1, None)
    # the agent run sits under the issuing call's thread position as well
    assert row(conn, b.id("engagement", "e1"))["thread_pos"] == 1


def test_calls_with_one_start_ms_are_ordered_by_rowid(tmp_path):
    conn = store(tmp_path)
    b = B("tie", start=0, end=5000)
    t = b.turn("t", start=0, end=5000, turn_kind="human")
    b.llm("z-first", t, 1000, 1500)  # lower rowid, op_id sorts last
    b.llm("a-second", t, 1000, 1600)
    put(conn, b)
    assert row(conn, b.id("llm_call", "z-first"))["thread_pos"] == 1
    assert row(conn, b.id("llm_call", "a-second"))["thread_pos"] == 2
    assert row(conn, b.id("llm_call", "a-second"))["caused_by"] == b.id("llm_call", "z-first")


def test_gap_is_start_minus_previous_end_and_first_call_is_null(tmp_path):
    conn = store(tmp_path)
    b = thread_session()
    put(conn, b)
    gap = {k: row(conn, b.id("llm_call", k))["gap_ms"] for k in ("c1", "c2", "c3", "a1", "a2")}
    assert gap == {"c1": None, "c2": 3000, "c3": 2500, "a1": None, "a2": 100}
    assert row(conn, b.id("turn", "t1"))["gap_ms"] is None  # llm_call only
    assert row(conn, b.id("tool_call", "k1"))["gap_ms"] is None


def test_gap_uses_the_previous_start_when_it_has_no_end(tmp_path):
    conn = store(tmp_path)
    b = B("noend", start=0, end=5000)
    t = b.turn("t", start=0, end=5000, turn_kind="human")
    b.llm("p", t, 1000, None)
    b.llm("q", t, 1800, 1900)
    put(conn, b)
    assert row(conn, b.id("llm_call", "q"))["gap_ms"] == 800


def test_calls_without_a_start_come_last_and_have_no_gap(tmp_path):
    conn = store(tmp_path)
    b = B("nostart", start=0, end=5000)
    t = b.turn("t", start=0, end=5000, turn_kind="human")
    b.llm("late", t, None, None)  # added first: lowest rowid, still ordered after the timed call
    b.llm("timed", t, 1000, 1500)
    put(conn, b)
    assert row(conn, b.id("llm_call", "timed"))["thread_pos"] == 1
    late = row(conn, b.id("llm_call", "late"))
    assert (late["thread_pos"], late["gap_ms"]) == (2, None)
    assert late["caused_by"] == b.id("llm_call", "timed")


# --- cause edge -------------------------------------------------------------------------------------


def test_cause_target_is_the_turn_for_the_first_call_of_a_turn(tmp_path):
    conn = store(tmp_path)
    b = thread_session()
    put(conn, b)
    for key, turn in (("c1", "t1"), ("c3", "t2")):  # c3: t2 started after c2
        r = facts(conn, b.id("llm_call", key))
        assert (r["caused_by"], r["caused_by_basis"]) == (b.id("turn", turn), "order")


def test_cause_target_is_the_previous_call_inside_a_turn(tmp_path):
    conn = store(tmp_path)
    b = thread_session()
    put(conn, b)
    assert facts(conn, b.id("llm_call", "c2"))["caused_by"] == b.id("llm_call", "c1")
    assert facts(conn, b.id("llm_call", "a2"))["caused_by"] == b.id("llm_call", "a1")
    assert facts(conn, b.id("llm_call", "a2"))["caused_by_basis"] == "order"


def test_cause_target_is_the_engagement_for_the_first_call_of_an_agent_run(tmp_path):
    conn = store(tmp_path)
    b = thread_session()
    put(conn, b)
    r = facts(conn, b.id("llm_call", "a1"))
    assert (r["caused_by"], r["caused_by_basis"]) == (b.id("engagement", "e1"), "order")


def test_cause_target_is_the_session_when_the_call_has_no_turn(tmp_path):
    conn = store(tmp_path)
    b = B("noturn", start=0, end=5000)
    b.llm("c1", b.session, 1000, 1500)
    b.llm("c2", b.session, 2000, 2500)
    put(conn, b)
    assert facts(conn, b.id("llm_call", "c1"))["caused_by"] == b.session
    assert facts(conn, b.id("llm_call", "c2"))["caused_by"] == b.id("llm_call", "c1")


@pytest.mark.parametrize("provider", ["claude_code", "codex", "omp"])
def test_every_llm_call_has_one_edge_in_every_provider(tmp_path, provider):
    conn = store(tmp_path)
    b = thread_session(provider=provider)
    put(conn, b)
    rows = conn.execute(
        "SELECT op_id, caused_by_rowid, caused_by_basis FROM ops WHERE kind='llm_call' AND root_rowid=?",
        (row(conn, b.session)["rowid"],),
    ).fetchall()
    assert len(rows) == 5
    assert all(r["caused_by_rowid"] is not None and r["caused_by_basis"] == "order" for r in rows)


def test_engagement_edges_and_attribution_are_unchanged(tmp_path):
    conn = store(tmp_path)
    b = thread_session()
    put(conn, b)
    e1 = facts(conn, b.id("engagement", "e1"))
    assert (e1["caused_by"], e1["caused_by_basis"]) == (b.id("tool_call", "k1"), "id")
    # a1/a2 are attributed to the main turn t1 through the engagement's tool_call, not through their own edge
    turn = row(conn, b.id("turn", "t1"))
    assert row(conn, b.id("llm_call", "a1"))["attr_turn_rowid"] == turn["rowid"]
    assert turn["attr_n_llm_calls"] == 4  # c1, a1, a2, c2


def test_tool_call_and_container_ops_get_no_edge_of_their_own(tmp_path):
    conn = store(tmp_path)
    b = thread_session()
    put(conn, b)
    for kind, key in (("tool_call", "k1"), ("turn", "t1"), ("turn", "ta")):
        assert facts(conn, b.id(kind, key))["caused_by_rowid"] is None


# --- edge invariant ---------------------------------------------------------------------------------


def edge_rows():
    """Three ops in pre-order: session s (thread s), turn t, call c with an edge to t."""
    s = dict(op_id="s", kind="session", rowid=1, pre=0, post=2, start_ms=0, thread_rowid=1, caused_by=None)
    t = dict(op_id="t", kind="turn", rowid=2, pre=1, post=2, start_ms=0, thread_rowid=1, caused_by=None)
    c = dict(op_id="c", kind="llm_call", rowid=3, pre=2, post=2, start_ms=5, thread_rowid=1, caused_by="t")
    return [s, t, c]


def test_edge_check_accepts_a_well_formed_edge():
    derive.check_call_edges(edge_rows())


def test_edge_check_rejects_a_call_without_an_edge():
    rows = edge_rows()
    rows[2]["caused_by"] = None
    with pytest.raises(derive.EdgeError, match="no cause"):
        derive.check_call_edges(rows)


def test_edge_check_rejects_a_target_that_does_not_come_before_the_call():
    rows = edge_rows()
    later = dict(rows[2], op_id="d", rowid=4, pre=3, post=3, start_ms=9, caused_by="c")
    rows[2]["caused_by"] = "d"  # c caused by the call d that follows it in the thread
    with pytest.raises(derive.EdgeError, match="before"):
        derive.check_call_edges([*rows, later])


def test_edge_check_rejects_a_target_in_another_thread():
    rows = edge_rows()
    other = dict(op_id="o", kind="llm_call", rowid=9, pre=3, post=3, start_ms=1, thread_rowid=7)
    other["caused_by"] = "t"
    rows[2]["caused_by"] = "o"
    with pytest.raises(derive.EdgeError, match="thread"):
        derive.check_call_edges([*rows, other])


def test_a_defect_in_the_edges_rejects_the_bundle_and_keeps_the_old_rows(tmp_path, monkeypatch):
    conn = store(tmp_path)
    b = thread_session()
    put(conn, b)
    before = conn.execute("SELECT rowid, caused_by_rowid FROM ops ORDER BY rowid").fetchall()

    def broken(_rows):
        raise derive.EdgeError("forced")

    monkeypatch.setattr(derive, "check_call_edges", broken)
    with pytest.raises(load.BundleInvalid, match="forced"):
        put(conn, thread_session())
    assert conn.execute("SELECT rowid, caused_by_rowid FROM ops ORDER BY rowid").fetchall() == before


# --- duplicates -------------------------------------------------------------------------------------


def session_of(sid, start, calls):
    """A session with one turn and `calls`, a list of (name, occ_key); 1 s apart, starting 1 s in."""
    b = B(sid, start=start, end=start + 20_000)
    t = b.turn("t", start=start, end=start + 20_000, turn_kind="human")
    for i, (name, occ) in enumerate(calls):
        b.llm(f"{name}@{sid}", t, start + 1000 * (i + 1), start + 1000 * (i + 1) + 500, occ=occ)
    return b


def owner_and_resumed():
    """`a` owns x1, x2; `b` is a later session holding copies of both, then its own call y."""
    owner = session_of("a", 0, [("x1", "x1"), ("x2", "x2")])
    copy = session_of("b", 100_000, [("x1", "x1"), ("x2", "x2"), ("y", "y")])
    return owner, copy


@pytest.mark.parametrize("owner_first", [True, False])
def test_duplicate_carries_its_owners_values(tmp_path, owner_first):
    conn = store(tmp_path)
    owner, copy = owner_and_resumed()
    put(conn, *((owner, copy) if owner_first else (copy, owner)))
    for k in ("x1", "x2"):
        mine, theirs = row(conn, copy.id("llm_call", f"{k}@b")), row(conn, owner.id("llm_call", f"{k}@a"))
        assert mine["duplicate_of"] == owner.id("llm_call", f"{k}@a")
        for col in COLUMNS:
            assert mine[col] == theirs[col], (k, col)
    assert row(conn, copy.id("llm_call", "x2@b"))["thread_pos"] == 2


def test_new_calls_after_copied_ones_continue_the_threads_numbering(tmp_path):
    conn = store(tmp_path)
    put(conn, *owner_and_resumed())
    y = row(conn, "claude_code/b/llm_call/y@b")
    assert y["thread_pos"] == 3
    assert y["duplicate_of"] is None
    assert y["caused_by"] == "claude_code/b/llm_call/x2@b"


# --- incremental ingest and refold ------------------------------------------------------------------


def snapshot(conn, session_rowid):
    return {
        r["op_id"]: tuple(r[c] for c in COLUMNS)
        for r in conn.execute(
            f"SELECT op_id, {','.join(COLUMNS)} FROM ops WHERE root_rowid=?",
            (session_rowid,),
        )
    }


def growing(n_calls):
    b = B("grow", start=0, end=100_000)
    t = b.turn("t", start=0, end=100_000, turn_kind="human")
    for i in range(n_calls):
        b.llm(f"c{i}", t, 1000 * (i + 1), 1000 * (i + 1) + 400)
    return b


def test_appending_calls_leaves_the_earlier_calls_values_unchanged(tmp_path):
    conn = store(tmp_path)
    put(conn, growing(3))
    root = row(conn, growing(3).session)["rowid"]
    before = snapshot(conn, root)
    put(conn, growing(5))
    after = snapshot(conn, root)
    old_ids = [o for o in before if "/llm_call/" in o]
    assert len(old_ids) == 3
    assert all(after[o] == before[o] for o in old_ids)
    assert after[growing(5).id("llm_call", "c4")][0] == root
    assert row(conn, growing(5).id("llm_call", "c4"))["thread_pos"] == 5


def test_refold_gives_the_values_the_load_wrote(tmp_path):
    conn = store(tmp_path)
    b = thread_session()
    put(conn, b)
    root = row(conn, b.session)["rowid"]
    before = snapshot(conn, root)
    with load.write_txn(conn):
        load.refold(conn, f"{b.provider}/{b.sid}")
    assert snapshot(conn, root) == before


def test_stream_rejects_a_cause_the_adapter_set_on_an_llm_call():
    b = thread_session()
    call = b.ops[[o.op_id for o in b.ops].index(b.id("llm_call", "c2"))]
    call.caused_by = b.id("llm_call", "c1")
    assert [(d.kind, d.ref) for d in stream.defects(b.bundle())] == [("kind_field", call.op_id)]


def test_stream_accepts_the_order_basis():
    b = thread_session()
    b.ops[[o.op_id for o in b.ops].index(b.id("llm_call", "c2"))].caused_by_basis = "order"
    assert stream.defects(b.bundle()) == []
