# ruff: noqa: F811
"""Q2c detail view (views/detail.py): QRY-35, QRY-36, QRY-37; API-24; D42.

Expected values come from the fixture timeline (qstore_ids.py, q2c_store.py), the stream files, or Q2b's
`turn_rows` / `agent_rows` (a separate view), never from `detail` itself.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import asdict

import pytest
import q2c_store as Q
from q2c_store import q2cstore  # noqa: F401 (fixture)
from qstore import STREAMS, qstore  # noqa: F401 (the QF fixture)
from qstore_ids import (
    A0,
    A1,
    B0,
    C0,
    CALL_THREE_TOOLS,
    FORK_COPIES,
    PYTEST_FAIL,
    PYTEST_OK,
    READ_SMALL,
    SAGENTS,
    SEND_A,
    SPAWN_B,
    TOOL_BIG,
    TOOL_INTERRUPTED,
    turn,
)

from botsworth import pointer
from botsworth.query import NotFound, QueryError, _conn, wire
from botsworth.query.views import text
from botsworth.query.views.detail import detail
from botsworth.query.views.turn import agent_rows, turn_rows

D = importlib.import_module("botsworth.query.views.detail")  # the module; the package binds the function
STOOLS_T1 = "claude_code/stools/turn/main#0"


def rows_of(db, turn_id: str) -> list:
    return turn_rows(turn_id, db=db).rows


def stream_step_srcs() -> dict[tuple[str, int], str]:
    out = {}
    for f in sorted(STREAMS.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            r = json.loads(line)
            if r["rec"] == "step":
                out[(r["turn_id"], r["ordinal"])] = r["src"]
    return out


# ── call ─────────────────────────────────────────────────────────────────────


def test_call_detail_fields_and_turn_cost(qstore):
    d = detail(CALL_THREE_TOOLS, db=qstore)
    assert isinstance(d, wire.CallDetail) and d.kind == "call"
    row = next(r for r in rows_of(qstore, STOOLS_T1) if r.id == CALL_THREE_TOOLS)
    shared = {k: v for k, v in asdict(row).items() if k != "type"}
    assert {k: v for k, v in asdict(d).items() if k in shared} == shared  # the CallRow fields, unchanged
    assert d.turn_cost_usd == pytest.approx(0.4)  # the turn's cost: this one call
    assert d.cost_usd == pytest.approx(0.4) and d.model == "synth-model-1"
    assert (d.tok["total"], d.tok["out"]) == (1000, 300)
    assert d.speed_tok_s == pytest.approx(300 / 20)  # 300 output tokens in 20 s of derived model time
    assert d.speed_basis == "derived"
    assert d.stop_reason == "tool_use" and d.error_kind is None
    with _conn(qstore) as conn:
        want = conn.execute(
            "SELECT cost_in_usd, cost_out_usd, cost_cache_read_usd, cost_cache_write_usd, cost_basis, "
            "context_tokens FROM ops WHERE op_id = ?",
            (CALL_THREE_TOOLS,),
        ).fetchone()
    assert (
        d.cost_in_usd, d.cost_out_usd, d.cost_cache_read_usd, d.cost_cache_write_usd, d.cost_basis,
        d.context_tokens,
    ) == tuple(want)  # fmt: skip


def test_call_detail_turn_cost_is_the_whole_turn_not_the_call(q2cstore):
    db, _ = q2cstore
    d = detail(Q.CALL_1, db=db)
    # turn 1 = this call 0.40 + the agent 1.55 + req_q2 0.05
    assert d.turn_cost_usd == pytest.approx(0.40 + sum(c for c in Q.AGENT_COSTS if c) + 0.05)
    assert d.cost_usd == pytest.approx(0.40)


def test_unpriced_call_detail_has_no_cost_and_no_speed_without_tokens(qstore):
    from qstore_ids import SUNPRICED
    from qstore_ids import turn as t

    with _conn(qstore) as conn:
        call = conn.execute(
            "SELECT op_id FROM ops WHERE kind = 'llm_call' AND cost_usd IS NULL AND root_rowid = "
            "(SELECT rowid FROM sessions WHERE bundle = ?) AND duplicate_of IS NULL",
            (SUNPRICED,),
        ).fetchone()[0]
    d = detail(call, db=qstore)
    assert d.cost_usd is None and d.cost_lower_bound is True and d.n_unpriced == 1
    assert t(SUNPRICED, 0) == d.position.turn


def test_call_sections_order_no_file_read(q2cstore, qstore, monkeypatch):
    """DET-13: thinking, then each tool_use in step order; `src` is the stream's step reference."""

    def boom(*a, **k):
        raise AssertionError("detail read a source file")

    monkeypatch.setattr(pointer, "resolve", boom)
    db, _ = q2cstore
    d = detail(Q.CALL_1, db=db)
    assert [s.part for s in d.sections] == ["thinking", "tool_use", "tool_use", "tool_use", "tool_use"]
    assert d.sections[0].snippet == Q.THINK_TEXT and d.sections[0].row is None
    assert [s.row for s in d.sections[1:]] == [
        wire.RowRef("tool", Q.TOOL_LONG),
        wire.RowRef("tool", Q.TOOL_EXIT),
        wire.RowRef("tool", Q.TOOL_OK),
        wire.RowRef("agent", Q.ENGAGEMENT),  # a spawn call opens its agent row
    ]
    assert d.sections[1].snippet.startswith("Bash")  # a tool_use section shows the step summary
    assert all(s.bytes is None for s in d.sections)
    assert all(s.src.startswith("src://claude_code/") and pointer.parse(s.src) for s in d.sections)
    three = detail(CALL_THREE_TOOLS, db=qstore)
    wants = stream_step_srcs()
    assert [s.src for s in three.sections] == [wants[(STOOLS_T1, i)] for i in range(3)]
    assert [s.row.id for s in three.sections] == [PYTEST_FAIL, PYTEST_OK, READ_SMALL]


