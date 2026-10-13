"""B3a store fold: DM-10, DM-12, DM-24, DM-25, DM-30, DM-41, DM-42, DM-44, DM-45, DM-47, DM-54, DM-62."""

import inspect
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
import test_adapter_codex as codex_base
import test_adapter_codex_v7 as codex_v7
from store_builders import T0, B, put, row, store
from stream_load import load_streams

from botsworth import pipeline, stream
from botsworth.query import zone
from botsworth.store import ingest, load
from botsworth.store.load import BundleInvalid


def at(iso: str) -> int:
    """Offset in ms from the builders' T0 of a UTC instant."""
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1000) - T0


def session(conn, sid):
    return conn.execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()


def titled(sid, asks, provider="claude_code", **kw):
    """A session with one main turn per `(kind, label)` in `asks`, 10 ms apart."""
    b = B(sid, provider=provider, **kw)
    for i, (turn_kind, label) in enumerate(asks):
        b.turn(f"main#{i}", start=i * 10, end=i * 10 + 9, turn_kind=turn_kind, label=label)
    return b


def ingest_all(tmp_path, *builders, db="s.db"):
    streams = tmp_path / "streams"
    for b in builders:
        stream.write_bundle(b.bundle(), streams)
    return load_streams(streams=[streams], db=tmp_path / db)


# --- DM-10 ----------------------------------------------------------------------------------------


def test_title_rename_beats_generated(tmp_path):
    conn = store(tmp_path)
    put(
        conn,
        titled("r", [("human", "fix the rounding in cost.py")], title="My name", title_source="rename"),
        titled("g", [("human", "fix the rounding in cost.py")], title="Gen name", title_source="generated"),
    )
    r, g = session(conn, "r"), session(conn, "g")
    assert (r["display_title"], r["title_source"], r["title"]) == ("My name", "rename", "My name")
    assert (g["display_title"], g["title_source"]) == ("Gen name", "generated")


def test_title_first_ask_when_no_title(tmp_path):
    conn = store(tmp_path)
    put(
        conn,
        titled(
            "a", [("human", "ok"), ("human", "fix the rounding in cost.py"), ("human", "more please now ok")]
        ),
    )
    s = session(conn, "a")
    assert (s["display_title"], s["title_source"]) == ("fix the rounding in cost.py", "first_ask")


def test_title_fallback_for_clear_and_ok(tmp_path):
    conn = store(tmp_path)
    put(conn, titled("a", [("harness", "/clear"), ("human", "ok")]))
    s = session(conn, "a")
    assert (s["display_title"], s["title_source"]) == (None, "fallback")


def test_codex_thread_title_only_without_qualifying_ask(tmp_path):
    kw = {"provider": "codex", "title": "Investigate the flaky build", "title_source": "thread_title"}
    conn = store(tmp_path)
    put(
        conn,
        titled("with", [("human", "fix the rounding in cost.py")], **kw),
        titled("without", [("human", "ok")], **kw),
        titled("none", [("human", "ok")], provider="codex"),
    )
    with_ask, without, none = (session(conn, s) for s in ("with", "without", "none"))
    assert (with_ask["display_title"], with_ask["title_source"]) == (
        "fix the rounding in cost.py",
        "first_ask",
    )
    assert (without["display_title"], without["title_source"]) == ("Investigate the flaky build", "first_ask")
    assert (none["display_title"], none["title_source"]) == (None, "fallback")


# --- DM-12 ----------------------------------------------------------------------------------------


def test_override_changes_title_without_rebuild(tmp_path):
    root = tmp_path / "codex"
    codex_base.write(root, codex_base.SID, codex_v7.turn_lines("fix the rounding in cost.py please"))
    db = tmp_path / "s.db"
    first = pipeline.run(["codex"], {"codex": root}, db=db)
    assert first["providers"]["codex"]["rebuilt"] == 1

    def shown():
        conn = sqlite3.connect(db)
        try:
            return conn.execute("SELECT display_title, title_source FROM sessions").fetchone()
        finally:
            conn.close()

    assert shown() == ("fix the rounding in cost.py please", "first_ask")
    codex_v7.index(root, {"id": codex_base.SID, "thread_name": "Review app"})
    second = pipeline.run(["codex"], {"codex": root}, db=db)
    assert second["providers"]["codex"]["rebuilt"] == 0
    assert second["store"]["overrides_applied"] == 1
    assert shown() == ("Review app", "rename")
    third = pipeline.run(["codex"], {"codex": root}, db=db)
    assert third["store"]["overrides_applied"] == 0
    codex_v7.index(
        root,
        {"id": codex_base.SID, "thread_name": "Review app"},
        {"id": codex_base.SID, "thread_name": "Later"},
    )
    assert pipeline.run(["codex"], {"codex": root}, db=db)["store"]["overrides_applied"] == 1
    assert shown() == ("Later", "rename")


