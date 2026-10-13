"""pipeline.run: change detection over provider files, source_missing, rejected bundles, provider filter."""

import os
import shutil
import sqlite3
import time
from pathlib import Path

import pytest
from store_builders import B

from botsworth import adapters, pipeline
from botsworth.adapters import BundleRef, BundleRejected
from botsworth.pricing import Pricer
from botsworth.store import ingest

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture(autouse=True)
def no_mtime_live(monkeypatch):
    """Keep the mtime rule out (files appended to are fresh) unless a test is about it."""
    monkeypatch.setattr(pipeline, "LIVE_WINDOW_MS", -3_600_000)


def claude_tree(tmp_path):
    root = tmp_path / "claude"
    shutil.copytree(FIXTURES / "F2" / "project", root / "project")
    return root


def run(tmp_path, root, **kw):
    return pipeline.run(["claude_code"], {"claude_code": root}, db=tmp_path / "s.db", **kw)


def source_rows(tmp_path):
    conn = sqlite3.connect(tmp_path / "s.db")
    try:
        return {
            Path(p).name: (b, s)
            for p, b, s in conn.execute("SELECT path, bundle_id, status FROM source_files")
        }
    finally:
        conn.close()


def test_unchanged_append_delete(tmp_path):
    root = claude_tree(tmp_path)
    first = run(tmp_path, root)
    rep = first["providers"]["claude_code"]
    assert (rep["discovered"], rep["rebuilt"], rep["rejected"]) == (1, 1, [])
    assert first["store"]["loaded"] == 1
    assert not (tmp_path / "streams").exists()  # D52: nothing is written beside the store
    assert source_rows(tmp_path) == {
        "s1.jsonl": ("claude_code/s1", "settled"),
        "agent-a.jsonl": ("claude_code/s1", "settled"),
        "agent-a.meta.json": ("claude_code/s1", "settled"),  # adapter-recorded input, checked too
    }

    again = run(tmp_path, root)
    assert (again["providers"]["claude_code"]["rebuilt"], again["providers"]["claude_code"]["unchanged"]) == (
        0,
        1,
    )
    assert again["store"]["bundles_changed"] == 0
    conn = sqlite3.connect(tmp_path / "s.db")
    meta = dict(
        conn.execute("SELECT key, value FROM meta WHERE key IN ('parser_version','price_fingerprint')")
    )
    conn.close()
    assert meta == {"parser_version": pipeline.PARSER_VERSION, "price_fingerprint": Pricer().fingerprint}

    main = root / "project" / "s1.jsonl"
    with main.open("a") as f:
        f.write(main.read_text().splitlines()[0] + "\n")
    grown = run(tmp_path, root)
    assert grown["providers"]["claude_code"]["rebuilt"] == 1
    assert grown["store"]["bundles_changed"] == 1

    (root / "project" / "s1" / "subagents" / "agent-a.meta.json").write_text("{}")
    assert (
        run(tmp_path, root)["providers"]["claude_code"]["rebuilt"] == 1
    )  # a non-member recorded file changed

    (root / "project" / "s1" / "subagents" / "agent-a.jsonl").unlink()
    shrunk = run(tmp_path, root)
    assert shrunk["providers"]["claude_code"]["rebuilt"] == 1  # a recorded member vanished: rebuild
    assert source_rows(tmp_path)["agent-a.jsonl"] == ("claude_code/s1", "source_missing")

    main.unlink()
    gone = run(tmp_path, root)
    assert gone["providers"]["claude_code"]["discovered"] == 0
    assert source_rows(tmp_path)["s1.jsonl"] == ("claude_code/s1", "source_missing")


def test_force_rebuilds_unchanged(tmp_path):
    root = claude_tree(tmp_path)
    run(tmp_path, root)
    assert run(tmp_path, root, force=True)["providers"]["claude_code"]["rebuilt"] == 1


class FakeAdapter:
    """One bundle per `*.txt` file; a file containing `reject` / `boom` fails the build."""

    provider = "codex"

    def discover(self, root):
        return [BundleRef(self.provider, p.stem, p.stem, (p,)) for p in sorted(Path(root).glob("*.txt"))]

    def build(self, ref, pricer):
        text = ref.files[0].read_text()
        if "reject" in text:
            raise BundleRejected("cycle", ["x"])
        if "boom" in text:
            raise RuntimeError("adapter bug")
        b = B(ref.session_id, provider=self.provider)
        b.llm("r0", b.turn("main#0"), 0, 5, cost=1.0, occ=f"{ref.session_id}:0")
        return b.bundle()


