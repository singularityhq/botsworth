"""store.ingest: loading bundles from memory, lock, reprice, meta, status, S52 (WAL reader vs writer)."""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from store_builders import B, owners_ok, row, store

from botsworth.pricing import Pricer
from botsworth.store import ingest, load


def simple(sid, start=0, cost=1.0, model="m1", n=2):
    b = B(sid, start=start, end=start + 100)
    t = b.turn("main#0", start=start, end=start + 100)
    for i in range(n):
        b.llm(f"r{i}", t, start + i * 10, start + i * 10 + 5, cost=cost, model=model, occ=f"{sid}:{i}")
    return b


def dangling(sid="bad"):
    """A bundle the store refuses (stream.defects: dangling_ref)."""
    b = simple(sid)
    bundle = b.bundle()
    bundle.ops[-1].parent = "no-such-op"
    return bundle


def test_bundles_load_in_the_order_given_and_a_reload_replaces_rows(tmp_path):
    db = tmp_path / "s.db"
    first = ingest.ingest([simple("a").bundle(), simple("b").bundle()], db=db)
    assert (first.bundles_changed, first.rejected) == (2, [])
    assert [r.bundle for r in first.loaded] == ["claude_code/a", "claude_code/b"]
    assert ingest.ingest(db=db).bundles_changed == 0  # nothing offered, nothing loaded
    bigger = simple("a", n=3)
    again = ingest.ingest([bigger.bundle()], db=db)
    assert [r.bundle for r in again.loaded] == ["claude_code/a"]
    conn = store(tmp_path)
    assert row(conn, bigger.session)["n_llm_calls"] == 3
    assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2


def test_last_loaded_bundle_can_grow_on_reload(tmp_path):
    """Its old rowids are the highest in `ops`; new ops must not collide with them (live sessions grow)."""
    db = tmp_path / "s.db"
    ingest.ingest([simple("a").bundle(), simple("b", start=1000).bundle()], db=db)
    grown = simple("b", start=1000, n=4)
    result = ingest.ingest([grown.bundle()], db=db)
    assert result.rejected == []
    conn = store(tmp_path)
    assert row(conn, grown.session)["n_llm_calls"] == 4
    assert conn.execute("SELECT count(*) FROM ops WHERE rowid IS NOT NULL").fetchone()[0] == len(
        conn.execute("SELECT DISTINCT rowid FROM ops").fetchall()
    )
    owners_ok(conn)


def test_bundles_are_consumed_one_at_a_time(tmp_path):
    db = tmp_path / "s.db"
    seen = []

    def lazy():
        for sid in ("a", "b", "c"):
            reader = sqlite3.connect(db) if db.exists() else None
            seen.append(reader.execute("SELECT count(*) FROM sessions").fetchone()[0] if reader else 0)
            yield simple(sid).bundle()

    ingest.ingest(lazy(), db=db)
    assert seen == [0, 1, 2]  # each bundle was committed before the next one was built


def test_partial_bundle_is_live_and_a_bad_bundle_is_rejected_without_a_file(tmp_path):
    db = tmp_path / "s.db"
    partial = simple("a").bundle()
    partial.n_partial = 1  # the adapter saw a trailing line without a newline
    result = ingest.ingest([partial, dangling()], db=db)
    assert [r["bundle"] for r in result.rejected] == ["claude_code/bad"]
    assert "dangling_ref" in result.rejected[0]["error"]
    conn = store(tmp_path)
    assert conn.execute("SELECT status FROM sessions").fetchone()[0] == "live"
    last = json.loads(conn.execute("SELECT value FROM meta WHERE key='last_run'").fetchone()[0])
    assert last["bundles_changed"] == 2 and len(last["rejected"]) == 1
    assert (
        int(conn.execute("SELECT value FROM meta WHERE key='last_ingest_ms'").fetchone()[0])
        == last["ended_ms"]
    )
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.startswith("s.db")) == []  # D52