# --- DM-24 ----------------------------------------------------------------------------------------


def test_display_ix_skips_harness(tmp_path):
    conn = store(tmp_path)
    b = titled(
        "a", [("human", "first ask here now"), ("harness", "/clear"), ("human", "second ask here now")]
    )
    put(conn, b)
    got = [r["display_ix"] for r in conn.execute("SELECT * FROM ops WHERE kind='turn' ORDER BY start_ms")]
    assert got == [1, None, 2]


def test_display_ix_ties_by_seq(tmp_path):
    b = B("a")
    late = b.turn("late", start=5, end=9, turn_kind="human", label="x y z w", seq=2)
    early = b.turn("early", start=5, end=9, turn_kind="message", label="x y z w", seq=1)
    conn = store(tmp_path)
    put(conn, b)
    assert (row(conn, early)["display_ix"], row(conn, late)["display_ix"]) == (1, 2)


def test_display_ix_agent_turn_not_numbered(tmp_path):
    b = B("a")
    t = b.turn("main#0", start=0, end=50, turn_kind="human", label="a b c d")
    e = b.engagement("e", t, "ag", start=1, end=40)
    at_ = b.turn("agent#0", parent=e, start=2, end=30, agent="ag", turn_kind="human", label="a b c d")
    conn = store(tmp_path)
    put(conn, b)
    assert row(conn, at_)["display_ix"] is None and row(conn, t)["display_ix"] == 1


# --- DM-25 ----------------------------------------------------------------------------------------


def test_sdk_only_session_automated(tmp_path):
    b = B("a")
    b.turn("main#0", start=0, end=9, turn_kind="message", turn_origin="sdk", label="summarize the repo now")
    conn = store(tmp_path)
    put(conn, b)
    s = session(conn, "a")
    assert (s["n_human_asks"], s["is_automated"]) == (0, 1)


def test_short_human_asks_not_automated(tmp_path):
    conn = store(tmp_path)
    put(conn, titled("a", [("human", "ok"), ("human", "yes")]))
    s = session(conn, "a")
    assert (s["n_human_asks"], s["is_automated"]) == (2, 0)


def test_subagents_only_automated(tmp_path):
    b = B("a")
    e = b.engagement("e", b.session, "ag", start=0, end=50)
    b.llm("l", e, 1, 20, agent="ag")
    conn = store(tmp_path)
    put(conn, b)
    s = session(conn, "a")
    assert (s["n_human_asks"], s["is_automated"], s["n_llm_calls"]) == (0, 1, 1)


# --- DM-30 ----------------------------------------------------------------------------------------


def skill_session(sid="a"):
    b = B(sid)
    t = b.turn("main#0", start=0, end=50, turn_kind="human", label="load the skill please now")
    c = b.llm("l", t, 1, 5)
    k = b.tool("k", c, 5, 9, tool_name="Skill")
    b.step(t, c, "think")
    b.step(t, k, "skill")
    return b, t, k


def test_skill_load_from_tool_event(tmp_path):
    b, t, k = skill_session()
    b.event("skill_invocation", k, 6, skill="team", via="tool", step_id=f"{t}#1")
    conn = store(tmp_path)
    put(conn, b)
    loads = [
        (r["ordinal"], r["skill_load"], r["verb"])
        for r in conn.execute("SELECT * FROM steps ORDER BY ordinal")
    ]
    assert loads == [(0, None, "think"), (1, "team", "skill")]


def test_skill_load_codex_sed_step(tmp_path):
    b = B("a", provider="codex")
    t = b.turn("main#0", start=0, end=50, turn_kind="human", label="load the skill please now")
    c = b.llm("l", t, 1, 5)
    k = b.tool("k", c, 5, 9, tool_name="exec_command")
    b.step(t, k, "run")
    b.event("skill_invocation", k, 6, skill="x", via="read", step_id=f"{t}#0")
    conn = store(tmp_path)
    put(conn, b)
    (s,) = conn.execute("SELECT * FROM steps")
    assert (s["skill_load"], s["verb"]) == ("x", "run")


