"""store.refs and compact reference storage: DM-6, DM-8 (D39, D42, D43)."""

import pytest
from store_builders import B, put, store
from stream_load import load_streams

from botsworth import pointer, stream
from botsworth.model import SourceFile
from botsworth.store import ingest, refs

HASH = "0123456789abcdef"
URIS = {
    "lines": "src://claude_code/proj/s.jsonl#L3-L9",
    "json_path": "src://claude_code/proj/s.jsonl#L3-L3/message.content.1.input",
    "hash": f"src://claude_code/proj/s.jsonl#L3-L9?h={HASH}",
    "both": f"src://claude_code/proj/s.jsonl#L12-L12/message.content.0?h={HASH}",
    "bytes": "src://codex/sessions/2026/10/12/rollout-x.jsonl#B120+300",
    "odd_relpath": pointer.format("omp", "dir with space/a#b?c.jsonl", pointer.Lines(1, 2)),
}


def bundle_with_every_uri():
    """One op, one step and one event per URI; returns (builder, {record id: stream uri})."""
    b = B("r")
    t = b.turn("main#0")
    expected = {}
    for i, uri in enumerate(URIS.values()):
        call = b.llm(f"c{i}", t, i, i + 1)
        next(o for o in b.ops if o.op_id == call).src = uri
        b.step(t, call, "say")
        b.steps[-1].src = uri
        b.event("compaction", op_id=call, ts=i)
        b.events[-1].src = uri
        expected[call] = uri
    return b, expected


def stored_parts(conn, table, where="", args=()):
    rows = conn.execute(
        f"SELECT t.rowid, f.prefix, t.src_loc, t.src_h FROM {table} t"
        f" JOIN src_files f ON f.rowid = t.src_file {where}",
        args,
    )
    return {r["rowid"]: refs.uri(r["prefix"], r["src_loc"], r["src_h"]) for r in rows}


def test_uri_round_trip_every_record(tmp_path):
    b, expected = bundle_with_every_uri()
    conn = store(tmp_path)
    put(conn, b)
    ops = {o.op_id: o.src for o in b.ops}
    rebuilt = stored_parts(conn, "ops")
    by_id = dict(conn.execute("SELECT rowid, op_id FROM ops"))
    assert {by_id[r]: u for r, u in rebuilt.items()} == ops
    assert sorted(stored_parts(conn, "steps").values()) == sorted(s.src for s in b.steps)
    assert sorted(stored_parts(conn, "events").values()) == sorted(e.src for e in b.events)
    for uri in [*ops.values(), *(s.src for s in b.steps), *(e.src for e in b.events)]:
        pointer.parse(uri)  # the grammar accepts every rebuilt reference
    assert set(expected.values()) <= set(rebuilt.values())


def test_no_hash_stores_null(tmp_path):
    conn = store(tmp_path)
    b = B("h")
    t = b.turn("main#0")
    plain = b.llm("plain", t, 0, 1)
    hashed = b.llm("hashed", t, 1, 2)
    for o in b.ops:
        if o.op_id == plain:
            o.src = URIS["lines"]
        if o.op_id == hashed:
            o.src = URIS["hash"]
    put(conn, b)
    rows = {r[0]: r[1:] for r in conn.execute("SELECT op_id, src_loc, src_h FROM ops")}
    assert rows[plain] == ("L3-L9", None)
    assert rows[hashed] == ("L3-L9", bytes.fromhex(HASH)) and len(rows[hashed][1]) == 8
    assert conn.execute("SELECT src FROM ops_v WHERE op_id=?", (plain,)).fetchone()[0] == URIS["lines"]
    assert "?h=" not in conn.execute("SELECT src FROM ops_v WHERE op_id=?", (plain,)).fetchone()[0]


def test_relpath_with_hash_question_space(tmp_path):
    uri = URIS["odd_relpath"]
    provider, relpath, prefix, loc, h = refs.to_parts(uri)
    assert (provider, relpath, loc, h) == ("omp", "dir with space/a#b?c.jsonl", "L1-L2", None)
    assert refs.uri(prefix, loc, h) == uri
    conn = store(tmp_path)
    b = B("odd")
    t = b.turn("main#0")
    call = b.llm("c", t, 0, 1)
    next(o for o in b.ops if o.op_id == call).src = uri
    put(conn, b)
    assert conn.execute("SELECT src FROM ops_v WHERE op_id=?", (call,)).fetchone()[0] == uri
    assert conn.execute("SELECT relpath FROM src_files WHERE provider='omp'").fetchone()[0] == relpath