def test_call_without_blocks_has_no_sections(qstore):
    with _conn(qstore) as conn:
        call = conn.execute(
            "SELECT o.op_id FROM ops o WHERE o.kind = 'llm_call' AND o.duplicate_of IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM steps s WHERE s.op_rowid = o.rowid) "
            "AND NOT EXISTS (SELECT 1 FROM ops c WHERE c.parent_rowid = o.rowid)"
        ).fetchone()[0]
    assert detail(call, db=qstore).sections == []


# ── tool ─────────────────────────────────────────────────────────────────────


def test_tool_detail_status_neighbours(qstore, q2cstore):
    d = detail(PYTEST_FAIL, db=qstore)
    assert isinstance(d, wire.ToolDetail) and d.kind == "tool"
    assert (d.status, d.failed, d.exit_code) == ("exit:1", True, 1)
    rows = rows_of(qstore, STOOLS_T1)
    i = next(n for n, r in enumerate(rows) if r.id == PYTEST_FAIL)
    assert [r.id for r in d.neighbours.before] == [r.id for r in rows[max(0, i - 2) : i]]
    assert [r.id for r in d.neighbours.after] == [r.id for r in rows[i + 1 : i + 3]]
    assert len(d.neighbours.before) == 1 and len(d.neighbours.after) == 2  # the call, then two tools
    tool = next(r for r in rows if r.id == PYTEST_FAIL)
    shared = {k: v for k, v in asdict(tool).items() if k != "type"}
    assert {k: v for k, v in asdict(d).items() if k in shared} == shared
    assert d.src == d.input_src
    # a tool inside an agent has its siblings in the agent's rows, at the agent's depth
    db, _ = q2cstore
    nested = detail(Q.AGENT_TOOL_EXIT, db=db)
    assert nested.position.depth == 1 and nested.depth == 1
    agent_list = agent_rows(Q.ENGAGEMENT, db=db).rows
    j = next(n for n, r in enumerate(agent_list) if r.id == Q.AGENT_TOOL_EXIT)
    assert [r.id for r in nested.neighbours.before] == [r.id for r in agent_list[max(0, j - 2) : j]]
    assert [r.id for r in nested.neighbours.after] == [r.id for r in agent_list[j + 1 : j + 3]]
    assert all(r.depth == 1 for r in nested.neighbours.before + nested.neighbours.after)


def test_tool_status_when_outcome_is_set_but_exit_code_is_not(q2cstore):
    """QRY-37: the step outcome `exit:n` gives the status and the failure when `exit_code` is null."""
    db, _ = q2cstore
    d = detail(Q.TOOL_EXIT, db=db)
    assert d.exit_code is None and d.is_error is False and d.interrupted is False
    assert (d.status, d.failed) == ("exit:2", True)
    ok = detail(Q.TOOL_OK, db=db)
    assert (ok.status, ok.failed) == ("ok", False)
    assert detail(Q.AGENT_TOOL_ERR, db=db).status == "error"