def test_slash_without_step_no_skill_load(tmp_path):
    b, t, _ = skill_session()
    b.event("skill_invocation", t, 0, skill="team", via="slash", step_id=None)
    conn = store(tmp_path)
    put(conn, b)
    assert conn.execute("SELECT count(*) FROM steps WHERE skill_load IS NOT NULL").fetchone()[0] == 0


def test_skill_event_naming_unknown_step_rejects_bundle(tmp_path):
    b, t, k = skill_session()
    b.event("skill_invocation", k, 6, skill="team", via="tool", step_id=f"{t}#9")
    with pytest.raises(BundleInvalid, match="step_id"):
        put(store(tmp_path), b)


# --- DM-41, DM-42, DM-54 ----------------------------------------------------------------------------


def day_sums(db, day, tz, *, llm_only=False):
    """(n_llm_calls, cost) of the buckets inside the local `day` of `tz`: the DM-41/DM-42 reader rule."""
    start, end = zone.day_bounds(day, tz)
    conn = sqlite3.connect(db)
    try:
        return tuple(
            conn.execute(
                "SELECT coalesce(sum(n_llm_calls), 0), coalesce(sum(cost_usd), 0) FROM rollup_time"
                " WHERE bucket_start_ms >= ? AND bucket_start_ms < ?"
                + (" AND n_llm_calls > 0" if llm_only else ""),
                (start, end),
            ).fetchone()
        )
    finally:
        conn.close()


def two_day_session():
    b = B("a", start=at("2026-10-06T10:00:00"), end=at("2026-10-07T10:00:00"))
    t = b.turn("main#0", start=at("2026-10-06T10:00:00"), end=at("2026-10-07T10:00:00"))
    b.llm("l1", t, at("2026-10-06T10:00:00"), at("2026-10-06T10:00:01"), cost=3.0)  # IST Oct 6
    b.llm("l2", t, at("2026-10-06T19:00:00"), at("2026-10-06T19:00:01"), cost=5.0)  # IST Oct 7
    return b


def test_rollup_one_row_per_root_15min_bucket_and_dims(tmp_path):
    """DM-41 test (g)."""
    b = B("a", start=at("2026-10-06T19:00:00"), end=at("2026-10-06T20:00:00"))
    t = b.turn("main#0", start=at("2026-10-06T19:00:00"), end=at("2026-10-06T20:00:00"))
    b.llm("l1", t, at("2026-10-06T19:00:00"), at("2026-10-06T19:00:01"), cost=1.0)
    b.llm("l2", t, at("2026-10-06T19:14:00"), at("2026-10-06T19:14:01"), cost=2.0)  # same bucket as l1
    b.llm("l3", t, at("2026-10-06T19:15:00"), at("2026-10-06T19:15:01"), cost=4.0)  # 15 minutes later
    ingest_all(tmp_path, b)
    conn = sqlite3.connect(tmp_path / "s.db")
    rows = conn.execute(
        "SELECT bucket_start_ms, n_llm_calls, cost_usd FROM rollup_time ORDER BY 1"
    ).fetchall()
    assert rows == [(T0 + at("2026-10-06T19:00:00"), 2, 3.0), (T0 + at("2026-10-06T19:15:00"), 1, 4.0)]
    assert all(r[0] % 900_000 == 0 for r in rows)
    # no zone anywhere: no meta.tz, no TEXT date column, no tz argument
    assert conn.execute("SELECT count(*) FROM meta WHERE key='tz'").fetchone()[0] == 0
    text_cols = {r[1] for r in conn.execute("PRAGMA table_info(rollup_time)") if r[2] == "TEXT"}
    assert text_cols == {"provider", "account", "model", "project", "agent_type"}
    assert "tz" not in inspect.signature(ingest.ingest).parameters
    assert "refold_zone" not in dir(ingest) and "store_tz" not in dir(load)