@pytest.fixture
def fake_codex(monkeypatch):
    real = adapters.get
    monkeypatch.setattr(adapters, "get", lambda p: FakeAdapter() if p == "codex" else real(p))


def test_rejected_bundle_reported_and_run_continues(tmp_path, fake_codex):
    root = tmp_path / "codex"
    root.mkdir()
    (root / "a.txt").write_text("ok")
    (root / "b.txt").write_text("reject")
    (root / "c.txt").write_text("boom")
    r = pipeline.run(["codex"], {"codex": root}, db=tmp_path / "s.db")
    rep = r["providers"]["codex"]
    assert (rep["discovered"], rep["rebuilt"]) == (3, 1)
    assert {x["bundle_id"]: x["reason"].split(":")[0] for x in rep["rejected"]} == {
        "b": "BundleRejected",
        "c": "RuntimeError",
    }
    assert r["store"]["loaded"] == 1
    # unchanged rejected files are not rebuilt; fixing one rebuilds just that one
    assert (
        pipeline.run(["codex"], {"codex": root}, db=tmp_path / "s.db")["providers"]["codex"]["rebuilt"] == 0
    )
    (root / "c.txt").write_text("fixed")
    again = pipeline.run(["codex"], {"codex": root}, db=tmp_path / "s.db")["providers"]["codex"]
    assert (again["rebuilt"], again["rejected"]) == (1, [])


def test_providers_filter_and_missing_root(tmp_path, fake_codex):
    croot = claude_tree(tmp_path)
    xroot = tmp_path / "codex"
    xroot.mkdir()
    (xroot / "a.txt").write_text("ok")
    roots = {"claude_code": croot, "codex": xroot, "omp": tmp_path / "nope"}
    only = pipeline.run(["codex"], roots, db=tmp_path / "s.db")
    assert set(only["providers"]) == {"codex"}
    assert source_rows(tmp_path) == {"a.txt": ("codex/a", "settled")}

    every = pipeline.run(None, roots, db=tmp_path / "s.db")
    assert every["providers"]["omp"]["skipped"] == "root missing"
    assert (every["providers"]["claude_code"]["rebuilt"], every["providers"]["codex"]["rebuilt"]) == (1, 0)
    assert every["store"]["loaded"] == 1


def touch(root, t):
    for f in root.rglob("*"):
        if f.is_file():
            os.utime(f, (t, t))


def test_live_by_mtime_window_then_settled(tmp_path, monkeypatch):
    root = claude_tree(tmp_path)
    monkeypatch.setattr(pipeline, "LIVE_WINDOW_MS", 600_000)
    touch(root, time.time() - 60)
    run(tmp_path, root)
    assert {s for _, s in source_rows(tmp_path).values()} == {"live"}  # modified 1 min ago
    touch(root, time.time() - 3600)
    r = run(tmp_path, root)  # mtime changed: rebuilt, now outside the window
    assert r["providers"]["claude_code"]["rebuilt"] == 1
    assert {s for _, s in source_rows(tmp_path).values()} == {"settled"}
    monkeypatch.setattr(pipeline, "LIVE_WINDOW_MS", 2 * 3600 * 1000)
    r = run(tmp_path, root)  # unchanged bundle, re-evaluated by the mtime rule
    assert r["providers"]["claude_code"]["unchanged"] == 1
    assert {s for _, s in source_rows(tmp_path).values()} == {"live"}


def test_source_missing_survives_a_later_reload(tmp_path):
    root = claude_tree(tmp_path)
    run(tmp_path, root)
    (root / "project" / "s1" / "subagents" / "agent-a.jsonl").unlink()
    run(tmp_path, root)
    main = root / "project" / "s1.jsonl"
    with main.open("a") as f:
        f.write(main.read_text().splitlines()[0] + "\n")
    assert run(tmp_path, root)["store"]["loaded"] == 1  # the reload deletes the bundle's rows
    assert source_rows(tmp_path)["agent-a.jsonl"] == ("claude_code/s1", "source_missing")


def test_concurrent_run_is_refused(tmp_path):
    root = claude_tree(tmp_path)
    with ingest.lock(tmp_path / "s.db"):
        with pytest.raises(ingest.IngestLocked):
            run(tmp_path, root)
    assert run(tmp_path, root)["providers"]["claude_code"]["rebuilt"] == 1