def test_tool_status_interrupted(qstore):
    d = detail(TOOL_INTERRUPTED, db=qstore)
    assert (d.status, d.failed, d.interrupted) == ("interrupted", True, True)


def test_tool_input_and_output_refs(qstore, q2cstore, qroots):
    """`output_src` names the line that holds the result; the 11 KB result reads through it."""
    d = detail(TOOL_BIG, db=qstore)
    assert d.input_src == d.src and d.output_src != d.input_src
    out = text.source(d.output_src, part="output", db=qstore, roots=qroots)
    assert out["total_bytes"] == 11264 and out["block"] == "tool_result"
    db, roots = q2cstore  # a range that reaches the result keeps the tool_call's own reference
    long = detail(Q.TOOL_LONG, db=db)
    assert pointer.parse(long.input_src).locator.last >= pointer.parse(long.input_src).locator.first
    assert text.source(long.output_src, part="output", db=db, roots=roots)["text"] == "done"


def test_output_src_prefers_a_range_that_reaches_the_result():
    span = "src://claude_code/p/a.jsonl#L2-L3/message.content.0"
    call_only = "src://claude_code/p/a.jsonl#L2-L2/message.content.0"
    result = "src://claude_code/p/a.jsonl#L3-L3/message.content.0"
    assert D.output_src(span, result) == span  # real adapters: the range ends at the result
    assert D.output_src(call_only, result) == result  # QF fixture shape: the step names the result line
    assert D.output_src(call_only, None) == call_only
    other = "src://claude_code/p/b.jsonl#L9-L9/message.content.0"
    assert D.output_src(call_only, other) == call_only  # a step in another file is not a later line


def test_tool_command_src_resolves_full_command(q2cstore, qstore):
    """D42: `command` is the 160-char preview; `command_src` read with part=input gives the whole command."""
    db, roots = q2cstore
    d = detail(Q.TOOL_LONG, db=db)
    assert len(d.command) == 160 and Q.LONG_COMMAND.startswith(d.command)
    assert d.command_src == d.input_src
    full = text.source(d.command_src, part="input", db=db, roots=roots)
    assert full["text"] == Q.LONG_COMMAND and full["total_bytes"] == 2048 > len(d.command)
    # a tool with no stored command has no command_src
    assert detail(Q.TOOL_OK, db=db).command is None and detail(Q.TOOL_OK, db=db).command_src is None
    # the reference in the detail is the one the turn blade carries
    row = next(r for r in rows_of(db, Q.TURN) if r.id == Q.TOOL_LONG)
    assert row.command_src == d.command_src


# ── agent ────────────────────────────────────────────────────────────────────


def test_agent_detail_top5_plus_other_equals_row(q2cstore, qstore, monkeypatch):
    db, roots = q2cstore
    d = detail(Q.ENGAGEMENT, db=db)
    assert isinstance(d, wire.AgentDetail) and d.kind == "agent"
    assert [c.id for c in d.top_calls] == Q.AGENT_CALLS[:5]  # 0.50 .. 0.10, costliest first
    assert d.other_calls.n == 2 and d.other_calls.cost_usd == pytest.approx(
        0.05
    )  # 0.05 and the unpriced call
    assert sum(c.cost_usd for c in d.top_calls) + d.other_calls.cost_usd == pytest.approx(d.cost_usd)
    assert d.cost_usd == pytest.approx(sum(c for c in Q.AGENT_COSTS if c))
    assert (d.cost_lower_bound, d.n_unpriced, d.n_llm_calls) == (True, 1, 7)
    assert d.n_llm_calls == len(d.top_calls) + d.other_calls.n
    # the figures are the agent row's of the turn blade
    row = next(r for r in rows_of(db, Q.TURN) if isinstance(r, wire.AgentRow))
    shared = {k: v for k, v in asdict(row).items() if k not in ("type", "runs")}
    assert {k: v for k, v in asdict(d).items() if k in shared} == shared
    # the asked prompt is read through the causing call, part=input
    ask = d.runs[0].ask_src
    assert text.source(ask, part="input", db=db, roots=roots)["text"] == Q.SPAWN_PROMPT
    assert d.turn_cost_usd == pytest.approx(0.40 + 1.55 + 0.05)
    # the rule holds for every agent row of the QF store, nested agents included
    monkeypatch.setattr(D, "TOP_CALLS", 1)
    nested = detail(A0, db=qstore)
    assert len(nested.top_calls) == 1 and nested.other_calls.n == nested.n_llm_calls - 1
    assert nested.top_calls[0].cost_usd + nested.other_calls.cost_usd == pytest.approx(nested.cost_usd)
    assert nested.cost_usd == pytest.approx(2.5)  # req_ra0 2.00 + the nested agent's 0.50
    assert nested.top_calls[0].id == "claude_code/sagents/llm_call/req_ra0"
    assert nested.other_calls.cost_usd == pytest.approx(0.5)  # the nested agent's call, counted in the row


