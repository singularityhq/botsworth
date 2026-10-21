"""TST-1: the QF fixture store holds every case the M2 tests need, and the hand-derived numbers hold.

Each `test_case_<n>_*` asserts what the loader stores. Facts that come from the B3 fold (display_ix,
attr_*, time_split, stretches, projects, local rollup_time, max_silence_ms, skill_load) are in the matching
`*_b3` test. The expected values are in tests/fixtures/qstore/expected.json and were
derived by hand from tests/fixtures/qstore/make.py; a failing B3 test is a report, not an edit.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
import qstore_ids as q
from qstore import expected

from botsworth import pointer
from botsworth.query import zone

IST = ZoneInfo("Asia/Kolkata")
EXP = expected()


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def local_date(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, IST).strftime("%Y-%m-%d")


@pytest.fixture(scope="module")
def db(qstore):
    conn = sqlite3.connect(f"file:{qstore}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def op(db, op_id: str) -> sqlite3.Row:
    r = db.execute("SELECT * FROM ops_v WHERE op_id=?", (op_id,)).fetchone()
    assert r is not None, op_id
    return r


def session(db, key: str) -> sqlite3.Row:
    r = db.execute(
        "SELECT s.*, o.rowid AS root FROM sessions s JOIN ops o ON o.rowid=s.op_rowid WHERE s.bundle=?",
        (key,),
    ).fetchone()
    assert r is not None, key
    return r


def main_turns(db, key: str) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT * FROM ops_v WHERE root_rowid=? AND kind='turn' AND agent_id IS NULL ORDER BY start_ms, seq",
        (session(db, key)["root"],),
    ).fetchall()


def steps_of(db, turn_id: str) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM steps_v WHERE turn_id=? ORDER BY ordinal", (turn_id,)).fetchall()


def stretch_rows(db, key: str) -> list[dict]:
    rows = db.execute(
        "SELECT st.*, t.op_id AS turn_op FROM stretches st LEFT JOIN ops t ON t.rowid=st.turn_rowid"
        " WHERE st.root_rowid=? ORDER BY st.start_ms",
        (session(db, key)["root"],),
    ).fetchall()
    return [dict(r) for r in rows]


def check_stretches(db, key: str, want: list[dict]) -> None:
    turns = {t["op_id"]: i for i, t in enumerate(main_turns(db, key))}
    got = [
        (r["state"], r["substate"], r["start_ms"], r["end_ms"], turns.get(r["turn_op"]))
        for r in stretch_rows(db, key)
    ]
    exp = []
    for w in want:
        t = w["turn"]
        idx = int(t.removeprefix("main#")) if isinstance(t, str) else t
        exp.append((w["state"], w["substate"], ms(w["start"]), ms(w["end"]), idx))
    assert got == exp


def parts(db, op_rowid: int, scope: str) -> dict[str, int]:
    """The stored `time_split` rows of one op and scope, keyed `state` or `state/substate`."""
    rows = db.execute(
        "SELECT state, substate, ms FROM time_split WHERE op_rowid=? AND scope=?", (op_rowid, scope)
    )
    return {r["state"] + (f"/{r['substate']}" if r["substate"] else ""): r["ms"] for r in rows}


def test_schema_v10_and_no_store_zone(db):
    meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
    assert meta["schema_version"] == "11"
    assert "tz" not in meta  # D50: the zone is a request parameter, not a stored value
    assert sorted(r["bundle"] for r in db.execute("SELECT bundle FROM sessions")) == sorted(q.SESSIONS)


def test_sessions_headline_figures(db):
    for sid, want in EXP["sessions"].items():
        s = [x for x in q.SESSIONS if x.endswith("/" + sid)][0]
        got = session(db, s)
        for col in (
            "incl_cost_usd",
            "n_llm_calls",
            "wall_ms",
            "n_duplicates",
            "cost_lower_bound",
            "tok_total",
        ):
            if col in want:
                col_db = "incl_tok_total" if col == "tok_total" else col
                actual = got[col_db]
                if col == "incl_cost_usd" and want[col] is not None:
                    assert actual == pytest.approx(want[col]), (sid, col)
                else:
                    assert actual == want[col], (sid, col)


def test_case_1_two_local_days(db):
    s = session(db, q.S2DAY)
    calls = db.execute(
        "SELECT cost_usd, start_ms FROM ops_v WHERE root_rowid=? AND kind='llm_call' ORDER BY start_ms",
        (s["root"],),
    ).fetchall()
    assert [c["cost_usd"] for c in calls] == [3.0, 5.0]
    # the two calls are on one UTC day and two IST days: only a local-day rollup separates them
    assert {datetime.fromtimestamp(c["start_ms"] / 1000, UTC).strftime("%Y-%m-%d") for c in calls} == {
        "2026-10-06"
    }
    assert [local_date(c["start_ms"]) for c in calls] == ["2026-10-06", "2026-10-07"]
    asks = [t for t in main_turns(db, q.S2DAY) if t["turn_kind"] == "human"]
    assert [local_date(t["start_ms"]) for t in asks] == ["2026-10-06", "2026-10-07"]
    assert s["incl_cost_usd"] == pytest.approx(8.0)


def day_rows(db, root, tz, first, last):
    """(day, cost, calls) per local day of `first..last` summed over the 15-minute buckets (D50)."""
    edges = zone.day_edges(first, last, tz)
    out = []
    for lo, hi in zip(edges, edges[1:], strict=False):
        c, n = db.execute(
            "SELECT sum(cost_usd), sum(n_llm_calls) FROM rollup_time"
            " WHERE root_rowid=? AND bucket_start_ms >= ? AND bucket_start_ms < ? AND n_llm_calls > 0",
            (root, lo, hi),
        ).fetchone()
        out.append((zone.local_day(lo, tz), c or 0, n or 0))
    return out


def test_case_1_two_local_days_b3(db):
    want = EXP["case_1_s2day"]["b3"]
    root = session(db, q.S2DAY)["root"]
    days = day_rows(db, root, EXP["tz"], "2026-10-06", "2026-10-07")
    assert days == [(d["bucket_day"], d["cost_usd"], d["n_llm_calls"]) for d in want["day_rows"]]
    # one store, other zones: the same rows split elsewhere (D50)
    assert day_rows(db, root, "UTC", "2026-10-06", "2026-10-07") == [
        ("2026-10-06", 8.0, 2),
        ("2026-10-07", 0, 0),
    ]
    buckets = db.execute(
        "SELECT bucket_start_ms FROM rollup_time WHERE root_rowid=? AND n_llm_calls > 0 ORDER BY 1", (root,)
    ).fetchall()
    assert [h[0] % 900_000 for h in buckets] == [0, 0]
    assert session(db, q.S2DAY)["n_human_asks"] == want["n_human_asks"]
    assert [t["display_ix"] for t in main_turns(db, q.S2DAY)] == want["display_ix"]
    check_stretches(db, q.S2DAY, want["stretches"])


def test_case_2_automated_session(db):
    turns = main_turns(db, q.SAUTO)
    assert [(t["turn_kind"], t["turn_origin"]) for t in turns] == [("message", "sdk")]
    assert session(db, q.SAUTO)["entrypoint"] == EXP["case_2_sauto"]["entrypoint"]
    assert session(db, q.SAUTO)["project"] == "/u/d/helper"


def test_case_2_automated_session_b3(db):
    s = session(db, q.SAUTO)
    want = EXP["case_2_sauto"]["b3"]
    assert (s["n_human_asks"], s["is_automated"]) == (want["n_human_asks"], want["is_automated"])


def test_case_3_unpriced_call(db):
    root = session(db, q.SUNPRICED)["root"]
    calls = db.execute(
        "SELECT cost_usd, cost_basis, in_tok, out_tok FROM ops_v"
        " WHERE root_rowid=? AND kind='llm_call' ORDER BY start_ms",
        (root,),
    ).fetchall()
    want = EXP["case_3_sunpriced"]
    assert [c["cost_usd"] for c in calls] == want["call_costs"]
    assert [c["cost_basis"] for c in calls] == want["cost_basis"]
    assert all((c["in_tok"], c["out_tok"]) == (100, 20) for c in calls)  # tokens are kept when cost is null
    s = session(db, q.SUNPRICED)
    assert (s["incl_cost_usd"], s["cost_lower_bound"], s["incl_tok_total"]) == (3.0, 1, 360)
    n_unpriced = db.execute("SELECT sum(n_unpriced) FROM rollup_time WHERE root_rowid=?", (root,)).fetchone()[
        0
    ]
    assert n_unpriced == 1


def test_case_3_unpriced_call_b3(db):
    root = session(db, q.SUNPRICED)["root"]
    want = EXP["case_3_sunpriced"]["b3"]["day_rows"]
    rows = db.execute(
        "SELECT sum(cost_usd) cost_usd, sum(n_llm_calls) n_llm_calls, sum(n_unpriced) n_unpriced"
        " FROM rollup_time"
        " WHERE root_rowid=?",
        (root,),
    ).fetchall()
    assert [dict(r) for r in rows] == [{k: v for k, v in w.items() if k != "bucket_day"} for w in want]


def test_case_4_session_with_no_llm_call(db):
    s = session(db, q.SZERO)
    assert (s["provider"], s["n_llm_calls"], s["incl_cost_usd"]) == ("codex", 0, None)
    want = EXP["case_4_szero"]
    assert [(t["turn_kind"], t["turn_origin"]) for t in main_turns(db, q.SZERO)] == [
        (t["turn_kind"], t["turn_origin"]) for t in want["turns"]
    ]
    assert (s["entrypoint"], s["originator"]) == (want["entrypoint"], want["originator"])
    assert (
        db.execute(
            "SELECT count(*) FROM ops WHERE root_rowid=? AND kind='llm_call'", (s["root"],)
        ).fetchone()[0]
        == 0
    )


def test_case_4_session_with_no_llm_call_b3(db):
    want = EXP["case_4_szero"]["b3"]
    r = db.execute("SELECT * FROM projects WHERE project=?", (want["project"],)).fetchone()
    assert (r["n_sessions"], r["n_automated"]) == (want["n_sessions"], want["n_automated"])


def test_case_5_colliding_project_names(db):
    projects = {r["bundle"]: r["project"] for r in db.execute("SELECT bundle, project FROM sessions")}
    assert projects[q.S2DAY] == projects[q.STOOLS] == "/u/a/bizdemo/src"
    assert projects[q.SZERO] == "/u/b/demolib/src"
    # same last component, different parents
    assert projects[q.S2DAY].rsplit("/", 1)[1] == projects[q.SZERO].rsplit("/", 1)[1] == "src"
    assert set(projects.values()) == set(EXP["case_5_projects"]["b3"]["names"])


def test_case_5_colliding_project_names_b3(db):
    want = EXP["case_5_projects"]["b3"]
    got = {
        r["project"]: (r["name"], r["n_sessions"], r["n_automated"])
        for r in db.execute("SELECT * FROM projects")
    }
    assert got == {
        p: (want["names"][p], c["n_sessions"], c["n_automated"]) for p, c in want["counts"].items()
    }


def test_case_6_agent_tree(db):
    want = EXP["case_6_sagents"]["tree"]
    depth = {
        e: op(db, getattr(q, n))["depth"]
        for e, n in (
            ("ra#0", "A0"),
            ("rn#0", "NESTED"),
            ("rb#0", "B0"),
            ("rb#1", "B1"),
            ("rc#0", "C0"),
            ("ra#1", "A1"),
        )
    }
    assert depth == want["engagement_depths"]
    a1 = op(db, q.A1)
    assert (a1["parent"], a1["caused_by"], a1["caused_by_basis"]) == (
        q.SPAWN_A,
        q.SEND_A,
        "content_hash+cycle",
    )
    assert op(db, q.A0)["caused_by"] == q.SPAWN_A
    c0 = op(db, q.C0)
    assert (c0["caused_by"], c0["caused_by_basis"], c0["parent"]) == (
        None,
        "none",
        "claude_code/sagents/session/-",
    )
    # the nested agent's spawn call sits inside A#0's turn
    assert op(db, q.NESTED)["caused_by"] == q.SPAWN_N
    assert op(db, q.SPAWN_N)["path_sig"].startswith("main/reviewer/")
    # one spawn call, two engagements
    assert [
        r["op_id"]
        for r in db.execute("SELECT op_id FROM ops_v WHERE caused_by=? ORDER BY start_ms", (q.SPAWN_B,))
    ] == [q.B0, q.B1]
    for k, (eng, cost) in {
        "ra#0": (q.A0, 2.5),
        "rn#0": (q.NESTED, 0.5),
        "rb#0": (q.B0, 0.4),
        "rb#1": (q.B1, 0.2),
        "rc#0": (q.C0, 0.3),
        "ra#1": (q.A1, 1.0),
    }.items():
        assert op(db, eng)["incl_cost_usd"] == pytest.approx(cost), k
    assert op(db, q.A0)["incl_cost_usd"] == pytest.approx(2.0 + 0.5)  # A#0's own $2 plus the nested agent
    assert session(db, q.SAGENTS)["incl_cost_usd"] == pytest.approx(4.7)


def test_case_6_agent_runs_inputs(db):
    """DM-36: the engagement walls, per causing call (the reader's rule runs on these)."""
    want = EXP["case_6_sagents"]["agent_runs"]
    by_call = defaultdict(list)
    for r in db.execute(
        "SELECT caused_by, wall_ms FROM ops_v"
        " WHERE kind='engagement' AND caused_by IS NOT NULL AND session_id='sagents'"
    ):
        by_call[r["caused_by"].rsplit("/", 1)[1]].append(r["wall_ms"])
    for call, w in want.items():
        if call == "note":
            continue
        assert (len(by_call[call]), sum(by_call[call])) == (w["runs"], w["run_ms"]), call
    # the two engagements of the spawn call are one hour apart and do not overlap
    b0, b1 = op(db, q.B0), op(db, q.B1)
    assert b1["start_ms"] - b0["start_ms"] == 3_600_000 and b0["end_ms"] < b1["start_ms"]
    assert (b0["wall_ms"], b1["wall_ms"]) == (600_000, 300_000)


def test_case_6_attribution_b3(db):
    want = EXP["case_6_sagents"]["b3"]
    turn_by_rowid = {t["rowid"]: t["op_id"] for t in main_turns(db, q.SAGENTS)}
    for tkey, w in want["attr"].items():
        t = op(db, f"claude_code/sagents/turn/{tkey}")
        assert (t["attr_cost_usd"], t["attr_n_llm_calls"], t["attr_tok_total"]) == pytest.approx(
            (w["attr_cost_usd"], w["attr_n_llm_calls"], w["attr_tok_total"])
        )
    for req, tkey in want["attr_turn_of_call"].items():
        got = op(db, q.SAGENTS_CALLS[req])["attr_turn_rowid"]
        assert (turn_by_rowid[got] if got is not None else None) == (
            None if tkey is None else f"claude_code/sagents/turn/{tkey}"
        ), req
    assert op(db, q.SAGENTS_CALLS["req_rc0"])["cost_usd"] == pytest.approx(want["unattributed_cost_usd"])


def test_case_6_time_split_b3(db):
    want = EXP["case_6_sagents"]["b3_time"]
    s = session(db, q.SAGENTS)
    assert parts(db, s["root"], "session") == want["session_split_ms"]
    for tkey, w in want["window_split_ms"].items():
        t = op(db, f"claude_code/sagents/turn/{tkey}")
        assert parts(db, t["rowid"], "window") == w, tkey
    for tkey, w in want["span_ms"].items():
        t = op(db, f"claude_code/sagents/turn/{tkey}")
        w = dict(w)
        assert t["span_wall_ms"] == w.pop("wall"), tkey
        assert parts(db, t["rowid"], "span") == w, tkey
    check_stretches(db, q.SAGENTS, want["stretches"])


def test_case_7_harness_turns(db):
    want = EXP["case_7_sharness"]["turns"]
    got = [(t["turn_kind"], t["turn_origin"], t["label"]) for t in main_turns(db, q.SHARNESS)]
    assert got == [(w["turn_kind"], w["turn_origin"], w["label"]) for w in want]
    # harness turns stay turn ops and nothing is lost: the three calls of the human turns are the whole cost
    assert session(db, q.SHARNESS)["incl_cost_usd"] == pytest.approx(0.6)


def test_case_7_harness_turns_b3(db):
    want = EXP["case_7_sharness"]["b3"]
    assert [t["display_ix"] for t in main_turns(db, q.SHARNESS)] == want["display_ix"]
    assert session(db, q.SHARNESS)["n_human_asks"] == want["n_human_asks"]
    check_stretches(db, q.SHARNESS, want["stretches"])


def test_case_8_idle_stretch_inputs(db):
    """The turn kinds and times the DM-52 rows, the limit label and the peer label are derived from."""
    turns = main_turns(db, q.SSTRETCH)
    assert [(t["turn_kind"], t["turn_origin"]) for t in turns] == [
        (t["turn_kind"], t["turn_origin"]) for t in EXP["case_8_sstretch"]["turns"]
    ]
    assert turns[2]["label"].startswith(EXP["case_8_sstretch"]["limit_label_prefix"])
    # a human ask starts at 02:26:00 while nothing runs until the call at 02:48:00
    assert turns[1]["start_ms"] == ms("2026-10-10T02:26:00Z")
    first_call = db.execute(
        "SELECT min(start_ms) FROM ops WHERE parent_rowid=?", (turns[1]["rowid"],)
    ).fetchone()[0]
    assert first_call == ms("2026-10-10T02:48:00Z")
    # the limit turn starts at the end of 62 min of silence
    assert turns[2]["start_ms"] - turns[1]["end_ms"] == 62 * 60_000
    assert turns[3]["turn_origin"] == "peer"
    agent = db.execute(
        "SELECT start_ms, end_ms FROM ops_v WHERE root_rowid=? AND kind='engagement'",
        (session(db, q.SSTRETCH)["root"],),
    ).fetchone()
    assert agent["end_ms"] - agent["start_ms"] == 40 * 60_000
    assert session(db, q.SSTRETCH)["wall_ms"] == 10_085_000


def test_case_8_idle_stretches_b3(db):
    want = EXP["case_8_sstretch"]["b3"]
    check_stretches(db, q.SSTRETCH, want["stretches"])
    assert [r["end_ms"] - r["start_ms"] for r in stretch_rows(db, q.SSTRETCH)] == want["durations_ms"]
    assert parts(db, session(db, q.SSTRETCH)["root"], "session") == want["session_split_ms"]


def test_case_9_failed_interrupted_and_error_tools(db):
    want = EXP["case_9_stools"]
    fail = op(db, q.PYTEST_FAIL)
    assert (fail["tool_name"], fail["exit_code"]) == ("Bash", want["failed_bash"]["exit_code"])
    ok = op(db, q.PYTEST_OK)
    assert (ok["exit_code"], ok["is_error"], ok["interrupted"]) == (0, 0, 0)
    assert op(db, q.TOOL_INTERRUPTED)["interrupted"] == 1
    err = op(db, q.TOOL_ERROR)
    assert (err["tool_name"], err["is_error"]) == ("Edit", 1)
    outcome = {
        r["op_id"]: r["outcome"]
        for r in db.execute("SELECT op_id, outcome FROM steps_v WHERE bundle=?", (q.STOOLS,))
    }
    assert outcome[q.PYTEST_FAIL] == want["failed_bash"]["step_outcome"]
    assert outcome[q.TOOL_INTERRUPTED] == want["interrupted"]["step_outcome"]
    assert outcome[q.TOOL_ERROR] == want["is_error"]["step_outcome"]


def test_case_10_skill_loads(db):
    want = EXP["case_10_sskills"]
    events = [
        (json.loads(e["fields"]), e["op_id"])
        for e in db.execute(
            "SELECT * FROM events_v WHERE bundle=? AND kind='skill_invocation' ORDER BY ts_ms, rowid",
            (q.SSKILLS,),
        )
    ]
    assert [(f["skill"], f["via"]) for f, _ in events] == [(w["skill"], w["via"]) for w in want["events"]]
    slash_fields, slash_op = events[0]
    assert (
        slash_op == q.SKILL_SLASH_TURN and "step_id" not in slash_fields
    )  # a slash skill has an event and no step
    assert steps_of(db, q.SKILL_SLASH_TURN) == []
    assert op(db, q.SKILL_SLASH_TURN)["label"] == want["slash_label"]
    assert (op(db, q.SKILL_SLASH_TURN)["turn_kind"], op(db, q.SKILL_SLASH_TURN)["turn_origin"]) == (
        "human",
        "slash",
    )
    # the tool-loaded skills: Skill steps, and each event names its step
    two = steps_of(db, q.SKILL_TWO_TURN)
    assert [(s["tool_name"], s["verb"]) for s in two] == [("Skill", "skill")] * want[
        "turn_main#1_skill_steps"
    ]
    assert [f["step_id"] for f, _ in events[1:3]] == [f"{q.SKILL_TWO_TURN}#0", f"{q.SKILL_TWO_TURN}#1"]
    assert events[3][0]["step_id"] == f"{q.SKILL_ONE_TURN}#0"
    assert [s["target"] for s in two] == ["demo:alpha", "demo:beta"]


def test_case_10_skill_loads_b3(db):
    want = EXP["case_10_sskills"]["b3"]
    loads = {
        f"{r['turn_id']}#{r['ordinal']}": r["skill_load"]
        for r in db.execute("SELECT * FROM steps_v WHERE bundle=? AND skill_load IS NOT NULL", (q.SSKILLS,))
    }
    assert loads == {f"claude_code/sskills/turn/{k}": v for k, v in want["skill_load"].items()}
    assert len(loads) == want["n_steps_with_skill_load"]
    for tkey, cost in want["attr_cost_usd"].items():
        assert op(db, f"claude_code/sskills/turn/{tkey}")["attr_cost_usd"] == pytest.approx(cost)


def test_case_11_derived_duration_and_user_wait(db):
    want = EXP["case_11_sslow1"]
    bash = op(db, q.SLOW_BASH)
    assert (bash["dur_ms"], bash["time_prov"], bash["command_key"]) == (
        want["bash"]["dur_ms"],
        "derived",
        "make bench",
    )
    assert bash["user_wait"] is None
    ask = op(db, q.ASK_USER)
    assert (ask["tool_name"], ask["dur_ms"], ask["user_wait"]) == ("AskUserQuestion", 900_000, "ask")
    perm = op(db, q.AGENT_PERMISSION)
    assert (perm["tool_name"], perm["dur_ms"], perm["user_wait"]) == ("Bash", 240_000, "permission")
    assert perm["agent_id"] is not None


def test_case_11_max_silence_b3(db):
    assert (
        op(db, q.SLOW_BASH)["max_silence_ms"]
        == EXP["case_11_sslow1"]["b3"]["max_silence_ms"]["toolu_slowbash"]
    )


def test_case_11_source_file_timestamps(db):
    """DM-40 input: the bundle's record timestamps (ts_list) leave a 30-min silence under the Bash call."""
    path = "-u-h-slowproj-app/sslow1.jsonl"
    # the stream carries ts_list; read it back from the stream file (the store does not keep it)
    from qstore import STREAMS

    recs = [json.loads(x) for x in (STREAMS / "sslow1.jsonl").read_text().splitlines()]
    sf = next(r for r in recs if r["rec"] == "source_file")
    assert sf["path"] == path
    t0, t1 = ms("2026-10-07T04:02:00Z"), ms("2026-10-07T04:42:00Z")
    inside = [t for t in sf["ts_list"] if t0 <= t <= t1]
    assert inside == [t0, ms("2026-10-07T04:12:00Z"), t1]
    gaps = [b - a for a, b in zip(inside, inside[1:], strict=False)]
    assert max(gaps) == 1_800_000


def test_case_12_duplicate_call_changes_nothing(db):
    want = EXP["case_12_sfork"]
    dups = {r["op_id"] for r in db.execute("SELECT op_id FROM ops_v WHERE duplicate_of IS NOT NULL")}
    assert dups == set(q.FORK_COPIES)
    assert [q.CALL_COPIED, q.TOOL_INTERRUPTED, q.TOOL_ERROR] == [
        r["duplicate_of"]
        for r in db.execute(
            "SELECT duplicate_of FROM ops WHERE op_id IN (?,?,?) ORDER BY rowid", q.FORK_COPIES
        )
    ]
    assert session(db, q.SFORK)["incl_cost_usd"] == pytest.approx(want["fork_incl_cost_usd"])
    assert session(db, q.STOOLS)["incl_cost_usd"] == pytest.approx(want["owner_incl_cost_usd"])
    owned = db.execute(
        "SELECT sum(cost_usd) FROM ops WHERE kind='llm_call' AND duplicate_of IS NULL"
    ).fetchone()[0]
    everything = db.execute("SELECT sum(cost_usd) FROM ops WHERE kind='llm_call'").fetchone()[0]
    assert owned == pytest.approx(EXP["store_total_owned_cost_usd"]["value"])
    assert everything == pytest.approx(owned + 0.30)  # the copy would add the owner's $0.30 again
    assert session(db, q.STOOLS)["n_duplicates"] == 0


def test_case_13_one_call_three_tools(db):
    want = EXP["case_13_stools"]
    call = op(db, q.CALL_THREE_TOOLS)
    assert call["tok_total"] == want["tok_total"] == 1000
    tools = db.execute(
        "SELECT op_id, command_key FROM ops_v WHERE parent=? ORDER BY start_ms, rowid", (q.CALL_THREE_TOOLS,)
    ).fetchall()
    assert [t["op_id"].rsplit("/", 1)[1] for t in tools] == want["tools"]
    assert [t["command_key"] for t in tools] == want["command_keys"]
    assert {t["op_id"]: op(db, t["op_id"])["tool_name"] for t in tools}[q.READ_SMALL] == "Read"


def test_case_14_omp_ttft_and_codex_reasoning(db, qroots):
    want = EXP["case_14_providers"]
    for call, ttft in zip(q.OMP_CALLS, want["somp"]["ttft_ms"], strict=True):
        r = op(db, call)
        assert (r["ttft_ms"], r["ttft_prov"], r["provider"]) == (ttft, "present", "omp")
    cx = op(db, q.CODEX_CALL)
    assert (cx["provider"], cx["reasoning_tok"]) == ("codex", want["scodex"]["reasoning_tok"])
    turn = db.execute(
        "SELECT op_id FROM ops_v WHERE root_rowid=? AND kind='turn'", (cx["root_rowid"],)
    ).fetchone()[0]
    think = [s for s in steps_of(db, turn) if s["verb"] == "think"]
    assert len(think) == want["scodex"]["think_steps"]
    first = pointer.resolve(think[0]["src"], qroots, part="summary.0.text")
    assert first["text"] == want["scodex"]["reasoning_summary"]
    for s in think:  # the pointer addresses the reasoning item itself
        item = json.loads(pointer.resolve(s["src"], qroots)["text"])
        assert item["type"] == "reasoning" and item["encrypted_content"].startswith("enc-")
    assert json.loads(pointer.resolve(think[1]["src"], qroots)["text"])["summary"] == []


def test_case_14_ttft_rollup_b3(db):
    want = EXP["case_14_providers"]["somp"]["b3"]
    root = session(db, q.SOMP)["root"]
    row = db.execute(
        "SELECT sum(n_ttft) n, sum(ttft_ms_sum) s FROM rollup_time WHERE root_rowid=?",
        (root,),
    ).fetchone()
    assert (row["n"], row["s"]) == (want["n_ttft"], want["ttft_ms_sum"])


def test_case_15_large_tool_result(db, qroots):
    want = EXP["case_15_stools"]
    big = op(db, q.TOOL_BIG)
    assert big["result_bytes"] == want["result_bytes"] == 11264
    step = db.execute("SELECT src FROM steps_v WHERE op_id=?", (q.TOOL_BIG,)).fetchone()
    got = pointer.resolve(step["src"], qroots, part="content")
    assert got["total_bytes"] == 11264 and len(got["text"].encode()) == 11264
    assert got["total_bytes"] > 10 * 1024


def test_case_16_two_sessions_in_one_project(db):
    want = EXP["case_16_slow"]
    rows = [session(db, k) for k in (q.SSLOW1, q.SSLOW2)]
    assert {r["project"] for r in rows} == {want["project"]} == {q.SLOW_PROJECT}
    assert [r["wall_ms"] for r in rows] == [3_600_000, 1_800_000]
    assert sum(r["wall_ms"] for r in rows) == want["wall_ms_total"]
    assert all(ms("2026-10-06T00:00:00Z") <= r["first_ms"] < ms("2026-10-12T00:00:00Z") for r in rows)
    assert {local_date(r["first_ms"]) for r in rows} == {"2026-10-07"}
    # the human ask that ends the 25-min silence of sslow2 starts at 06:27:00
    turns = main_turns(db, q.SSLOW2)
    assert turns[1]["start_ms"] - turns[0]["end_ms"] == 25 * 60_000
    assert turns[1]["turn_kind"] == "human"


def test_case_16_slow_scenario_b3(db):
    want = EXP["case_16_slow"]["b3"]
    total = defaultdict(int)
    for k in (q.SSLOW1, q.SSLOW2):
        for part, value in parts(db, session(db, k)["root"], "session").items():
            total[part] += value
    assert dict(total) == want["time_split_ms"]
    assert sum(total.values()) == EXP["case_16_slow"]["wall_ms_total"]
    st = want["stretches"][0]
    check_stretches(db, q.SSLOW2, [{**st, "turn": 0}])
    assert stretch_rows(db, q.SSLOW1) == []  # the 15-min ask is a main-thread call: a tool row, no stretch
    assert op(db, q.SLOW_BASH)["max_silence_ms"] == want["slowest_tool"]["max_silence_ms"]


def test_cost_equals_owned_leaf_sum(db):
    """Invariant 3 on the fixture: a session's inclusive cost is the sum of its owned llm_call costs."""
    for s in db.execute("SELECT s.bundle, s.incl_cost_usd, s.op_rowid FROM sessions s"):
        owned = db.execute(
            "SELECT sum(cost_usd) FROM ops WHERE root_rowid=? AND kind='llm_call' AND duplicate_of IS NULL",
            (s["op_rowid"],),
        ).fetchone()[0]
        assert s["incl_cost_usd"] == (pytest.approx(owned) if owned is not None else None), s["bundle"]


def test_cost_invariant_dm45(db):
    for s in db.execute("SELECT s.bundle, s.incl_cost_usd, s.op_rowid FROM sessions s"):
        by_turn = db.execute(
            "SELECT sum(attr_cost_usd) FROM ops WHERE root_rowid=? AND kind='turn' AND agent_id IS NULL",
            (s["op_rowid"],),
        ).fetchone()[0]
        loose = db.execute(
            "SELECT sum(cost_usd) FROM ops WHERE root_rowid=? AND kind='llm_call' AND duplicate_of IS NULL"
            " AND attr_turn_rowid IS NULL",
            (s["op_rowid"],),
        ).fetchone()[0]
        total = (by_turn or 0) + (loose or 0)
        assert total == pytest.approx(s["incl_cost_usd"] or 0), s["bundle"]


def test_refs_resolve(db, qroots):
    """Every op, step and event reference resolves under qroots with no SourceMissing and no drift."""
    n = 0
    for table in ("ops_v", "steps_v", "events_v"):
        for r in db.execute(f"SELECT src FROM {table}"):
            got = pointer.resolve(r["src"], qroots)
            assert got["drift"] is False, r["src"]
            n += 1
    assert (
        n
        == db.execute(
            "SELECT (SELECT count(*) FROM ops) + (SELECT count(*) FROM steps) + (SELECT count(*) FROM events)"
        ).fetchone()[0]
    )
    assert n > 100