def test_first_run_rebuilds_every_bundle(tmp_path):
    root = claude_tree(tmp_path)
    first = run(tmp_path, root)
    rep = first["providers"]["claude_code"]
    assert first["mode"] == "rebuild"
    assert rep["rebuilt"] == rep["discovered"] == 1 and rep["unchanged"] == 0
    assert run(tmp_path, root)["mode"] == "incremental"


def test_parser_version_2(tmp_path):
    run(tmp_path, claude_tree(tmp_path))
    conn = sqlite3.connect(tmp_path / "s.db")
    try:
        assert conn.execute("SELECT value FROM meta WHERE key='parser_version'").fetchone() == ("2",)
    finally:
        conn.close()
    assert pipeline.PARSER_VERSION == "2"


def test_status_on_old_version_store(tmp_path):
    root = claude_tree(tmp_path)
    run(tmp_path, root)
    assert ingest.status(tmp_path / "s.db")["sources"]
    conn = sqlite3.connect(tmp_path / "s.db")
    conn.execute("UPDATE meta SET value='0' WHERE key='schema_version'")  # make the store look old
    conn.commit()
    conn.close()
    assert ingest.status(tmp_path / "s.db") == {"sources": []}


# ── D52: no streams folder; the store is a disposable cache over the logs ───────────────────────────


def store_files(folder):
    return sorted(p.name for p in folder.iterdir())


def test_ingest_leaves_only_the_store_files(tmp_path):
    root = claude_tree(tmp_path)
    data = tmp_path / "data"
    for _ in range(2):  # a rebuild run, then an incremental run
        pipeline.run(["claude_code"], {"claude_code": root}, db=data / "s.db")
        names = store_files(data)
        assert "s.db" in names
        assert all(n.startswith("s.db") for n in names), names  # s.db, s.db.lock, s.db-wal, s.db-shm
        assert not any(p.is_dir() for p in data.iterdir())
    assert not list(data.glob("*.rebuild*"))


def test_unchanged_logs_parse_nothing(tmp_path, fake_codex, monkeypatch):
    root = tmp_path / "codex"
    root.mkdir()
    (root / "a.txt").write_text("ok")
    built = []
    real_build = FakeAdapter.build
    monkeypatch.setattr(
        FakeAdapter, "build", lambda self, ref, pricer: built.append(ref) or real_build(self, ref, pricer)
    )
    pipeline.run(["codex"], {"codex": root}, db=tmp_path / "s.db")
    assert len(built) == 1
    again = pipeline.run(["codex"], {"codex": root}, db=tmp_path / "s.db")
    assert len(built) == 1 and again["store"]["bundles_changed"] == 0


def test_bundle_the_store_rejects_is_not_retried_until_its_file_changes(tmp_path, monkeypatch):
    class Invalid(FakeAdapter):
        def build(self, ref, pricer):
            bundle = super().build(ref, pricer)
            if "dangling" in ref.files[0].read_text():
                bundle.ops[
                    -1
                ].parent = "no-such-op"  # a defect the store refuses (stream.defects: dangling_ref)
            return bundle

    monkeypatch.setattr(adapters, "get", lambda p: Invalid())
    root = tmp_path / "codex"
    root.mkdir()
    (root / "a.txt").write_text("dangling")
    db = tmp_path / "s.db"
    first = pipeline.run(["codex"], {"codex": root}, db=db)
    assert [r["bundle"] for r in first["store"]["rejected"]] == ["codex/a"]
    assert first["store"]["loaded"] == 0
    assert ingest.status(db)["sources"][0]["rejected"] == 1
    again = pipeline.run(["codex"], {"codex": root}, db=db)
    assert (again["providers"]["codex"]["rebuilt"], again["store"]["bundles_changed"]) == (0, 0)
    (root / "a.txt").write_text("ok now")
    fixed = pipeline.run(["codex"], {"codex": root}, db=db)
    assert (fixed["store"]["loaded"], fixed["store"]["rejected"]) == (1, [])
    assert ingest.status(db)["sources"][0]["rejected"] == 0