def test_src_files_rowid_stable_on_reload(tmp_path):
    conn = store(tmp_path)
    b, _ = bundle_with_every_uri()
    put(conn, b)
    before = conn.execute("SELECT rowid, provider, relpath, prefix FROM src_files ORDER BY rowid").fetchall()
    other = B("other")  # a second bundle interns one more file; the first rows keep their ids
    other.llm("c", other.turn("main#0"), 0, 1)
    put(conn, other, b)
    after = conn.execute("SELECT rowid, provider, relpath, prefix FROM src_files ORDER BY rowid").fetchall()
    assert [tuple(r) for r in after[: len(before)]] == [tuple(r) for r in before]
    assert len(after) == len(before) + 1
    assert (
        conn.execute(
            "SELECT count(*) FROM ops o LEFT JOIN src_files f ON f.rowid=o.src_file WHERE f.rowid IS NULL"
        ).fetchone()[0]
        == 0
    )


def test_src_sql_equals_python_uri(tmp_path):
    conn = store(tmp_path)
    b, _ = bundle_with_every_uri()
    put(conn, b)
    for table in ("ops", "steps", "events"):
        sql = (
            f"SELECT t.rowid, {refs.src_sql('t', 'sf')} FROM {table} t"
            " JOIN src_files sf ON sf.rowid = t.src_file"
        )
        in_sql = dict(conn.execute(sql).fetchall())
        assert in_sql == stored_parts(conn, table)
    assert refs.SRC_SQL.format(t="x", f="y").count("x.src_loc") == 1


def test_to_parts_rejects_bad_input():
    with pytest.raises(pointer.PointerError):
        refs.to_parts("src://x#L1")
    with pytest.raises(ValueError):
        refs.to_parts("src://claude_code/a.jsonl#L1-L1?h=abc")  # h is 16 hex digits


def test_clip_preview_and_intern_helpers(tmp_path):
    assert refs.clip_preview(None) is None
    assert refs.clip_preview("a" * 200) == "a" * 160
    assert refs.clip_preview("ab  " + "x" * 200, 4) == "ab"  # cut at the cap, then strip trailing whitespace
    assert len(refs.occ_hash("k")) == 8 and refs.occ_hash("k") != refs.occ_hash("k2")
    conn = store(tmp_path)
    assert refs.intern_name(conn, None) is None
    assert refs.intern_name(conn, "/x") == refs.intern_name(conn, "/x") != refs.intern_name(conn, "/y")


def test_stream_ingest_registers_source_files(tmp_path):
    """D42 item 2: a --stream ingest of a bundle with source_file records writes source_files rows."""
    b = B("sf")
    b.llm("c", b.turn("main#0"), 0, 1)
    bundle = b.bundle()
    bundle.source_files = [
        SourceFile(provider="claude_code", path="/x/proj/sf.jsonl", size=10, mtime_ns=5, sha_first_4k="aa"),
        SourceFile(provider="claude_code", path="/x/proj/sf/subagents/a.jsonl", size=3, mtime_ns=6),
    ]
    d, db = tmp_path / "streams", tmp_path / "s.db"
    stream.write_bundle(bundle, d)
    assert load_streams(db=db, streams=[d]).rejected == []
    conn = ingest.open_store(db)
    rows = conn.execute("SELECT path, bundle_id, size, status FROM source_files ORDER BY path").fetchall()
    assert [tuple(r) for r in rows] == [
        ("/x/proj/sf.jsonl", "claude_code/sf", 10, "settled"),
        ("/x/proj/sf/subagents/a.jsonl", "claude_code/sf", 3, "settled"),
    ]
    assert ingest.source_files(db)["/x/proj/sf.jsonl"]["sha_first_4k"] == "aa"


def test_legacy_op_wire_src_is_uri(tmp_path):
    """D42 item 3: query, show and op_wire still return `src` as the URI."""
    import botsworth

    b, expected = bundle_with_every_uri()
    d, db = tmp_path / "streams", tmp_path / "s.db"
    stream.write_bundle(b.bundle(), d)
    load_streams(db=db, streams=[d])
    for op_id, uri in expected.items():
        shown = botsworth.show(op_id, db=db)
        assert shown["op"]["src"] == uri
        assert [e["src"] for e in shown["events"]] == [uri]
    got = botsworth.query(
        {"scope": {"op": b.session}, "select": "descendants", "where": [{"field": "kind", "eq": "llm_call"}],
         "metrics": ["cost_usd"], "limit": 50},
        db=db,
    )  # fmt: skip
    assert {r["op"]["op_id"]: r["op"]["src"] for r in got["rows"]} == expected
    listed = botsworth.steps("claude_code/r/turn/main#0", db=db)["steps"]
    assert sorted(s["src"] for s in listed) == sorted(expected.values())