def test_session_two_local_days_3_and_5(tmp_path):
    ingest_all(tmp_path, two_day_session())
    db = tmp_path / "s.db"
    assert day_sums(db, "2026-10-06", "Asia/Kolkata") == (1, 3.0)
    assert day_sums(db, "2026-10-07", "Asia/Kolkata") == (1, 5.0)
    assert day_sums(db, "2026-10-06", "UTC") == (2, 8.0)  # same store, another request zone
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT incl_cost_usd FROM sessions").fetchone()[0] == 8.0


def test_tool_only_day_has_no_llm_membership(tmp_path):
    b = B("a", start=at("2026-10-06T10:00:00"), end=at("2026-10-07T10:00:00"))
    t = b.turn("main#0", start=at("2026-10-06T10:00:00"), end=at("2026-10-07T10:00:00"))
    c = b.llm("l1", t, at("2026-10-06T10:00:00"), at("2026-10-06T10:00:01"))
    b.tool("k", c, at("2026-10-07T05:00:00"), at("2026-10-07T05:00:01"))
    ingest_all(tmp_path, b)
    db = tmp_path / "s.db"
    assert day_sums(db, "2026-10-06", "UTC", llm_only=True)[0] == 1
    assert day_sums(db, "2026-10-07", "UTC", llm_only=True)[0] == 0
    conn = sqlite3.connect(db)
    start, end = zone.day_bounds("2026-10-07", "UTC")
    tool_rows = conn.execute(
        "SELECT count(*) FROM rollup_time WHERE bucket_start_ms >= ? AND bucket_start_ms < ?", (start, end)
    ).fetchone()[0]
    assert tool_rows > 0


def test_request_zone_change_leaves_the_store_unchanged(tmp_path, monkeypatch):
    root = tmp_path / "claude"
    shutil.copytree(Path(__file__).parents[1] / "fixtures" / "F2" / "project", root / "project")
    monkeypatch.setattr(pipeline, "LIVE_WINDOW_MS", -3_600_000)
    db = tmp_path / "s.db"

    def snapshot():
        conn = sqlite3.connect(db)
        try:
            return (
                conn.execute("SELECT * FROM rollup_time ORDER BY 1, 2, 3, 4, 5, 6, 7").fetchall(),
                conn.execute("SELECT key FROM meta ORDER BY 1").fetchall(),
            )
        finally:
            conn.close()

    monkeypatch.setenv("BOTSWORTH_TZ", "UTC")
    first = pipeline.run(["claude_code"], {"claude_code": root}, db=db)
    assert first["providers"]["claude_code"]["rebuilt"] == 1
    before = snapshot()
    monkeypatch.setenv("BOTSWORTH_TZ", "Pacific/Kiritimati")  # UTC+14: it would have moved every day bucket
    second = pipeline.run(["claude_code"], {"claude_code": root}, db=db)
    assert second["providers"]["claude_code"]["rebuilt"] == 0
    assert snapshot() == before
    assert ("tz",) not in before[1]


# --- DM-44 ----------------------------------------------------------------------------------------


def test_activity_sum_equals_session_tokens(tmp_path):
    b = B("a", start=0, end=7_200_000)
    t = b.turn("main#0", start=0, end=7_200_000)
    b.llm("l1", t, 0, 10, tokens=(10, 20, 5, 0))
    b.llm("l2", t, 3_600_000, 3_600_010, tokens=(1, 2, 0, 0))
    e = b.engagement("e", t, "ag", start=7_000_000, end=7_200_000)
    b.llm("l3", e, 7_199_000, 7_200_000, tokens=(7, 7, 0, 0), agent="ag")
    conn = store(tmp_path)
    put(conn, b)
    s = session(conn, "a")
    bins = json.loads(s["activity_tok"])
    assert s["activity_bin_ms"] == 120_000 and len(bins) == 60
    assert sum(bins) == s["incl_tok_total"] == 35 + 3 + 14
    assert (bins[0], bins[30], bins[59]) == (35, 3, 14)


def test_activity_empty_without_calls(tmp_path):
    b = B("a")
    b.turn("main#0")
    conn = store(tmp_path)
    put(conn, b)
    assert json.loads(session(conn, "a")["activity_tok"]) == []


# --- DM-45 ----------------------------------------------------------------------------------------