def test_one_bundle_twice_in_a_run_second_rejected(tmp_path):
    result = ingest.ingest([simple("a").bundle(), simple("a").bundle()], db=tmp_path / "s.db")
    assert len(result.loaded) == 1 and "already loaded" in result.rejected[0]["error"]


def test_second_concurrent_ingest_is_refused(tmp_path):
    import fcntl

    db = tmp_path / "s.db"
    fd = os.open(f"{db}.lock", os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(ingest.IngestLocked):
            ingest.ingest(db=db)
    finally:
        os.close(fd)
    ingest.ingest(db=db)  # lock released
    with ingest.lock(db):
        with pytest.raises(ingest.IngestLocked):
            ingest.ingest(db=db)
        ingest.ingest(db=db, locked=True)  # the holder may call in


def test_reprice_recosts_computed_and_missing_only(tmp_path):
    db = tmp_path / "s.db"
    old = simple("a", cost=1.0, model="fixture-a")  # computed under an older fingerprint
    for op in old.ops:
        if op.kind == "llm_call":
            op.price_fingerprint = "old"
    unknown = simple("b", start=1000, cost=None, model="nobody")
    omp = B("c", provider="omp", start=2000, end=2100)
    omp.llm("r0", omp.turn("main#0", start=2000, end=2100), 2000, 2010, cost=0.25, cost_basis="reported")
    ingest.ingest([old.bundle(), unknown.bundle(), omp.bundle()], db=db)
    snap = tmp_path / "prices.json"
    rates = {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75}
    snap.write_text(json.dumps({"anthropic": {"models": {"fixture-a": {"cost": rates}}}}))
    pricer = Pricer(snap)
    result = ingest.ingest(db=db, reprice=True, pricer=pricer)
    assert result.repriced == 4  # 2 computed + 2 missing claude calls; omp reported untouched
    conn = store(tmp_path)
    call = row(conn, old.id("llm_call", "r0"))
    assert call["cost_usd"] == pytest.approx((10 * 3 + 20 * 15) / 1e6)
    assert (call["cost_basis"], call["price_fingerprint"]) == ("computed", pricer.fingerprint)
    assert row(conn, old.session)["incl_cost_usd"] == pytest.approx(2 * (10 * 3 + 20 * 15) / 1e6)
    assert row(conn, unknown.session)["cost_lower_bound"] == 1
    assert row(conn, omp.id("llm_call", "r0"))["cost_usd"] == 0.25
    meta = dict(conn.execute("SELECT key, value FROM meta"))
    assert meta["price_fingerprint"] == pricer.fingerprint
    assert ingest.ingest(db=db, reprice=True, pricer=pricer).repriced == 0  # fingerprint unchanged
    owners_ok(conn)


def test_S52_reader_sees_only_committed_bundles_while_writer_runs(tmp_path):
    db = tmp_path / "s.db"
    # each later session repeats the previous one's keys, so loads flip owners and refold other roots
    builders = []
    for i in range(40):
        b = B(f"s{i:02}", start=1000 - i * 10, end=2000)
        t = b.turn("main#0", start=1000 - i * 10, end=2000)
        for k in range(20):
            b.llm(f"r{k}", t, 1000 + k, 1001 + k, cost=0.01 * (k + 1), occ=f"k{(i // 2) * 20 + k}")
        builders.append(b)
    writer = ingest.open_store(db)
    stop = threading.Event()
    stats = {"reads": 0, "bad": 0}

    def reader():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, isolation_level=None)
        while not stop.is_set():
            conn.execute("BEGIN")
            a = conn.execute(
                "SELECT coalesce(sum(incl_cost_usd), 0) FROM ops WHERE kind='session'"
            ).fetchone()[0]
            b = conn.execute(
                "SELECT coalesce(sum(cost_usd), 0) FROM ops WHERE kind='llm_call' AND duplicate_of IS NULL"
            ).fetchone()[0]
            conn.execute("COMMIT")
            stats["reads"] += 1
            stats["bad"] += abs(a - b) > 1e-9
        conn.close()

    th = threading.Thread(target=reader)
    th.start()
    try:  # latest session first: every second load flips the previous owner and refolds its root
        results = [load.load_bundle(writer, b.bundle()) for b in builders]
    finally:
        stop.set()
        th.join()
    assert sum(r.flips for r in results) == 20 * 20
    assert stats["reads"] > 0 and stats["bad"] == 0, stats
    owners_ok(store(tmp_path))