def test_agent_detail_every_qf_agent_row_sums(qstore):
    seen = 0
    with _conn(qstore) as conn:
        engagements = [r[0] for r in conn.execute("SELECT op_id FROM ops WHERE kind = 'engagement'")]
    for e in engagements:
        try:
            d = detail(e, db=qstore)
        except NotFound:
            continue  # an engagement with no causing call has no agent row
        seen += 1
        total = sum(c.cost_usd for c in d.top_calls if c.cost_usd is not None)
        total += d.other_calls.cost_usd or 0.0
        assert total == pytest.approx(d.cost_usd or 0.0), e
        assert d.n_llm_calls == len(d.top_calls) + d.other_calls.n, e
    assert seen >= 4


def test_agent_detail_any_engagement_resolves_same_row(qstore):
    first = detail(B0, db=qstore)
    for op in (SPAWN_B, B0, "claude_code/sagents/engagement/rb#1"):
        d = detail(op, db=qstore)
        assert d.id == B0 == d.position.row.id
        assert {k: v for k, v in asdict(d).items() if k != "focus_engagement"} == {
            k: v for k, v in asdict(first).items() if k != "focus_engagement"
        }
    assert detail("claude_code/sagents/engagement/rb#1", db=qstore).focus_engagement.endswith("rb#1")
    assert first.focus_engagement == B0
    assert [r.engagement for r in first.runs] == [B0, "claude_code/sagents/engagement/rb#1"]
    assert len({r.ask_src for r in first.runs}) == 1  # one causing call
    assert first.mode == "started" and detail(SEND_A, db=qstore).mode == "messaged"
    reply = detail(A1, db=qstore)  # the +cycle reply is the row of its SendMessage cause
    assert reply.caused_by == SEND_A and reply.position.turn == turn(SAGENTS, 1)


def test_agent_with_no_cause_is_not_found(qstore):
    with pytest.raises(NotFound):
        detail(C0, db=qstore)


# ── message ──────────────────────────────────────────────────────────────────


def test_message_detail_text_src(qstore):
    t = turn(SAGENTS, 0)
    d = detail(t, db=qstore)
    assert isinstance(d, wire.MessageDetail) and d.kind == "message"
    header = turn_rows(t, db=qstore).turn
    assert (d.turn, d.display_id, d.turn_kind, d.turn_origin, d.label) == (
        t, "T1", "human", "typed", "have the reviewer check the migration plan",
    )  # fmt: skip
    assert d.text_src == header.text_src and d.text_bytes == header.text_bytes
    assert d.position.row == wire.RowRef("turn", t) and d.position.turn == t and d.position.depth == 0
    assert pointer.parse(d.text_src).locator == pointer.Lines(1, 1)


def test_message_detail_harness_turn_has_no_display_id(qstore):
    d = detail("claude_code/sharness/turn/main#1", db=qstore)  # /clear
    assert d.turn_kind == "harness" and d.display_id is None and d.position.display_id is None


# ── position (QRY-36) ────────────────────────────────────────────────────────


def test_position_fields(qstore, q2cstore):
    for op, row_type in ((CALL_THREE_TOOLS, "call"), (PYTEST_OK, "tool")):
        p = detail(op, db=qstore).position
        assert (p.session, p.turn, p.display_id, p.row, p.depth) == (
            "claude_code/stools/session/-", STOOLS_T1, "T1", wire.RowRef(row_type, op), 0,
        )  # fmt: skip
    db, _ = q2cstore
    inner = detail(Q.AGENT_CALLS[1], db=db).position
    assert inner.row == wire.RowRef("call", Q.AGENT_CALLS[1]) and inner.depth == 1 and inner.turn == Q.TURN
    nested = detail(A0, db=qstore)
    assert nested.position.row == wire.RowRef("agent", A0) and nested.position.depth == 0
    deeper = detail("claude_code/sagents/llm_call/req_rn0", db=qstore).position
    assert deeper.depth == 2 and deeper.turn == turn(SAGENTS, 0)
    for copy in FORK_COPIES:  # a fork's copy is owned elsewhere: no turn
        p = detail(copy, db=qstore).position
        assert p.turn is None and p.display_id is None and p.session == "claude_code/sfork/session/-"