def spawn_session():
    """T1 spawns agent A (A#0, $2); in T2 a SendMessage causes A#1 ($1), parented under T1's spawn call."""
    b = B("a", end=1000)
    t1 = b.turn("t1", start=0, end=400, turn_kind="human", label="spawn the agent now please")
    c1 = b.llm("c1", t1, 1, 5, cost=0.5)
    spawn = b.tool("spawn", c1, 5, 9, tool_name="Agent")
    t2 = b.turn("t2", start=500, end=900, turn_kind="human", label="send a message to it now")
    c2 = b.llm("c2", t2, 501, 505, cost=0.25)
    send = b.tool("send", c2, 505, 509, tool_name="SendMessage")
    e0 = b.engagement("a0", spawn, "ag", start=10, end=200, caused_by=spawn, caused_by_basis="id")
    b.llm("a0l", e0, 11, 20, cost=2.0, agent="ag")
    e1 = b.engagement("a1", spawn, "ag", start=510, end=800, caused_by=send, caused_by_basis="content_hash")
    b.llm("a1l", e1, 511, 520, cost=1.0, agent="ag")
    return b, t1, t2


def test_attr_spawn_and_cycle_reply_split_t1_2_t2_1(tmp_path):
    b, t1, t2 = spawn_session()
    conn = store(tmp_path)
    put(conn, b)
    r1, r2 = row(conn, t1), row(conn, t2)
    assert (r1["attr_cost_usd"], r1["attr_n_llm_calls"]) == (2.5, 2)  # c1 + A#0
    assert (r2["attr_cost_usd"], r2["attr_n_llm_calls"]) == (1.25, 2)  # c2 + A#1
    assert row(conn, b.id("llm_call", "a1l"))["attr_turn_rowid"] == r2["rowid"]
    assert row(conn, b.id("llm_call", "a0l"))["attr_turn_rowid"] == r1["rowid"]
    assert r1["attr_tok_total"] == 60 and r1["attr_cost_lower_bound"] == 0
    assert row(conn, b.id("engagement", "a0"))["attr_turn_rowid"] is None  # only leaves carry it


def test_attr_none_basis_unattributed(tmp_path):
    b = B("a", end=1000)
    t = b.turn("t", start=0, end=400, turn_kind="human", label="do some work now please")
    b.llm("c", t, 1, 5, cost=1.0)
    e = b.engagement("e", t, "ag", start=10, end=200, caused_by=None, caused_by_basis="none")
    b.llm("al", e, 11, 20, cost=4.0, agent="ag")
    conn = store(tmp_path)
    put(conn, b)
    assert row(conn, b.id("llm_call", "al"))["attr_turn_rowid"] is None
    assert row(conn, t)["attr_cost_usd"] == 1.0
    attr_invariant(conn)


def test_attr_session_level_leaf_unattributed(tmp_path):
    b = B("a", end=1000)
    t = b.turn("t", start=0, end=400, turn_kind="human", label="do some work now please")
    b.llm("c", t, 1, 5, cost=1.0)
    top = b.llm("top", b.session, 600, 605, cost=2.0)
    conn = store(tmp_path)
    put(conn, b)
    assert row(conn, top)["attr_turn_rowid"] is None
    attr_invariant(conn)


def test_attr_revisit_raises_bundle_invalid(tmp_path):
    # engagement ea is caused by a call inside eb, and eb by a call inside ea: the walk revisits ea
    b = B("a", end=1000)
    t = b.turn("t", start=0, end=400)
    ea = b.engagement(
        "ea", t, "a", start=10, end=100, caused_by=b.id("tool_call", "tb"), caused_by_basis="id"
    )
    eb = b.engagement(
        "eb", t, "b", start=20, end=100, caused_by=b.id("tool_call", "ta"), caused_by_basis="id"
    )
    ca = b.llm("ca", ea, 11, 15, agent="a")
    b.tool("ta", ca, 15, 19, agent="a")
    cb = b.llm("cb", eb, 21, 25, agent="b")
    b.tool("tb", cb, 25, 29, agent="b")
    conn = store(tmp_path)
    with pytest.raises(BundleInvalid, match="revisited"):
        put(conn, b)
    assert conn.execute("SELECT count(*) FROM ops").fetchone()[0] == 0


def test_attr_cause_not_a_tool_call_raises(tmp_path):
    b = B("a", end=1000)
    t = b.turn("t", start=0, end=400)
    c = b.llm("c", t, 1, 5)
    e = b.engagement("e", t, "a", start=10, end=100, caused_by=c, caused_by_basis="id")
    b.llm("al", e, 11, 15, agent="a")
    with pytest.raises(BundleInvalid, match="not a tool_call"):
        put(store(tmp_path), b)


