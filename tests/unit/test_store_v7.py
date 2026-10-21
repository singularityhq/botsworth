"""Schema v7 and compact rows: DM-1 (schema), DM-9, DM-13, DM-14, DM-15, DM-26 (store side)."""

import sqlite3
from collections import defaultdict

import pytest
from store_builders import B, owners_ok, put, row, store

from botsworth.store import ingest, refs, schema
from botsworth.stream import StreamDefects

# DM §2.2 / §2.3: every index the spec names
V7_INDEXES = {
    "ops_op_id", "ops_parent_cost", "ops_parent_tok", "ops_root_kind", "ops_root_pre", "ops_kind_start",
    "ops_kind_cost", "ops_occ", "ops_command_key", "ops_commands", "owner_key", "ops_attr_turn",
    "ops_parent_kind_start", "ops_caused_by", "steps_turn", "steps_op", "steps_root", "steps_skill_load",
    "sessions_key", "sessions_op", "sessions_project_time", "sessions_account_time",
    "sessions_automated_time", "rollup_bucket", "rollup_root",
    "stretches_root", "stretches_len", "events_id", "events_kind_time", "events_session", "events_op",
    "events_root",
}  # fmt: skip
DROPPED_OPS = {
    "parent", "caused_by", "session_id", "bundle", "project", "cwd", "account", "path_sig", "occ_key",
    "src", "args_json",
}  # fmt: skip
HASH = "0123456789abcdef"


def names(conn, kind):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))}


def columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_ensure_creates_v10_tables_and_indexes(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    schema.ensure(conn)
    assert schema.SCHEMA_VERSION == 12
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "12"
    for table in schema.TABLES:
        n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        assert n == (1 if table == "meta" else 0), table  # only meta.schema_version
    tables = names(conn, "table")
    assert {"stretches", "time_split", "projects", "src_files", "names"} <= tables
    assert "op_closure" not in tables and "tz" not in schema.META_KEYS
    assert "stream_files" not in tables and "reject_reason" in columns(conn, "source_files")  # D52
    indexes = names(conn, "index")
    assert V7_INDEXES <= indexes
    assert not {"ops_bundle", "ops_command_norm", "closure_desc"} & indexes
    assert names(conn, "view") == {"ops_v", "steps_v", "events_v"}
    assert not DROPPED_OPS & columns(conn, "ops")
    assert not {"turn_id", "op_id", "bundle", "src"} & columns(conn, "steps")
    assert not {"bundle", "src"} & columns(conn, "events")
    assert {"caused_by_rowid", "cwd_id", "account_id", "path_sig_id", "occ_hash", "src_file", "src_loc",
            "src_h", "pre", "post", "turn_kind", "turn_origin", "display_ix", "user_wait",
            "max_silence_ms", "attr_turn_rowid", "span_wall_ms"} <= columns(conn, "ops")  # fmt: skip
    assert {"turn_rowid", "op_rowid", "skill_load"} <= columns(conn, "steps")
    assert {"title_source", "display_title", "n_human_asks", "is_automated", "activity_bin_ms",
            "activity_tok"} <= columns(conn, "sessions")  # fmt: skip
    # D54: the split lives in `time_split`; the per-state columns and `waits_on_user` are gone
    assert not {"model_ms", "tool_ms", "agents_ms", "none_ms"} & columns(conn, "sessions")
    assert not {"model_ms", "span_model_ms", "span_none_ms", "waits_on_user"} & columns(conn, "ops")
    assert {"substate"} <= columns(conn, "stretches") and "next_turn_kind" not in columns(conn, "stretches")
    assert columns(conn, "time_split") == {"root_rowid", "op_rowid", "scope", "state", "substate", "ms"}
    assert {"timed_out_tok", "timed_ms", "n_ttft", "ttft_ms_sum"} <= columns(conn, "rollup_time")
    assert not {"bucket_day", "bucket_kind"} & columns(conn, "rollup_time")  # DM-41 [D50]
    bucket_ix = [r[2] for r in conn.execute("PRAGMA index_info(rollup_bucket)")]
    assert bucket_ix == ["bucket_start_ms", "root_rowid"]
    # the three view column sets carry every dropped v6 name
    assert DROPPED_OPS - {"occ_key", "args_json"} <= columns(conn, "ops_v")
    assert {"turn_id", "op_id", "bundle", "src"} <= columns(conn, "steps_v")
    assert {"bundle", "src"} <= columns(conn, "events_v")


def test_ensure_upgrades_a_v6_store_in_place(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    schema.ensure(conn)
    conn.execute("CREATE TABLE op_closure(a)")  # what a v6 file still holds
    conn.execute("CREATE TABLE stream_files(a)")  # what a v8 file still holds
    conn.execute("UPDATE meta SET value='6' WHERE key='schema_version'")
    conn.commit()
    schema.ensure(conn)
    assert not {"op_closure", "stream_files"} & names(conn, "table")
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "12"


def full_bundle():
    """A depth-4 tree where every op carries project/cwd/account, a distinct src and a caused_by link."""
    common = {"project": "/repo", "cwd": "/repo/sub", "account": "acct"}
    b = B("v", start=0, end=1000, cwd="/repo/sub")
    t = b.turn("main#0", start=0, end=1000, **common)
    c = b.llm("r0", t, 0, 100, **common)
    spawn = b.tool("t0", c, 100, 900, tool_name="Agent", **common)
    e = b.engagement("a#0", spawn, "a", start=110, end=800, agent_type="coder", caused_by=spawn, **common)
    t2 = b.turn("a#0", e, start=110, end=800, agent="a", **common)
    b.llm("ra", t2, 110, 400, agent="a", model="m2", **common)
    b.tool("tb", t2, 410, 450, agent="a", tool_name="Bash", command="ls", **common)
    b.llm("r1", t, 950, 1000, caused_by=spawn, **common)
    for i, o in enumerate(b.ops):
        o.src = f"src://claude_code/proj/v.jsonl#L{i + 1}-L{i + 2}" + (f"?h={HASH}" if i % 2 else "")
    return b


def test_ops_v_reproduces_stream_fields(tmp_path):
    b = full_bundle()
    conn = store(tmp_path)
    put(conn, b)
    got = {r["op_id"]: r for r in conn.execute("SELECT * FROM ops_v")}
    assert set(got) == {o.op_id for o in b.ops}
    for o in b.ops:
        r = got[o.op_id]
        got_fields = tuple(
            r[k] for k in ("parent", "caused_by", "session_id", "project", "cwd", "account", "src")
        )
        assert got_fields == (o.parent, o.caused_by, o.session_id, "/repo", o.cwd, o.account, o.src)
        assert r["bundle"] == "claude_code/v"
    assert sum(r["caused_by"] is not None for r in got.values()) == 2  # the links the fixture sets
    assert got["claude_code/v/engagement/a#0"]["path_sig"] == "main/coder/engagement"
    assert got["claude_code/v/llm_call/ra"]["path_sig"] == "main/coder/m2"
    assert got["claude_code/v/session/-"]["parent"] is None
    # the table keeps rowids and interned ids, not the strings
    t = conn.execute(
        "SELECT parent_rowid, caused_by_rowid, cwd_id FROM ops WHERE op_id=?", ("claude_code/v/llm_call/r1",)
    )
    parent_rowid, caused_rowid, cwd_id = t.fetchone()
    assert (
        conn.execute("SELECT op_id FROM ops WHERE rowid=?", (parent_rowid,))
        .fetchone()[0]
        .endswith("turn/main#0")
    )
    assert (
        conn.execute("SELECT op_id FROM ops WHERE rowid=?", (caused_rowid,))
        .fetchone()[0]
        .endswith("tool_call/t0")
    )
    assert conn.execute("SELECT s FROM names WHERE id=?", (cwd_id,)).fetchone()[0] == "/repo/sub"


def test_occ_hash_one_owner_across_bundles(tmp_path):
    def shared(sid, start):
        x = B(sid, start=start, end=start + 100)
        t = x.turn("main#0", start=start, end=start + 100)
        x.llm(f"a@{sid}", t, start, start + 5, occ="req-shared")
        x.llm(f"b@{sid}", t, start + 10, start + 15, occ=f"own-{sid}")
        return x

    conn = store(tmp_path)
    put(conn, shared("late", 1000), shared("early", 0))
    owners_ok(conn)
    rows = conn.execute("SELECT op_id, duplicate_of, occ_hash FROM ops WHERE occ_hash IS NOT NULL").fetchall()
    by = {r["op_id"]: r for r in rows}
    assert by["claude_code/late/llm_call/a@late"]["duplicate_of"] == "claude_code/early/llm_call/a@early"
    assert by["claude_code/early/llm_call/a@early"]["duplicate_of"] is None
    assert (
        by["claude_code/late/llm_call/a@late"]["occ_hash"]
        == refs.occ_hash("req-shared")
        == by["claude_code/early/llm_call/a@early"]["occ_hash"]
    )
    assert (
        by["claude_code/early/llm_call/b@early"]["occ_hash"]
        != by["claude_code/late/llm_call/b@late"]["occ_hash"]
    )
    assert (
        sum(1 for r in rows if r["occ_hash"] == refs.occ_hash("req-shared") and r["duplicate_of"] is None)
        == 1
    )
    assert (
        conn.execute(
            "SELECT count(*) FROM ops WHERE occ_hash IS NOT NULL AND length(occ_hash) != 8"
        ).fetchone()[0]
        == 0
    )


def walk_descendants(conn, rowid):
    kids = defaultdict(list)
    for rid, parent in conn.execute("SELECT rowid, parent_rowid FROM ops"):
        kids[parent].append(rid)
    out, todo = set(), [rowid]
    while todo:
        n = todo.pop()
        out.add(n)
        todo += kids[n]
    return out


def test_nested_set_equals_parent_walk(tmp_path):
    conn = store(tmp_path)
    put(conn, full_bundle())
    depths = [r[0] for r in conn.execute("SELECT depth FROM ops")]
    assert max(depths) >= 3
    ops = conn.execute("SELECT rowid, root_rowid, pre, post FROM ops").fetchall()
    assert sorted(r["pre"] for r in ops) == list(range(len(ops)))  # 0-based pre-order, no gaps
    for r in ops:
        by_interval = {
            x[0]
            for x in conn.execute(
                "SELECT rowid FROM ops WHERE root_rowid=? AND pre BETWEEN ? AND ?",
                (r["root_rowid"], r["pre"], r["post"]),
            )
        }
        assert by_interval == walk_descendants(conn, r["rowid"])
        assert r["post"] >= r["pre"]
    # ancestors by interval equal the parent walk (DM-15)
    leaf = conn.execute(
        "SELECT rowid, root_rowid, pre, post FROM ops WHERE op_id LIKE '%tool_call/tb'"
    ).fetchone()
    by_interval = {
        x[0]
        for x in conn.execute(
            "SELECT rowid FROM ops WHERE root_rowid=? AND pre <= ? AND post >= ?",
            (leaf["root_rowid"], leaf["pre"], leaf["post"]),
        )
    }
    chain, node = set(), leaf["rowid"]
    while node is not None:
        chain.add(node)
        node = conn.execute("SELECT parent_rowid FROM ops WHERE rowid=?", (node,)).fetchone()[0]
    assert by_interval == chain


def test_refold_keeps_pre_post(tmp_path):
    def shared(sid, start):
        x = B(sid, start=start, end=start + 100)
        t = x.turn("main#0", start=start, end=start + 100)
        u = x.turn("main#1", start=start + 50, end=start + 100)
        x.llm(f"a@{sid}", t, start, start + 5, occ="k1")
        x.llm(f"b@{sid}", u, start + 60, start + 65, occ="k2")
        return x

    conn = store(tmp_path)
    late = shared("late", 1000)
    put(conn, late)
    before = conn.execute("SELECT op_id, pre, post FROM ops ORDER BY rowid").fetchall()
    (result,) = put(conn, shared("early", 0))
    assert result.refolded == ["claude_code/late"]  # `late` lost its owners and was refolded
    assert row(conn, late.session)["incl_cost_usd"] is None
    assert (
        conn.execute(
            "SELECT op_id, pre, post FROM ops WHERE op_id LIKE 'claude_code/late/%' ORDER BY rowid"
        ).fetchall()
        == before
    )


def ingest_one(tmp_path, builder):
    bundle, db = builder.bundle(), tmp_path / "s.db"
    assert ingest.ingest([bundle], db=db).rejected == []
    conn = ingest.open_store(db)
    conn.row_factory = sqlite3.Row
    return conn, bundle


def test_command_clipped_160(tmp_path):
    long = "echo " + "x" * 2043  # 2,048 characters
    assert len(long) == 2048
    b = B("c")
    call = b.tool("t0", b.turn("main#0"), 0, 5, tool_name="Bash", command=long)
    # whitespace at the cut is stripped (common.turns.clip semantics)
    spaced = b.tool("t1", b.ops[1].op_id, 6, 7, tool_name="Bash", command="y" * 159 + "  " + "z" * 100)
    b.tool("t2", b.ops[1].op_id, 8, 9, tool_name="Bash", command="short")
    conn, bundle = ingest_one(tmp_path, b)
    got = {
        r["op_id"]: r["command"]
        for r in conn.execute("SELECT op_id, command FROM ops_v WHERE command IS NOT NULL")
    }
    assert got[call] == long[:160] and len(got[call]) == 160
    assert got[spaced] == "y" * 159
    assert got["claude_code/c/tool_call/t2"] == "short"
    assert len(bundle.ops[2].command) == 2048  # the bundle keeps the full command; only the store clips it


def test_file_path_and_target_clipped_160(tmp_path):
    b = B("p")
    t = b.turn("main#0")
    call = b.tool("t0", t, 0, 5, tool_name="Read", file_path="/very/long/" + "d" * 289)
    assert len(b.ops[-1].file_path) == 300
    b.step(t, call, "read", target="a" * 200)
    conn, _ = ingest_one(tmp_path, b)
    assert conn.execute("SELECT length(file_path) FROM ops WHERE op_id=?", (call,)).fetchone()[0] == 160
    assert conn.execute("SELECT length(target) FROM steps").fetchone()[0] == 160


def test_never_stored_markers(tmp_path):
    def marker(name):
        return name + "-" + "M" * (39 - len(name))  # 40 chars

    pad = "p" * 200
    markers = {k: marker(k) for k in ("command", "args", "path", "label")}
    b = B("m")
    t = b.turn("main#0", label="turn")
    b.tool("t0", t, 0, 5, tool_name="Bash", command=pad + markers["command"], args_json=pad + markers["args"],
           file_path=pad + markers["path"])  # fmt: skip
    b.llm("r0", t, 6, 7)
    b.step(t, b.ops[-1].op_id, "say", snippet="s" * 160, summary="u" * 120)
    conn, _ = ingest_one(tmp_path, b)
    # tables the markers could reach: every TEXT-affinity column of every table
    checked = 0
    for table in names(conn, "table"):
        for _, col, typ, *_ in conn.execute(f"PRAGMA table_info({table})").fetchall():
            if "TEXT" not in typ.upper():
                continue
            checked += 1
            for m in markers.values():
                hit = conn.execute(
                    f"SELECT count(*) FROM {table} WHERE instr({col}, ?) > 0", (m,)
                ).fetchone()[0]
                assert hit == 0, (table, col, m)
    assert checked > 40
    assert conn.execute("SELECT count(*) FROM ops WHERE command IS NOT NULL").fetchone()[0] == 1
    # a snippet beyond the 160-character stream cap cannot reach the store: the validator rejects it
    b.steps[0].snippet = "s" * 200 + markers["label"]
    with pytest.raises(StreamDefects):
        put(conn, b)


def test_new_adapter_fields_persist(tmp_path):
    b = B("n", start=0, end=100, entrypoint="cli", originator="codex_exec", title_source="rename")
    human = b.turn("main#0", turn_kind="human", turn_origin="typed")
    harness = b.turn("main#1", turn_kind="harness", turn_origin="builtin:clear")
    c = b.llm("r0", human, 0, 5)
    ask = b.tool("ask", c, 6, 9, tool_name="AskUserQuestion", user_wait="ask")
    plain = b.tool("sh", c, 10, 12, tool_name="Bash", user_wait="permission")
    other = b.tool("rd", c, 13, 14, tool_name="Read")
    conn = store(tmp_path)
    put(conn, b)
    turns = {
        r["op_id"]: (r["turn_kind"], r["turn_origin"])
        for r in conn.execute("SELECT * FROM ops WHERE kind='turn'")
    }
    assert turns == {human: ("human", "typed"), harness: ("harness", "builtin:clear")}
    waits = {r["op_id"]: r["user_wait"] for r in conn.execute("SELECT * FROM ops WHERE kind='tool_call'")}
    assert waits == {ask: "ask", plain: "permission", other: None}
    s = conn.execute("SELECT * FROM sessions").fetchone()
    assert (s["entrypoint"], s["originator"]) == ("cli", "codex_exec")
    # B3a fills the display, attribution, title, bin and bucket columns (test_store_fold_v7), B3b the time
    # split and span columns (test_store_time_v7); only projects waits for `ingest` (DM-60)
    total = conn.execute("SELECT sum(ms) FROM time_split WHERE scope='session'").fetchone()[0]
    assert total == s["wall_ms"]
    assert conn.execute("SELECT count(*) FROM rollup_time").fetchone()[0] > 0
    assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