# ── errors ───────────────────────────────────────────────────────────────────


def test_other_kind_400(qstore):
    for op in (
        "claude_code/stools/session/-",  # a session
        "claude_code/sagents/turn/ra#0",  # an agent's own turn is not a main turn
    ):
        with pytest.raises(QueryError) as e:
            detail(op, db=qstore)
        assert not isinstance(e.value, NotFound)
    with pytest.raises(NotFound):
        detail("claude_code/stools/tool_call/nope", db=qstore)


# ── QRY-37: one failure rule in Python and SQL ───────────────────────────────

OUTCOMES = [
    None, "ok", "error", "interrupted", "exit:0", "exit:1", "exit:2", "exit:-1", "exit:00", "exit:007",
    "exit:", "exit:-", "exit:x", "exit:1x", "exit:1-2", "exit: 3", "exit:+3", "EXIT:3",
]  # fmt: skip


def test_failed_sql_equals_tool_status():
    """`status.failed_sql` and `rows.tool_status` agree for every mix of is_error, exit_code, interrupted and
    step outcome, including an outcome that is not `exit:<n>`."""
    import itertools
    import sqlite3

    from botsworth.query.views import rows as R
    from botsworth.query.views.status import failed_sql

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE ops(rowid INTEGER PRIMARY KEY, is_error INTEGER, exit_code INTEGER,"
        " interrupted INTEGER);"
        "CREATE TABLE steps(op_rowid INTEGER, ordinal INTEGER, outcome TEXT);"
    )
    cases = list(itertools.product((0, 1), (None, 0, 3), (0, 1), OUTCOMES))
    for i, (err, code, intr, outcome) in enumerate(cases, start=1):
        conn.execute("INSERT INTO ops VALUES (?,?,?,?)", (i, err, code, intr))
        if outcome is not None:
            conn.execute("INSERT INTO steps VALUES (?,?,?)", (i, 0, outcome))
            conn.execute("INSERT INTO steps VALUES (?,?,?)", (i, 1, "exit:9"))  # only the first step counts
    got = dict(conn.execute(f"SELECT rowid, {failed_sql('o')} FROM ops o").fetchall())
    assert len(got) == len(cases) == 2 * 3 * 2 * len(OUTCOMES)
    for i, (err, code, intr, outcome) in enumerate(cases, start=1):
        row = conn.execute("SELECT * FROM ops WHERE rowid = ?", (i,)).fetchone()
        step = {"outcome": outcome} if outcome is not None else None
        assert bool(got[i]) == R.tool_status(row, step)[1], (err, code, intr, outcome)
    assert sum(got.values()) > 100  # both answers occur many times


def test_session_and_turn_failed_counts_use_the_outcome_when_exit_code_is_null(q2cstore):
    """QRY-37: the session header and turn list count a step outcome `exit:n` (n != 0) with no `exit_code`."""
    from botsworth.query.views.session import session_detail, session_turns

    db, _ = q2cstore
    with _conn(db) as conn:
        none_exit = conn.execute(
            "SELECT count(*) FROM ops o WHERE o.kind = 'tool_call' AND o.exit_code IS NULL "
            "AND o.is_error = 0 AND o.interrupted = 0 AND EXISTS (SELECT 1 FROM steps s "
            "WHERE s.op_rowid = o.rowid AND s.outcome GLOB 'exit:[1-9]*')"
        ).fetchone()[0]
    assert none_exit == 2  # toolu_exit and toolu_zexit: failed by outcome alone
    session = session_detail(Q.SESSION_KEY, db=db).session
    # those two, the is_error Edit inside the agent, and the exit-code 9 tool of the interrupt turn
    assert session.n_failed == 4
    turns = {t.turn: t for t in session_turns(Q.SESSION_KEY, db=db).items if isinstance(t, wire.TurnRow)}
    assert turns[Q.TURN].n_failed == 3  # toolu_exit, and the agent's toolu_zexit and toolu_zerr
    assert turns[Q.TURN_2].n_failed == 0 and turns[Q.TURN_HARNESS].n_failed == 1
    # the rows agree with the counts
    failed_rows = [r.id for r in rows_of(db, Q.TURN) if isinstance(r, wire.ToolRow) and r.failed]
    assert failed_rows == [Q.TOOL_EXIT]