def test_attr_null_cost_sets_lower_bound(tmp_path):
    b = B("a", end=1000)
    t = b.turn("t", start=0, end=400, turn_kind="human", label="do some work now please")
    b.llm("c1", t, 1, 5, cost=1.5)
    b.llm("c2", t, 6, 9, cost=None)
    conn = store(tmp_path)
    put(conn, b)
    r = row(conn, t)
    assert (r["attr_cost_usd"], r["attr_cost_lower_bound"], r["attr_n_llm_calls"]) == (1.5, 1, 2)


def attr_invariant(conn):
    """D38 L9: Σ main-turn attr_cost + Σ unattributed owned llm_call cost = sessions.incl_cost_usd."""
    for s in conn.execute("SELECT rowid, incl_cost_usd FROM sessions").fetchall():
        turns = conn.execute(
            "SELECT coalesce(sum(attr_cost_usd), 0) FROM ops"
            " WHERE root_rowid=? AND kind='turn' AND agent_id IS NULL",
            (s["rowid"],),
        ).fetchone()[0]
        loose = conn.execute(
            "SELECT coalesce(sum(cost_usd), 0) FROM ops WHERE root_rowid=? AND kind='llm_call'"
            " AND duplicate_of IS NULL AND attr_turn_rowid IS NULL",
            (s["rowid"],),
        ).fetchone()[0]
        assert turns + loose == pytest.approx(s["incl_cost_usd"] or 0)


def test_attr_invariant_sums_to_session_cost(tmp_path):
    spawned, _, _ = spawn_session()
    spawned.engagement(
        "none", spawned.session, "x", start=900, end=950, caused_by=None, caused_by_basis="none"
    )
    spawned.llm("nl", spawned.id("engagement", "none"), 901, 905, cost=7.0, agent="x")
    spawned.llm("top", spawned.session, 960, 965, cost=11.0)
    conn = store(tmp_path)
    put(conn, spawned)
    attr_invariant(conn)
    s = session(conn, "a")
    assert s["incl_cost_usd"] == pytest.approx(0.5 + 0.25 + 2 + 1 + 7 + 11)


def test_attr_follows_ownership_flip_by_refold(tmp_path):
    # the later bundle loads first and owns the call; the earlier one then takes it and refolds the later
    first, t1, _ = spawn_session()
    later = B("z", start=10_000, end=11_000, first_line_ms=T0 + 10_000)
    t = later.turn("t", start=10_000, end=10_500, turn_kind="human", label="the same calls again here")
    later.llm("c1x", t, 10_001, 10_005, cost=0.5, occ="c1")
    conn = store(tmp_path)
    put(conn, later)
    assert (row(conn, t)["attr_cost_usd"], row(conn, t)["attr_n_llm_calls"]) == (0.5, 1)
    result = put(conn, first)[0]
    assert result.refolded == ["claude_code/z"]
    assert row(conn, "claude_code/z/llm_call/c1x")["attr_turn_rowid"] is None  # now a duplicate
    assert (row(conn, t)["attr_cost_usd"], row(conn, t)["attr_n_llm_calls"]) == (None, 0)
    assert row(conn, t1)["attr_cost_usd"] == 2.5
    attr_invariant(conn)


# --- DM-47 ----------------------------------------------------------------------------------------


def test_rollup_speed_columns_100_over_2000(tmp_path):
    b = B("a", end=100_000)
    t = b.turn("t", start=0, end=100_000)
    b.llm("l1", t, 0, 2000, tokens=(10, 100, 0, 0), ttft_ms=300)
    b.llm("l2", t, 3000, 4000, tokens=(10, 50, 0, 0))
    b.ops[-1].dur_ms = None
    conn = store(tmp_path)
    put(conn, b)
    r = conn.execute(
        "SELECT sum(timed_out_tok), sum(timed_ms), sum(n_ttft), sum(ttft_ms_sum), sum(out_tok),"
        " sum(n_llm_calls) FROM rollup_time"
    ).fetchone()
    assert tuple(r) == (100, 2000, 1, 300, 150, 2)


# --- DM-62 ----------------------------------------------------------------------------------------


