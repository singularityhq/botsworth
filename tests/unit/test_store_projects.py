import dataclasses

from tests.unit.store_builders import B, put, store

from botsworth.store import projects


def test_colliding_names_get_parent_prefix():
    got = projects.names(["/Users/a/p/bizdemo/src", "/Users/a/q/demolib/src", "/Users/a/q/other"], "/Users/a")
    assert got == {
        "/Users/a/p/bizdemo/src": "bizdemo/src",
        "/Users/a/q/demolib/src": "demolib/src",
        "/Users/a/q/other": "other",
    }


def test_colliding_pair_with_shorter_path_stays_unique():
    got = projects.names(["/src", "/x/src"], "/home/u")
    assert got == {"/src": "src", "/x/src": "x/src"}


def test_home_dir_is_tilde():
    got = projects.names(["/Users/a", "/Users/a/code"], "/Users/a")
    assert got == {"/Users/a": "~", "/Users/a/code": "code"}


def test_single_component_name():
    assert projects.names(["/private/tmp/helperbot"], "/Users/a") == {"/private/tmp/helperbot": "helperbot"}


def _session(sid, project, with_call, first, last, human=False):
    b = B(sid, start=first, end=last)
    b.ops[0] = dataclasses.replace(b.ops[0], project=project)
    t = b.turn("t0", start=first, end=last, turn_kind="human" if human else "harness")
    if with_call:
        b.llm("l0", t, first, first + 1, occ=f"occ-{sid}")
    return b


def test_counts_only_sessions_with_calls(tmp_path):
    # D38 L1: a project whose only session has 0 llm_calls has n_sessions = 0
    conn = store(tmp_path)
    put(conn, _session("s1", "/w/real", True, 0, 50), _session("s2", "/w/real", False, 10, 90))
    put(conn, _session("s3", "/w/empty", False, 5, 6))
    projects.refresh(conn, "/Users/a")
    rows = {r["project"]: r for r in conn.execute("SELECT * FROM projects")}
    assert rows["/w/real"]["n_sessions"] == 1
    assert (rows["/w/real"]["first_ms"] is not None) and rows["/w/real"]["last_ms"] > rows["/w/real"][
        "first_ms"
    ]
    assert rows["/w/empty"]["n_sessions"] == 0 and rows["/w/empty"]["n_automated"] == 0
    assert rows["/w/real"]["name"] == "real"


def test_n_automated(tmp_path):
    # DM-61: n_automated counts automated sessions among those with calls; all-automated == n_sessions
    conn = store(tmp_path)
    put(conn, _session("a1", "/tmp/bot", True, 0, 5), _session("a2", "/tmp/bot", True, 6, 9))
    put(conn, _session("m1", "/w/mixed", True, 0, 5), _session("m2", "/w/mixed", True, 6, 9, human=True))
    # is_automated is written by the fold (DM-25): m2 has a human ask, the others have none
    projects.refresh(conn, "/Users/a")
    rows = {r["project"]: (r["n_sessions"], r["n_automated"]) for r in conn.execute("SELECT * FROM projects")}
    assert rows == {"/tmp/bot": (2, 2), "/w/mixed": (2, 1)}


def test_refresh_rewrites_rows(tmp_path):
    conn = store(tmp_path)
    put(conn, _session("s1", "/w/a", True, 0, 5))
    projects.refresh(conn, "/Users/a")
    projects.refresh(conn, "/Users/a")
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
