# ruff: noqa: F811
"""Q2b row builders (views/rows.py): QRY-31, QRY-34, QRY-37, DM-36, DM-45, DM-46.

Pure cases call `tool_status` with plain dicts; the rest read the QF fixture store (tests/fixtures/qstore).
Expected figures come from the fixture timeline (qstore_ids.py, make.py), not from the code.
"""

from __future__ import annotations

import pytest
from qstore import qstore  # noqa: F401 (the QF fixture)
from qstore_ids import (
    A0,
    A1,
    B0,
    B1,
    CALL_THREE_TOOLS,
    NESTED,
    PYTEST_FAIL,
    PYTEST_OK,
    READ_SMALL,
    SAGENTS,
    SEND_A,
    SPAWN_A,
    STOOLS,
    TOOL_ERROR,
    TOOL_INTERRUPTED,
    op,
    turn,
)

from botsworth.query import _conn, wire
from botsworth.query.views import rows as R

SCODEX_T0 = "codex/scodex/turn/main#0"


def turn_rows_of(db, turn_id: str, depth: int = 0) -> list:
    with _conn(db) as conn:
        rowid = conn.execute("SELECT rowid FROM ops WHERE op_id = ?", (turn_id,)).fetchone()[0]
        return R.rows_as_ran(conn, rowid, depth)


def by_id(rows: list) -> dict:
    return {r.id: r for r in rows}


# ── QRY-37 ───────────────────────────────────────────────────────────────────


def tool(exit_code=None, interrupted=0, is_error=0) -> dict:
    return {"exit_code": exit_code, "interrupted": interrupted, "is_error": is_error}


def test_tool_status_order():
    # 1. exit_code first, even when the step says error or the op is interrupted
    assert R.tool_status(tool(exit_code=2, interrupted=1), {"outcome": "error"}) == ("exit:2", True)
    assert R.tool_status(tool(exit_code=0), {"outcome": "error"}) == ("exit:0", False)
    # 2. interrupted before the step outcome
    assert R.tool_status(tool(interrupted=1), {"outcome": "ok"}) == ("interrupted", True)
    # 3. the outcome when it is error, interrupted or exit:<n>
    assert R.tool_status(tool(), {"outcome": "error"}) == ("error", False)
    assert R.tool_status(tool(), {"outcome": "interrupted"}) == ("interrupted", False)
    assert R.tool_status(tool(), {"outcome": "exit:3"}) == ("exit:3", True)
    # 4. ok only when the outcome is ok
    assert R.tool_status(tool(), {"outcome": "ok"}) == ("ok", False)
    # 5. null otherwise, never ok
    assert R.tool_status(tool(), {"outcome": None}) == (None, False)
    assert R.tool_status(tool(), {"outcome": "weird"}) == (None, False)
    assert R.tool_status(tool(), None) == (None, False)


def test_failed_rule():
    assert R.tool_status(tool(is_error=1), None) == (None, True)  # is_error alone fails the row
    assert R.tool_status(tool(exit_code=1), None)[1] is True
    assert R.tool_status(tool(exit_code=0), None)[1] is False
    assert R.tool_status(tool(interrupted=1), None)[1] is True
    assert R.tool_status(tool(), {"outcome": "exit:1"})[1] is True
    assert R.tool_status(tool(), {"outcome": "exit:0"}) == ("exit:0", False)
    assert R.tool_status(tool(), {"outcome": "error"})[1] is False  # an `error` outcome alone is not failed


def test_failed_rule_on_the_fixture_tool_rows(qstore):
    rows = by_id(turn_rows_of(qstore, turn(STOOLS, 0)))
    rows.update(by_id(turn_rows_of(qstore, turn(STOOLS, 1))))  # the error and the interrupted call
    assert (rows[PYTEST_FAIL].status, rows[PYTEST_FAIL].failed) == ("exit:1", True)
    assert (rows[PYTEST_OK].status, rows[PYTEST_OK].failed) == ("exit:0", False)
    assert (rows[READ_SMALL].status, rows[READ_SMALL].failed) == ("ok", False)
    assert rows[TOOL_ERROR].failed is True and rows[TOOL_ERROR].is_error is True
    assert (rows[TOOL_INTERRUPTED].status, rows[TOOL_INTERRUPTED].failed) == ("interrupted", True)


# ── DM-46 ────────────────────────────────────────────────────────────────────


def test_call_row_steps_via_tools(qstore):
    stools_t = by_id(turn_rows_of(qstore, turn(STOOLS, 0)))
    call = stools_t[CALL_THREE_TOOLS]
    assert call.type == "call" and call.steps == []  # no think, say or answer step of its own
    assert call.cost_usd == pytest.approx(0.4)
    for tool_id in (PYTEST_FAIL, PYTEST_OK, READ_SMALL):  # its content is three tool_use blocks
        row = stools_t[tool_id]
        assert row.type == "tool" and row.step_id is not None and row.verb in ("run", "read")
        assert row.step_id.startswith(turn(STOOLS, 0) + "#")
    # the single tool_use call of sagents: one call row with its cost, the tool step under the agent row
    t1 = turn_rows_of(qstore, turn(SAGENTS, 0))
    c1 = by_id(t1)[op("sagents", "llm_call", "req_c1")]
    assert c1.cost_usd == pytest.approx(0.1) and c1.steps == []
    agent = next(r for r in t1 if isinstance(r, wire.AgentRow) and r.caused_by == SPAWN_A)
    assert agent.description == "Agent reviewer"


