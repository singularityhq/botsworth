"""F1-F11 (approaches.md fixture table) at the store.

F1-F7 and F9 are canonical streams emitted by adapter-claude in milestone 2
(copied from spikes/sqlite-rollup/fixtures, legacy names included, so the
reader's normalizer is on the path). F8, F10, F11 are hand-built bundles: the
adapter half of those fixtures belongs to T1/T2.
"""

import json
from pathlib import Path

import pytest
from store_builders import B, owners_ok, put, row, store
from stream_load import load_streams

FX = Path(__file__).parent / "store_streams"


def load(tmp_path, *dirs):
    db = tmp_path / "s.db"
    for d in dirs:
        result = load_streams(db=db, streams=[FX / d])
        assert result.rejected == [], result.rejected
    conn = store(tmp_path)
    owners_ok(conn)
    return conn


def op(conn, suffix):
    r = conn.execute("SELECT * FROM ops WHERE op_id LIKE ?", ("%/" + suffix,)).fetchone()
    assert r is not None, suffix
    return r


def test_F1_one_call_exact_tokens_cost_context(tmp_path):
    conn = load(tmp_path, "F1")
    (c,) = conn.execute("SELECT * FROM ops WHERE kind='llm_call'").fetchall()
    assert (
        c["in_tok"],
        c["cache_write_tok"],
        c["cache_write_5m_tok"],
        c["cache_read_tok"],
        c["out_tok"],
    ) == (
        10,
        1000,
        1000,
        5000,
        200,
    )
    assert c["cost_usd"] == pytest.approx(0.00828, abs=1e-12)
    assert c["tok_total"] == 6210
    s = op(conn, "session/-")
    assert (s["incl_cost_usd"], s["incl_tok_total"], s["n_llm_calls"]) == (c["cost_usd"], 6210, 1)


def test_F2_final_usage_not_sum(tmp_path):
    conn = load(tmp_path, "F2")
    outs = [
        r[0] for r in conn.execute("SELECT out_tok FROM ops WHERE kind='llm_call' AND agent_id IS NOT NULL")
    ]
    assert outs == [480]


def test_F3_parallel_time_rollups(tmp_path):
    conn = load(tmp_path, "F3")
    t = op(conn, "turn/main#0")
    got = (t["wall_ms"], t["busy_sum_ms"], t["active_ms"], t["blocked_ms"], t["idle_ms"], t["max_parallel"])
    assert got == (50000, 73000, 48000, 40000, 2000, 2)
    a = op(conn, "tool_call/ta")
    assert (a["dur_ms"], a["wall_ms"]) == (0, 40000)
    s = op(conn, "session/-")
    total = conn.execute("SELECT sum(tok_total) FROM ops WHERE kind='llm_call'").fetchone()[0]
    assert s["incl_tok_total"] == total and s["n_llm_calls"] == 4 and s["n_engagements"] == 2


def test_F4_cache_tiers(tmp_path):
    conn = load(tmp_path, "F4")
    c = conn.execute("SELECT * FROM ops WHERE kind='llm_call'").fetchone()
    assert (c["cache_write_tok"], c["cache_write_1h_tok"], c["cache_write_5m_tok"]) == (3000, 2000, 1000)
    assert c["cost_usd"] == pytest.approx(0.02028)
    assert op(conn, "session/-")["incl_cache_write_1h_tok"] == 2000


@pytest.mark.parametrize("order", [("F5-s2", "F5-s1"), ("F5-s1", "F5-s2")])
def test_F5_owner_by_session_start_not_load_order(tmp_path, order):
    conn = load(tmp_path, *order)
    for key in ("r1", "r2"):
        owner, copy = op(conn, "s1/llm_call/" + key), op(conn, "s2/llm_call/" + key)
        assert owner["duplicate_of"] is None and copy["duplicate_of"] == owner["op_id"]
    assert op(conn, "s1/session/-")["incl_cost_usd"] == pytest.approx(2 * 0.00828)
    s2 = op(conn, "s2/session/-")
    assert (s2["incl_cost_usd"], s2["n_duplicates"], s2["n_llm_calls"]) == (None, 2, 0)


def test_F6_unknown_price_null_and_lower_bound(tmp_path):
    conn = load(tmp_path, "F6")
    c = conn.execute("SELECT * FROM ops WHERE kind='llm_call'").fetchone()
    assert c["cost_usd"] is None and c["cost_basis"] == "missing" and c["tok_total"] > 0
    ancestors = conn.execute(
        "SELECT o.* FROM ops o WHERE o.root_rowid=? AND o.pre < ? AND o.post >= ?",
        (c["root_rowid"], c["pre"], c["post"]),
    ).fetchall()
    assert ancestors
    for anc in ancestors:
        assert anc["cost_lower_bound"] == 1 and anc["incl_cost_usd"] is None and anc["incl_tok_total"] > 0


