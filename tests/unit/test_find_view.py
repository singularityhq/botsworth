# ruff: noqa: F811
"""Q2c finders (views/find.py): QRY-38, QRY-37; API-25; SES-32, SES-51 to SES-56; D38 M4, D42.

Expected hits are rebuilt from Q2b's `session_turns`, `turn_rows` and `agent_rows` (separate views) or from
the fixture's own columns, never from `find` itself.
"""

from __future__ import annotations

import pytest
import q2c_store as Q
from q2c_store import q2cstore  # noqa: F401 (fixture)
from qstore import qstore  # noqa: F401 (the QF fixture)
from qstore_ids import (
    A0,
    A1,
    ASK_USER,
    B0,
    NESTED,
    PYTEST_FAIL,
    SAGENTS,
    SKILL_SLASH_TURN,
    SLOW_BASH,
    SSKILLS,
    SSLOW1,
    STOOLS,
    TOOL_ERROR,
    TOOL_INTERRUPTED,
    turn,
)

from botsworth.query import NotFound, QueryError, _conn, wire
from botsworth.query.views.find import find, gap_label
from botsworth.query.views.session import session_turns
from botsworth.query.views.turn import agent_rows, turn_rows

ALL = [
    "claude_code/s2day", "claude_code/sauto", "claude_code/sunpriced", "codex/szero", "claude_code/sagents",
    "claude_code/sharness", "claude_code/sstretch", "claude_code/stools", "claude_code/sskills",
    "claude_code/sslow1", "claude_code/sslow2", "claude_code/sfork", "omp/somp", "codex/scodex",
]  # fmt: skip
Q2C = f"claude_code/{Q.SID}"


def key(session: str) -> str:
    return session + "/session/-"


def numbered(db, session: str) -> list[str]:
    """Numbered (non-harness) turn op_ids of a session in time order."""
    return [
        t.turn
        for t in session_turns(key(session), db=db).items
        if isinstance(t, wire.TurnRow) and t.kind != "harness"
    ]


def depth0(db, session: str) -> list[tuple[str, object]]:
    return [(t, r) for t in numbered(db, session) for r in turn_rows(t, db=db).rows]


def as_ran(db, rows) -> list:
    """Rows with each agent row followed by its own rows, to any depth (QRY-38 session order)."""
    out = []
    for r in rows:
        out.append(r)
        if isinstance(r, wire.AgentRow):
            out += as_ran(db, agent_rows(r.id, limit=2000, db=db).rows)
    return out


def session_rows(db, session: str) -> list[tuple[str, object]]:
    return [(t, r) for t in numbered(db, session) for r in as_ran(db, turn_rows(t, db=db).rows)]


def ids(result: wire.FindResult) -> list[str]:
    return [h.row.id for h in result.hits]


def value_of(row, measure: str):
    if isinstance(row, wire.CallRow):
        return {"cost": row.cost_usd, "tokens": row.tok["total"], "time": row.dur_ms}[measure]
    if isinstance(row, wire.AgentRow):
        return {"cost": row.cost_usd, "tokens": row.tok_total, "time": row.run_ms}[measure]
    if measure != "time":
        return None
    if isinstance(row, wire.ToolRow):
        if row.user_wait is not None or (row.max_silence_ms or 0) >= 300_000:
            return None
        return row.dur_ms
    return row.dur_ms


# ── biggest ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("measure", ["cost", "time", "tokens"])
def test_biggest_each_measure_order(qstore, q2cstore, measure):
    db2, _ = q2cstore
    total_hits = 0
    for db, session in [(qstore, s) for s in ALL] + [(db2, Q2C)]:
        rows = depth0(db, session)
        want = sorted(
            ((value_of(r, measure), n, r.id) for n, (t, r) in enumerate(rows)),
            key=lambda x: (-(x[0] or 0), x[1]),
        )
        want = [w for w in want if w[0] is not None and w[0] > 0]
        got = find(key(session), "biggest", measure, db=db)
        assert [h.row.id for h in got.hits] == [w[2] for w in want], (session, measure)
        assert [h.value for h in got.hits] == pytest.approx([w[0] for w in want]), (session, measure)
        assert got.total == len(want) and got.kind == "biggest"
        assert all(h.depth == 0 for h in got.hits)
        total_hits += got.total
    assert total_hits > 20