def test_session_bounds_cover_agent_tail(tmp_path):
    b = B("a", start=0, end=100)
    t = b.turn("t", start=0, end=100)
    b.llm("c", t, 10, 20)
    e = b.engagement("e", t, "ag", start=30, end=100)
    b.llm("tail", e, 40, 100 + 300_000, agent="ag")
    early = b.engagement("e2", t, "ag2", start=5, end=50)
    b.llm("pre", early, -500, -400, agent="ag2")
    conn = store(tmp_path)
    put(conn, b)
    s = session(conn, "a")
    assert (s["first_ms"], s["last_ms"]) == (T0 - 500, T0 + 300_100)
    root = row(conn, b.session)
    assert (root["start_ms"], root["end_ms"]) == (s["first_ms"], s["last_ms"])
    leaves = conn.execute("SELECT min(start_ms), max(end_ms) FROM ops WHERE kind='llm_call'").fetchone()
    assert (s["first_ms"], s["last_ms"]) == tuple(leaves)


def test_session_bounds_cover_agent_below_a_tool_call(tmp_path):
    # the agent runs under the Agent tool_call (a leaf) and outlives the main transcript
    b = B("a", start=0, end=100)
    t = b.turn("t", start=0, end=100)
    c = b.llm("c", t, 10, 20)
    spawn = b.tool("spawn", c, 20, 30, tool_name="Agent")
    e = b.engagement("e", spawn, "ag", start=30, end=60)
    b.llm("tail", e, 40, 5_000, agent="ag")
    conn = store(tmp_path)
    put(conn, b)
    s = session(conn, "a")
    assert (s["first_ms"], s["last_ms"]) == (T0, T0 + 5_000)
    # only the session op takes the whole-subtree extent: the turn keeps its stream end (DM-53, D38 H1)
    assert row(conn, b.session)["end_ms"] == T0 + 5_000 and row(conn, e)["end_ms"] == T0 + 5_000
    assert row(conn, t)["end_ms"] == T0 + 100
    assert row(conn, spawn)["end_ms"] == T0 + 30  # a leaf keeps its own interval


def test_attr_cause_starting_after_its_engagement_attributes_normally(tmp_path):
    # 95 of 6,527 real engagements have a cause that starts later (DM-45 wording deviation): no time check
    b = B("a", end=1000)
    t = b.turn("t", start=0, end=900, turn_kind="human", label="send a message later on")
    c = b.llm("c", t, 500, 505, cost=1.0)
    send = b.tool("send", c, 505, 509, tool_name="SendMessage")
    e = b.engagement("e", b.session, "ag", start=10, end=200, caused_by=send, caused_by_basis="content_hash")
    al = b.llm("al", e, 11, 20, cost=2.0, agent="ag")
    conn = store(tmp_path)
    put(conn, b)
    assert row(conn, al)["attr_turn_rowid"] == row(conn, t)["rowid"]
    assert row(conn, t)["attr_cost_usd"] == 3.0
    attr_invariant(conn)


def test_turn_end_not_widened_over_its_agent(tmp_path):
    # DM-53, D38 H1: an agent ends 10 min after the turn's last main-thread record; the turn op keeps its
    # stream bounds (also when the engagement hangs directly under it), the session covers the agent
    ten_min = 600_000
    b = B("a", start=0, end=100)
    t = b.turn("t", start=0, end=100)
    c = b.llm("c", t, 10, 20)
    spawn = b.tool("spawn", c, 20, 30, tool_name="Agent")
    below_call = b.engagement("e1", spawn, "a1", start=30, end=60)
    b.llm("l1", below_call, 40, 100 + ten_min, agent="a1")
    direct = b.engagement("e2", t, "a2", start=40, end=60)
    b.llm("l2", direct, 50, 100 + ten_min, agent="a2")
    conn = store(tmp_path)
    put(conn, b)
    assert (row(conn, t)["start_ms"], row(conn, t)["end_ms"]) == (T0, T0 + 100)
    assert row(conn, direct)["end_ms"] == T0 + 100 + ten_min  # engagements cover their subtree
    assert row(conn, below_call)["end_ms"] == T0 + 100 + ten_min
    s = session(conn, "a")
    assert s["last_ms"] == T0 + 100 + ten_min == row(conn, b.session)["end_ms"]