def source_row(path, bundle, status, reject_reason=None, provider="claude_code", ingested_at_ms=1):
    return {
        "path": path,
        "provider": provider,
        "bundle_id": bundle,
        "size": 1,
        "mtime_ns": 1,
        "sha_first_4k": "x",
        "ingested_at_ms": ingested_at_ms,
        "status": status,
        "reject_reason": reject_reason,
    }


def test_status_sources_come_from_source_files_and_last_run(tmp_path):
    db = tmp_path / "s.db"
    assert ingest.status(db) == {"sources": []}  # no store yet
    ingest.ingest([simple("a").bundle(), dangling()], db=db, roots={"claude_code": "/logs/claude"})
    ingest.record_source_files(
        db,
        [
            source_row("/p/m", "a", "live", ingested_at_ms=5),
            source_row("/p/s", "a", "live", ingested_at_ms=9),
            source_row("/p/g", "a", "settled"),
            source_row("/p/x", "bad", "settled", reject_reason="StreamDefects: dangling_ref"),
            source_row("/o/1", "o", "settled", provider="omp"),
        ],
    )
    st = ingest.status(db)
    by_provider = {s["provider"]: s for s in st["sources"]}
    assert by_provider["claude_code"] == {
        "provider": "claude_code",
        "root": "/logs/claude",
        "files": 4,
        "bundles": 2,
        "live": 2,
        "last_ingest_ms": 9,
        "rejected": 1,
    }
    assert (by_provider["omp"]["root"], by_provider["omp"]["files"]) == ("", 1)  # no root recorded for it
    assert set(st["last_run"]) == {"started_ms", "ended_ms", "bundles_changed", "rejected"}


def test_db_from_environment(tmp_path, monkeypatch):
    db = tmp_path / "env.db"
    omp = B("o", provider="omp")
    omp.llm("r0", omp.turn("main#0"), 0, 5, cost=0.1, cost_basis="reported")
    monkeypatch.setenv("BOTSWORTH_DB", str(db))
    r = ingest.ingest([omp.bundle()])
    assert [x.bundle for x in r.loaded] == ["omp/o"] and db.exists()
    assert r.to_dict()["loaded"] == 1


def test_run_writes_price_fingerprint_and_parser_version(tmp_path):
    db = tmp_path / "s.db"
    ingest.ingest([simple("a").bundle()], db=db, parser_version="pv-1")
    conn = sqlite3.connect(db)
    meta = dict(
        conn.execute("SELECT key, value FROM meta WHERE key IN ('price_fingerprint','parser_version')")
    )
    assert meta == {"price_fingerprint": Pricer().fingerprint, "parser_version": "pv-1"}


def test_source_roots_merge_across_runs(tmp_path):
    db = tmp_path / "s.db"
    ingest.ingest(db=db, roots={"codex": "/c"})
    ingest.ingest(db=db, roots={"omp": "/o"})
    conn = sqlite3.connect(db)
    assert json.loads(conn.execute("SELECT value FROM meta WHERE key='source_roots'").fetchone()[0]) == {
        "codex": "/c",
        "omp": "/o",
    }


def test_lock_file_holds_pid_while_running_and_is_truncated_after(tmp_path):
    db = tmp_path / "s.db"
    lock_file = Path(f"{db}.lock")
    before = int(time.time() * 1000)
    with ingest.lock(db):
        payload = json.loads(lock_file.read_text())
        assert set(payload) == {"pid", "started_ms"}
        assert payload["pid"] == os.getpid() and payload["started_ms"] >= before
        with pytest.raises(ingest.IngestLocked):
            ingest.ingest(db=db)
        assert json.loads(lock_file.read_text())["pid"] == os.getpid()  # a refused call leaves it alone
    assert lock_file.stat().st_size == 0