def test_biggest_covers_the_right_row_types(qstore):
    key_ = key(SAGENTS)
    by_type = {
        m: {h.row.type for h in find(key_, "biggest", m, db=qstore).hits} for m in ("cost", "time", "tokens")
    }
    assert by_type["cost"] <= {"call", "agent"} and by_type["tokens"] <= {"call", "agent"}
    assert "agent" in by_type["cost"] and "call" in by_type["cost"]
    assert {"gap", "agent"} <= by_type["time"]  # gap rows count for time only
    stools = {h.row.type for h in find(key(STOOLS), "biggest", "time", db=qstore).hits}
    assert stools == {"call", "tool"}


def test_biggest_time_excludes_waits_and_silence(qstore):
    turn_rows_ = [r for _, r in depth0(qstore, SSLOW1)]
    slow = next(r for r in turn_rows_ if r.id == SLOW_BASH)
    ask = next(r for r in turn_rows_ if r.id == ASK_USER)
    assert (slow.max_silence_ms or 0) >= 300_000 and ask.user_wait == "ask"  # both would top the ranking
    got = ids(find(key(SSLOW1), "biggest", "time", db=qstore))
    assert SLOW_BASH not in got and ASK_USER not in got
    calls = [r.id for r in turn_rows_ if isinstance(r, wire.CallRow)]
    assert calls and set(got) == set(calls)  # the session's only other time-bearing rows are its calls
    # ordinary tools stay in the ranking
    db_tools = find(key(STOOLS), "biggest", "time", db=qstore)
    assert {h.row.type for h in db_tools.hits} == {"call", "tool"}


# ── waits ────────────────────────────────────────────────────────────────────


def test_waits_floor_2min_all_labels(qstore):
    labels: set[str] = set()
    below = 0
    for session in ALL:
        # D54: gap rows of every state, plus main-thread tool rows that waited on the user, from 2 min
        rows = [(t, r) for t, r in depth0(qstore, session) if isinstance(r, wire.GapRow | wire.ToolRow)]
        waits = [(t, r) for t, r in rows if isinstance(r, wire.GapRow) or r.user_wait is not None]
        want = [(t, r) for t, r in waits if (r.dur_ms or 0) >= 120_000]
        below += len(waits) - len(want)
        got = find(key(session), "waits", db=qstore)
        assert [h.row.id for h in got.hits] == [r.id for _, r in want], session
        assert [h.value for h in got.hits] == [r.dur_ms for _, r in want]
        assert [h.row.type for h in got.hits] == [r.type for _, r in want]
        assert all(h.depth == 0 for h in got.hits)
        labels |= {gap_label(r) for _, r in want if isinstance(r, wire.GapRow)}
    assert below >= 1  # a stretch under 2 min exists and is left out
    assert {"you", "agents"} <= labels and len(labels) >= 3  # every state counts, not only "you"
    assert {"permission", "limit", "agentmsg"} <= labels  # the substate `permission` names its hit


def test_waits_include_main_thread_user_wait_tool_rows(qstore):
    got = find(key(SSLOW1), "waits", db=qstore)
    assert [(h.row.type, h.row.id, h.value) for h in got.hits] == [("tool", ASK_USER, 900_000)]
    assert got.total == 1 and got.per_turn == {turn(SSLOW1, 0): 1}


# ── failures ─────────────────────────────────────────────────────────────────


def test_failures_any_depth(qstore, q2cstore):
    got = find(key(STOOLS), "failures", db=qstore)
    assert ids(got) == [PYTEST_FAIL, TOOL_INTERRUPTED, TOOL_ERROR]
    assert all(h.value is None and h.row.type == "tool" for h in got.hits)
    db, _ = q2cstore
    nested = find(key(Q2C), "failures", db=db)
    # main thread first, then the agent's rows after the agent row; the interrupt turn is skipped
    assert ids(nested) == [Q.TOOL_EXIT, Q.AGENT_TOOL_EXIT, Q.AGENT_TOOL_ERR]
    assert [h.depth for h in nested.hits] == [0, 1, 1]
    assert Q.HARNESS_TOOL not in ids(nested)


def test_failures_equal_the_failed_flag_on_every_tool_row(qstore, q2cstore):
    db2, _ = q2cstore
    for db, session in [(qstore, s) for s in ALL] + [(db2, Q2C)]:
        want = [r.id for _, r in session_rows(db, session) if isinstance(r, wire.ToolRow) and r.failed]
        assert ids(find(key(session), "failures", db=db)) == want, session


# ── skills ───────────────────────────────────────────────────────────────────


