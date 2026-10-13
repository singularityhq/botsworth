"""Display ids (design §6.1a): outline rows, resolution both ways, anchoring, batched spawns."""

from __future__ import annotations

import shutil
import sqlite3

import pytest
from query_fixture import build

import botsworth
from botsworth.query import QueryError
from botsworth.query.outline import display_of, resolve

SA = "claude_code/sA/session/-"
A_T1 = "claude_code/sA/turn/main#0"
X_T = "claude_code/sA/turn/x#0"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    d = tmp_path_factory.mktemp("store")
    build(d / "store.db")
    return d / "store.db"


def rows(db, ident, depth=1):
    return botsworth.outline(ident, depth=depth, db=db)["rows"]


def test_session_outline_depth1(db):
    r = rows(db, SA)
    assert [x["display_id"] for x in r] == [
        "T1",
        "T1.1",
        "T1.2",
        "T1.3",
        "T1.4",
        "T2",
        "T2.1",
        "T2.2",
        "T2.3",
    ]
    t1 = r[0]
    assert t1 == {
        "display_id": "T1", "id": A_T1, "kind": "turn", "verb": None, "target": "fix the rounding bug",
        "outcome": "ok", "dur_ms": 35000, "cost_usd": pytest.approx(0.31), "level": 0,
    }  # fmt: skip
    run = r[1]
    assert (run["id"], run["verb"], run["target"], run["outcome"], run["dur_ms"]) == (
        f"{A_T1}#0", "run", "uv run pytest -q tests/cost", "exit:1", 4000,
    )  # fmt: skip
    assert all(x["cost_usd"] is None for x in r if x["kind"] == "step")


def test_session_outline_depth2_expands_spawns_and_orphans(db):
    r = rows(db, SA, depth=2)
    got = [(x["display_id"], x["level"]) for x in r]
    assert ("T1.2/1", 1) in got and ("T1.2/1.1", 1) in got and ("T1.2/1.2", 1) in got
    assert ("E1/1", 1) in got and ("E1/1.1", 1) in got
    assert [x["display_id"] for x in r].index("T1.2/1") == [x["display_id"] for x in r].index("T1.2") + 1


def test_every_display_id_resolves_both_ways(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    anchor = conn.execute("SELECT rowid FROM ops WHERE op_id = ?", (SA,)).fetchone()[0]
    for row in rows(db, SA, depth=3):
        what, found = resolve(conn, row["display_id"], anchor)
        stable = f"{found['turn_id']}#{found['ordinal']}" if what == "step" else found["op_id"]
        assert stable == row["id"], row
        what2, by_id = resolve(conn, row["id"])
        assert display_of(conn, what2, by_id) == row["display_id"]
    assert (
        display_of(
            conn,
            "op",
            conn.execute("SELECT * FROM ops WHERE op_id = 'claude_code/sA/engagement/y#0'").fetchone(),
        )
        == "E1"
    )


def test_display_ids_accepted_by_every_call(db):
    assert botsworth.show("sA:T1.2/1.1", db=db)["op"]["op_id"] == "claude_code/sA/tool_call/toolu_x1"
    assert botsworth.steps("sA:T1.2/1", db=db)["turn"]["op_id"] == X_T
    assert botsworth.timeline(f"{A_T1}:T2", db=db)["root"]["op_id"] == "claude_code/sA/turn/main#1"
    q = botsworth.query({"scope": {"op": "sA:T1"}, "select": "children", "metrics": ["cost_usd"]}, db=db)
    assert len(q["rows"]) == 4
    assert botsworth.outline("sA:T1.3", db=db)["scope"] == {
        "id": f"{A_T1}#2",
        "display_id": "T1.3",
        "kind": "step",
    }


def test_scoped_outlines(db):
    step = rows(db, f"{A_T1}#2")
    assert [x["display_id"] for x in step] == ["T1", "T1.3"]
    call = rows(db, "claude_code/sA/llm_call/req_a3")
    assert call[0]["id"] == A_T1
    eng = rows(db, "claude_code/sA/engagement/x#0")
    assert [x["display_id"] for x in eng] == ["T1.2/1", "T1.2/1.1", "T1.2/1.2"]


@pytest.mark.parametrize(
    "ident, msg",
    [
        ("T1", "needs a session"),
        ("sA:T9", "out of range"),
        ("sA:T1.9", "no step"),
        ("sA:T1/1", "must follow a step"),
        ("nope:T1", "anchor"),
        ("sA:E5", "out of range"),
    ],
)
def test_bad_display_ids(db, ident, msg):
    with pytest.raises(QueryError, match=msg):
        botsworth.show(ident, db=db)


def test_batched_spawn_steps_share_one_path(db, tmp_path):
    copy = tmp_path / "b.db"
    shutil.copy(db, copy)
    conn = sqlite3.connect(copy)
    conn.execute(
        "INSERT INTO steps(root_rowid, turn_rowid, ordinal, op_rowid, verb, target, outcome,"
        " src_file, src_loc, src_h) "
        "SELECT root_rowid, turn_rowid, 4, op_rowid, verb, 'second child', outcome, src_file, src_loc, src_h "
        "FROM steps WHERE turn_rowid = (SELECT rowid FROM ops WHERE op_id = ?) AND ordinal = 1",
        (A_T1,),
    )
    conn.commit()
    conn.close()
    assert botsworth.show("sA:T1.5/1", db=copy)["op"]["op_id"] == X_T
    ids = [x["display_id"] for x in rows(copy, A_T1, depth=2)]
    assert ids.count("T1.2/1") == 1 and "T1.5/1" not in ids  # the lowest-ordinal step names the path