def test_rebuild_replaces_the_store_and_drops_rows_of_deleted_logs(tmp_path):
    root = claude_tree(tmp_path)
    data = tmp_path / "data"
    db = data / "s.db"
    kw = {"db": db}
    pipeline.run(["claude_code"], {"claude_code": root}, **kw)
    shutil.rmtree(root)  # the logs are gone
    root.mkdir()
    gone = pipeline.run(["claude_code"], {"claude_code": root}, **kw)
    assert gone["mode"] == "incremental"
    assert {s for _, s in source_rows(data).values()} == {"source_missing"}  # rows stay (D52)
    rebuilt = pipeline.run(["claude_code"], {"claude_code": root}, rebuild=True, **kw)
    assert rebuilt["mode"] == "rebuild"
    assert source_rows(data) == {}
    assert store_files(data) == sorted(n for n in store_files(data) if n.startswith("s.db"))
    assert not list(data.glob("*.rebuild*"))


def test_failed_rebuild_keeps_the_old_store_and_leaves_no_temp_file(tmp_path, monkeypatch):
    root = claude_tree(tmp_path)
    db = tmp_path / "data" / "s.db"
    pipeline.run(["claude_code"], {"claude_code": root}, db=db)
    before = db.read_bytes()

    def boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ingest, "record_source_files", boom)  # fails after the temp store was filled
    with pytest.raises(RuntimeError, match="disk full"):
        pipeline.run(["claude_code"], {"claude_code": root}, rebuild=True, db=db)
    assert db.read_bytes() == before
    assert not list(db.parent.glob("*.rebuild*"))


# ── load order: oldest session first, whatever order the adapter lists bundles in ──────────────────


class StampedAdapter:
    """One bundle per `*.jsonl`; the first line carries the timestamp; every bundle repeats one llm_call
    (same occ key), so whichever session is older owns it. `order` is the listing order."""

    provider = "codex"
    order: list[str] = []
    starts = {"old": 1_000, "mid": 2_000, "new": 3_000}

    def discover(self, root):
        return [BundleRef(self.provider, n, n, (Path(root) / f"{n}.jsonl",)) for n in self.order]

    def build(self, ref, pricer):
        start = self.starts[ref.bundle_id]
        b = B(ref.session_id, provider=self.provider, start=start, end=start + 100)
        b.llm("r0", b.turn("main#0", start=start, end=start + 100), start, start + 5, cost=1.0, occ="shared")
        return b.bundle()


def stamped_root(tmp_path):
    root = tmp_path / "codex"
    root.mkdir()
    for name, start in StampedAdapter.starts.items():
        (root / f"{name}.jsonl").write_text(f'{{"timestamp": {start}}}\n')
    return root


@pytest.mark.parametrize("listing", [["old", "mid", "new"], ["new", "mid", "old"], ["mid", "new", "old"]])
def test_load_order_is_oldest_first_whatever_the_listing_order(tmp_path, monkeypatch, listing):
    monkeypatch.setattr(StampedAdapter, "order", listing)
    monkeypatch.setattr(adapters, "get", lambda p: StampedAdapter())
    root = stamped_root(tmp_path)
    for mode in ("rebuild", "incremental"):  # rebuild and incremental agree
        r = pipeline.run(["codex"], {"codex": root}, db=tmp_path / "s.db", force=mode == "incremental")
        assert r["store"]["ownership_flips"] == 0  # the oldest loads first, so nothing is ever taken over
    conn = sqlite3.connect(tmp_path / "s.db")
    owners = {
        o: b
        for o, b in conn.execute(
            "SELECT s.bundle, o.duplicate_of IS NULL FROM ops o JOIN sessions s ON s.rowid = o.root_rowid"
            " WHERE o.kind = 'llm_call'"
        )
    }
    assert owners == {"codex/old": 1, "codex/mid": 0, "codex/new": 0}


def test_session_start_ms_reads_the_main_file_only_as_far_as_it_needs(tmp_path):
    def ref(text):
        f = tmp_path / "m.jsonl"
        f.write_text(text)
        return BundleRef("x", "m", "m", (f,))

    assert (
        adapters.session_start_ms(ref('not json\n{"timestamp": "2026-10-01T10:00:00.000Z"}\n'))
        == 1790848800000
    )
    assert adapters.session_start_ms(ref('{"a": 1}\n{"timestamp": 5}\n')) == 5
    assert adapters.session_start_ms(ref('{"a": 1}\n')) is None  # sorts last in the pipeline
    assert adapters.session_start_ms(BundleRef("x", "m", "m", (tmp_path / "gone.jsonl",))) is None