def test_call_row_own_steps_are_think_say_answer(qstore):
    rows = by_id(turn_rows_of(qstore, turn(SAGENTS, 1)))
    c2b = rows[op("sagents", "llm_call", "req_c2b")]
    assert [s.verb for s in c2b.steps] == ["say"]
    assert c2b.steps[0].step_id == f"{turn(SAGENTS, 1)}#1"
    assert c2b.steps[0].summary == "synthetic reply"


def test_is_final(qstore):
    rows = turn_rows_of(qstore, SCODEX_T0)
    calls = [r for r in rows if isinstance(r, wire.CallRow)]
    assert [(c.is_final, [s.verb for s in c.steps]) for c in calls] == [(True, ["think", "think", "answer"])]
    other = by_id(turn_rows_of(qstore, turn(SAGENTS, 1)))
    assert other[op("sagents", "llm_call", "req_c2b")].is_final is False  # a say step is not the answer


def test_unpriced_call_has_null_cost_and_lower_bound(qstore):
    with _conn(qstore) as conn:
        unpriced = conn.execute(
            "SELECT o.op_id, o.parent_rowid FROM ops o WHERE o.kind = 'llm_call' AND o.duplicate_of IS NULL "
            "AND o.cost_usd IS NULL"
        ).fetchall()
    assert unpriced
    with _conn(qstore) as conn:
        parent = conn.execute(
            "SELECT op_id FROM ops WHERE rowid = ?", (unpriced[0]["parent_rowid"],)
        ).fetchone()
    row = by_id(turn_rows_of(qstore, parent["op_id"]))[unpriced[0]["op_id"]]
    assert (row.cost_usd, row.cost_lower_bound, row.n_unpriced) == (None, True, 1)


# ── DM-36, DM-45, QRY-34 ─────────────────────────────────────────────────────


def agent_rows(db, turn_id: str, depth: int = 0) -> dict:
    return {r.id: r for r in turn_rows_of(db, turn_id, depth) if isinstance(r, wire.AgentRow)}


def test_agent_row_two_engagements_one_row(qstore):
    rows = agent_rows(qstore, turn(SAGENTS, 0))
    b = rows[B0]  # one spawn call caused rb#0 (10 min) and rb#1 (5 min, an hour later): one row
    assert B1 not in rows
    assert [r.engagement for r in b.runs] == [B0, B1]
    assert b.run_ms == 15 * 60_000  # the union of two disjoint runs
    assert [r.wall_ms for r in b.runs] == [10 * 60_000, 5 * 60_000]
    assert b.agent_names == ["rb", "rb"]
    assert b.cost_usd == pytest.approx(0.4 + 0.2)  # both runs' calls, one of them in the next turn's time


def test_cycle_reply_in_later_turn_row(qstore):
    t1, t2 = agent_rows(qstore, turn(SAGENTS, 0)), agent_rows(qstore, turn(SAGENTS, 1))
    # DM-45: T1's spawn row holds A#0 ($2.0) with its nested agent ($0.5); the SendMessage reply A#1 ($1.0)
    # sits under the same spawn call in the tree but belongs to T2
    assert t1[A0].cost_usd == pytest.approx(2.5) and A1 not in t1
    assert t2[A1].cost_usd == pytest.approx(1.0) and A0 not in t2
    assert t2[A1].caused_by == SEND_A


def test_agent_row_includes_nested(qstore):
    rows = turn_rows_of(qstore, turn(SAGENTS, 0), depth=3)
    ids = [r.id for r in rows]
    a = by_id(rows)[A0]
    assert (a.n_llm_calls, a.n_tool_calls) == (2, 1)  # req_ra0, req_rn0 and the spawnN call
    assert a.depth == 0 and by_id(rows)[NESTED].depth == 1
    # the nested agent row follows the call of A that spawned it, inside A's rows
    assert ids.index(A0) < ids.index(op("sagents", "llm_call", "req_ra0")) < ids.index(NESTED)
    assert ids.index(NESTED) < ids.index(op("sagents", "llm_call", "req_rn0"))
    assert by_id(rows)[op("sagents", "llm_call", "req_rn0")].depth == 2
    assert by_id(rows)[NESTED].cost_usd == pytest.approx(0.5)
    assert a.n_rows == 2  # depth-1 rows: the call and the nested agent row


def test_messaged_mode_for_sendmessage(qstore):
    t1, t2 = agent_rows(qstore, turn(SAGENTS, 0)), agent_rows(qstore, turn(SAGENTS, 1))
    assert t1[A0].mode == "started" and t2[A1].mode == "messaged"
    assert t2[A1].description == "SendMessage ra"