def test_F7_unlinked_engagement_under_session(tmp_path):
    conn = load(tmp_path, "F7")
    e = conn.execute("SELECT * FROM ops WHERE kind='engagement'").fetchone()
    assert e["parent_rowid"] == op(conn, "session/-")["rowid"]
    assert e["caused_by_basis"] == "none"


def test_F9_nested_fork_once_owner_is_parent(tmp_path):
    conn = load(tmp_path, "F9")
    rp, copy = op(conn, "llm_call/rp"), op(conn, "llm_call/rp@copy:afork")
    assert rp["agent_id"] == "zparent" and copy["duplicate_of"] == rp["op_id"]
    assert op(conn, "llm_call/rf")["duplicate_of"] is None
    assert (
        json.loads(conn.execute("SELECT value FROM meta WHERE key='last_run'").fetchone()[0])["rejected"]
        == []
    )
    assert conn.execute("SELECT value FROM meta WHERE key='n_ownership_disagreements'").fetchone()[0] == "0"


def test_F8_codex_segments_three_calls(tmp_path):
    # token_count totals 100,100,250,250,250,400 -> calls of 100/150/150 (adapter splits; store sums once)
    b = B("c1", provider="codex")
    t = b.turn("main#0")
    for i, (inp, out) in enumerate([(60, 40), (100, 50), (100, 50)]):
        b.llm(f"seg@{i}", t, i * 10, i * 10 + 5, tokens=(inp, out, 0, None), cost=None, occ=f"th:0:{i}")
    conn = store(tmp_path)

    put(conn, b)
    assert [r[0] for r in conn.execute("SELECT tok_total FROM ops WHERE kind='llm_call' ORDER BY op_id")] == [
        100,
        150,
        150,
    ]
    s = row(conn, b.session)
    assert (s["incl_tok_total"], s["incl_cost_usd"], s["cost_lower_bound"]) == (400, None, 1)


def test_F10_steps_and_skill_event(tmp_path):
    b = B("s10")
    t = b.turn("main#0")
    c1 = b.llm("r1", t, 0, 10)
    read = b.tool("t_read", c1, 10, 11, tool_name="Read")
    run = b.tool(
        "t_run", c1, 11, 20, tool_name="Bash", exit_code=1, is_error=True, command_key="uv run pytest"
    )
    skill = b.tool("t_skill", c1, 20, 21, tool_name="Skill")
    spawn = b.tool("t_agent", c1, 21, 22, tool_name="Agent")
    for verb, ref, kw in [
        ("think", c1, {}),
        ("say", c1, {}),
        ("read", read, {"target": "a.py", "outcome": "ok"}),
        ("run", run, {"target": "uv run pytest", "outcome": "exit:1"}),
        ("skill", skill, {"target": "grill-me"}),
        ("spawn", spawn, {"target": "coder"}),
        ("say", c1, {}),
    ]:
        b.step(t, ref, verb, **kw)
    b.event("skill_invocation", op_id=skill, ts=20, skill="grill-me", via="tool")
    conn = store(tmp_path)

    put(conn, b)
    steps = conn.execute(
        "SELECT verb, target, outcome, op_id FROM steps_v WHERE turn_id=? ORDER BY ordinal", (t,)
    )
    steps = steps.fetchall()
    assert [s["verb"] for s in steps] == ["think", "say", "read", "run", "skill", "spawn", "say"]
    run_step = steps[3]
    assert (run_step["target"], run_step["outcome"]) == ("uv run pytest", "exit:1")
    assert row(conn, run_step["op_id"])["kind"] == "tool_call"  # the step's op ref resolves
    (ev,) = conn.execute("SELECT * FROM events WHERE kind='skill_invocation'").fetchall()
    assert json.loads(ev["fields"])["skill"] == "grill-me"
    assert (ev["provider"], ev["project"]) == ("claude_code", "/repo")
    assert row(conn, t)["n_steps"] == 7 and row(conn, b.session)["n_errors"] == 1


def test_F11_compaction_event(tmp_path):
    b = B("s11")
    t = b.turn("main#0")
    b.llm("r1", t, 0, 10, context_tokens=150_000)
    b.event("compaction", op_id=t, ts=11, trigger="auto", pre_tokens=150_000, post_tokens=30_000)
    after = b.llm("r2", t, 12, 20, context_tokens=31_000)
    conn = store(tmp_path)

    put(conn, b)
    (ev,) = conn.execute("SELECT * FROM events WHERE kind='compaction'").fetchall()
    fields = json.loads(ev["fields"])
    assert (fields["trigger"], fields["pre_tokens"], fields["post_tokens"]) == ("auto", 150_000, 30_000)
    assert ev["op_id"] == t
    assert row(conn, after)["context_tokens"] < fields["pre_tokens"]