def test_skills_slash_turn_hit(qstore):
    got = find(key(SSKILLS), "skills", db=qstore)
    assert [(h.row.type, h.row.id) for h in got.hits] == [
        ("turn", SKILL_SLASH_TURN),  # a slash turn with an event and no step is a hit on the turn
        ("tool", "claude_code/sskills/tool_call/toolu_sk1"),
        ("tool", "claude_code/sskills/tool_call/toolu_sk2"),
        ("tool", "claude_code/sskills/tool_call/toolu_sk3"),
    ]
    assert [h.label for h in got.hits] == ["/mad:bootstrap ARGS", "demo:alpha", "demo:beta", "demo:alpha"]
    assert [h.display_id for h in got.hits] == ["T1", "T2", "T2", "T3"]
    assert got.per_turn == {
        SKILL_SLASH_TURN: 1,
        "claude_code/sskills/turn/main#1": 2,
        "claude_code/sskills/turn/main#2": 1,
    }
    assert got.total == 4
    # the count matches the session header (SES-55)
    from botsworth.query.views.session import session_detail

    assert session_detail(key(SSKILLS), db=qstore).session.n_skill_loads == 4


def test_skills_none_without_loads(qstore):
    got = find(key(STOOLS), "skills", db=qstore)
    assert (got.total, got.hits, got.per_turn) == (0, [], {})


# ── agents ───────────────────────────────────────────────────────────────────


def test_agents_any_depth(qstore):
    got = find(key(SAGENTS), "agents", db=qstore)
    assert ids(got) == [A0, NESTED, B0, A1]  # the nested agent follows its host; C#0 has no cause: no row
    assert [h.depth for h in got.hits] == [0, 1, 0, 0]
    assert all(h.row.type == "agent" for h in got.hits)
    want = [r.id for _, r in session_rows(qstore, SAGENTS) if isinstance(r, wire.AgentRow)]
    assert ids(got) == want
    assert got.per_turn == {"claude_code/sagents/turn/main#0": 3, "claude_code/sagents/turn/main#1": 1}
    assert [h.label for h in got.hits][2] == "rb, rb"  # one row, two engagements


# ── search ───────────────────────────────────────────────────────────────────


def search(db, session, text, **kw):
    return find(key(session), "search", text=text, db=db, **kw)


def test_search_every_term_fields(qstore, q2cstore):
    db, _ = q2cstore
    # a term in the tool's command preview
    assert ids(search(db, Q2C, "false")) == [Q.TOOL_EXIT]
    # case-insensitive, every term must match: "run" is in the turn label, "FALSE" in the command
    assert ids(search(db, Q2C, "RUN FALSE")) == [Q.TOOL_EXIT]
    assert ids(search(db, Q2C, "false nonsense")) == []
    # a term in file_path, in tool_name (Edit), in the step snippet (thinking text), in the agent name
    assert ids(search(db, Q2C, "notes.md")) == [Q.TOOL_OK]
    # the Edit tool, and the call whose name lists its tools ("Tool request: Bash, Edit")
    assert ids(search(db, Q2C, "edit")) == [Q.AGENT_CALLS[0], Q.AGENT_TOOL_ERR]
    assert ids(search(db, Q2C, "weigh options")) == [Q.CALL_1]
    assert ids(search(db, Q2C, "rz")) == [Q.ENGAGEMENT]
    # D42: the command is the stored 160-char preview; text after it in the source is not searched
    assert Q.END_MARK in Q.LONG_COMMAND and Q.END_MARK not in Q.LONG_COMMAND[:160]
    assert ids(search(db, Q2C, Q.END_MARK)) == []
    assert ids(search(db, Q2C, "echo")) == [Q.TOOL_LONG]
    # the turn's label is a field of each of its rows: "migrations" is in turn 1's label only
    in_turn_1 = [r.id for t, r in session_rows(db, Q2C) if t == Q.TURN and not isinstance(r, wire.GapRow)]
    got = search(db, Q2C, "migrations")
    assert ids(got) == in_turn_1 and got.per_turn == {Q.TURN: len(in_turn_1)}
    # a term from the label plus a term from the row: both must match
    assert ids(search(db, Q2C, "migrations rz")) == [Q.ENGAGEMENT]
    # a stored step summary on the Q2b fixture
    assert ids(search(qstore, STOOLS, "uv pytest")) == [PYTEST_FAIL, "claude_code/stools/tool_call/toolu_pt2"]
    # ASCII case folding only, as SQLite `lower` (QRY-7)
    assert ids(search(db, Q2C, "FALSE")) == [Q.TOOL_EXIT]


def test_search_has_no_gap_rows_and_needs_text(qstore):
    got = find(key("claude_code/sstretch"), "search", text="idle", db=qstore)
    assert all(h.row.type != "gap" for h in got.hits)
    for text in ("", "   "):
        with pytest.raises(QueryError):
            find(key(STOOLS), "search", text=text, db=qstore)


# ── hit set, order, harness, errors ──────────────────────────────────────────


def test_one_hit_set_total_per_turn(qstore, q2cstore):
    db2, _ = q2cstore
    for db, session in ((qstore, SAGENTS), (qstore, STOOLS), (db2, Q2C)):
        for kind, kw in (("biggest", {}), ("failures", {}), ("agents", {}), ("search", {"text": "a"})):
            full = find(key(session), kind, db=db, **kw)
            assert sum(full.per_turn.values()) == full.total == len(full.hits)
            for h in full.hits:
                assert h.turn in full.per_turn
            if full.total >= 2:
                cut = find(key(session), kind, limit=1, db=db, **kw)
                assert len(cut.hits) == 1 and cut.hits[0] == full.hits[0]
                assert (cut.total, cut.per_turn) == (
                    full.total,
                    full.per_turn,
                )  # the count reads the whole set


def test_harness_turns_skipped(qstore, q2cstore):
    db, _ = q2cstore
    assert Q.HARNESS_CALL not in ids(find(key(Q2C), "biggest", db=db))
    assert Q.HARNESS_CALL not in ids(find(key(Q2C), "biggest", "tokens", db=db))
    assert Q.HARNESS_CALL not in ids(find(key(Q2C), "biggest", "time", db=db))
    assert Q.HARNESS_TOOL not in ids(find(key(Q2C), "failures", db=db))
    assert (
        ids(search(db, Q2C, "harness")) == []
    )  # the call, its tool and the turn label are all in the skipped turn
    assert ids(search(db, Q2C, "interrupted")) == []
    for kind in ("biggest", "waits", "failures", "skills", "agents"):
        assert Q.TURN_HARNESS not in find(key(Q2C), kind, db=db).per_turn
    with _conn(db) as conn:  # the skipped turn does hold a call, so the checks above test something
        held = conn.execute(
            "SELECT count(*) FROM ops c JOIN ops t ON t.rowid = c.parent_rowid "
            "WHERE t.op_id = ? AND c.kind = 'llm_call'",
            (Q.TURN_HARNESS,),
        ).fetchone()[0]
    assert held == 1
    # QF's harness turns: no hit names one
    for session in ("claude_code/sharness", "codex/szero"):
        harness = {
            t.turn
            for t in session_turns(key(session), db=qstore).items
            if isinstance(t, wire.TurnRow) and t.kind == "harness"
        }
        assert harness
        for kind in ("biggest", "waits", "failures", "skills", "agents"):
            assert not harness & {h.turn for h in find(key(session), kind, db=qstore).hits}
        assert ids(search(qstore, session, "clear")) == []


def test_session_order(q2cstore, qstore):
    db, _ = q2cstore
    # every row of turn 1 matches "audit" (its label); the agent's rows come right after the agent row
    expected = [r.id for t, r in session_rows(db, Q2C) if t == Q.TURN and not isinstance(r, wire.GapRow)]
    got = search(db, Q2C, "audit")
    assert ids(got) == expected
    assert expected.index(Q.ENGAGEMENT) < expected.index(Q.AGENT_CALLS[0]) < expected.index(Q.AGENT_TOOL_EXIT)
    assert [h.depth for h in got.hits if h.row.id in Q.AGENT_CALLS] == [1] * 7
    # turns by time: a text every numbered turn matches walks T1, T2 in order
    turns = [h.turn for h in search(db, Q2C, "a").hits]
    assert turns == sorted(turns, key=[Q.TURN, Q.TURN_2].index)
    # all rows of a QF session in order, via a term found in every row name or label
    rows = [r.id for t, r in session_rows(qstore, "claude_code/sagents") if not isinstance(r, wire.GapRow)]
    every = find(key("claude_code/sagents"), "search", text="e", db=qstore)
    assert ids(every) == [i for i in rows if i in set(ids(every))]


def test_errors(qstore):
    for kw in (
        {"kind": "nope"},
        {"kind": "biggest", "measure": "weight"},
        {"kind": "biggest", "limit": 0},
        {"kind": "biggest", "limit": 10001},
    ):
        with pytest.raises(QueryError):
            find(key(STOOLS), db=qstore, **kw)
    assert find(key(STOOLS), "biggest", limit=10000, db=qstore).total > 0
    with pytest.raises(NotFound):
        find("claude_code/nosuch/session/-", "biggest", db=qstore)
    got = find(STOOLS.split("/")[1], "biggest", db=qstore)  # a bare session id works (QRY-6)
    assert got.freshness.schema_version == 12
