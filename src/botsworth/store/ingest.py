"""CONTRACT: load canonical bundles into the store and keep its manifest (design §8, D7, D52).

GUARANTEES:
- Runs only when called (`botsworth ingest`); nothing in the query path calls it.
- `ingest(bundles, reprice, *, db, pricer)` opens the store at `location.resolve_db(db)` (parent dir
  created) and loads each bundle of the iterable `bundles`, in the order given, one at a time (a generator
  is consumed lazily, so only one bundle is in memory). Bundles come from the adapters in memory; nothing
  is written beside the store (D52). Each bundle loads in its own transaction via `store.load`; a rejected
  bundle keeps its previous rows and is listed in `IngestResult.rejected` as `{bundle, error}`. A second
  bundle with the same key (`provider/bundle_id`) in one run is rejected. Which bundles to pass (change
  detection) is the caller's job; the manifest is `source_files`.
- `reprice=True` re-costs every `llm_call` with `cost_basis` computed|missing
  whose `price_fingerprint` differs from the Pricer's, without reading provider files, then re-folds the
  affected bundles; `meta.price_fingerprint` is set to the Pricer's.
- At the end of a run: `meta.last_ingest_ms` = end time,
  `meta.n_ownership_disagreements` = adapter `duplicate_of` hints that differed
  from the store's decision in this run, `meta.last_run` = JSON
  `{started_ms, ended_ms, bundles_changed, rejected}`,
  `meta.price_fingerprint` = the Pricer's fingerprint (the Pricer new bundles
  were priced with; rows from earlier runs keep their own `ops.price_fingerprint`
  until `reprice`), `meta.parser_version` = the `parser_version` argument
  when given, `meta.source_roots` = JSON `{provider: root}` merged with the `roots` argument.
  The store holds no zone: there is no `meta.tz` and no zone re-fold (DM-41, DM-43).
  `ANALYZE` runs when the run changed a bundle or repriced (DM-5).
- `projects` is rewritten by `projects.refresh` after the loads and overrides of every run, also a run that
  changed nothing (DM-60, DM-61).
- `overrides` (`{provider: {session_id: name}}`, DM-12): after the loads, `apply_overrides` sets a stored
  session's `title`, `title_source = rename` and `display_title` to the name when they differ, without
  rebuilding the bundle; the count is `IngestResult.overrides_applied`.
  `IngestResult.to_dict()` is the CLI's return payload.
- Rebuild by swap (DM-4, DM-16, D52): `needs_rebuild(db)` is true when the store is
  missing or its `meta.schema_version` differs from `schema.SCHEMA_VERSION`; it
  opens the store read-only. The caller (the pipeline) then takes
  `rebuild_target(db)` (= `<db>.rebuild`, a temp file beside the store, leftovers removed), ingests into
  it, and calls `finish_rebuild(db)`: `ANALYZE`, `VACUUM`, `journal_mode=DELETE`,
  close, `os.replace` onto `<db>`, then `<db>-wal`/`<db>-shm` removed. The pipeline also takes this path
  for `botsworth ingest --rebuild`, on a current store.
  `discard_rebuild(db)` removes `<db>.rebuild*`; `<db>` is not touched by it.
- While `lock(db)` is held, `<db>.lock` holds `{"pid", "started_ms"}` (QRY-40);
  it is truncated to 0 bytes before the lock is released.
- `status(db)` is read-only: `{sources: [{provider, root, files, bundles, live,
  last_ingest_ms, rejected}], last_run?}`: one entry per provider in `source_files` and `meta`;
  `files` = that provider's `source_files` rows (provider source files, any status), `live` = those with
  status `live`, `bundles` = distinct `bundle_id`, `rejected` = distinct bundles with a `reject_reason`,
  `last_ingest_ms` = latest `ingested_at_ms`, `root` = `meta.source_roots[provider]`
  (types.ts IngestStatus minus `freshness`); `{sources: []}` when no store.
- At most one ingest per store at a time: an exclusive lock on `<db>.lock`;
  a second concurrent call raises `IngestLocked`. `lock(db)` is that lock as a
  context manager for a caller (the ingest pipeline) that must hold it across
  more than one store call; inside it, call `ingest(..., locked=True)`.
- `source_files(db)` returns `{path: row}` from `source_files` (row keys
  provider, bundle_id, size, mtime_ns, sha_first_4k, status, reject_reason); `{}` when no
  store or a store of another schema version (opened read-only, never
  rewritten). `record_source_files(db, rows)` upserts `rows` (keys = the
  `source_files` columns path, provider, bundle_id, size, mtime_ns,
  sha_first_4k, ingested_at_ms, status, reject_reason) in one transaction.
- `open_store(db)` returns a connection with the current schema (WAL,
  autocommit mode; `store.load` opens its own transactions).
EXPECTS: bundles that the adapters built (`botsworth.model.Bundle`).
FAILURE BEHAVIOR: a failed rebuild is discarded by the caller (`discard_rebuild`);
  `<db>` stays as it was. An invalid bundle (`StreamDefects`, `BundleInvalid`) -> that bundle rejected,
  run continues.
DOES NOT: run adapters or read provider files; answer queries; choose rebuild or
  in-place mode (the pipeline does); remove rows of a bundle whose provider files are gone (D52: they stay,
  `source_files` marks them `source_missing`, until a rebuild drops them).
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from botsworth import stream
from botsworth.model import Bundle
from botsworth.pricing import Pricer
from botsworth.store import load, location, projects, refs, schema


class IngestLocked(RuntimeError):
    """Another ingest holds the store lock."""


@dataclass(slots=True)
class IngestResult:
    started_ms: int
    ended_ms: int = 0
    bundles_changed: int = 0
    loaded: list[load.LoadResult] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    repriced: int = 0
    overrides_applied: int = 0

    @property
    def flips(self) -> int:
        return sum(r.flips for r in self.loaded)

    @property
    def disagreements(self) -> int:
        return sum(r.disagreements for r in self.loaded)

    def to_dict(self) -> dict:
        return {
            "started_ms": self.started_ms,
            "ended_ms": self.ended_ms,
            "bundles_changed": self.bundles_changed,
            "loaded": len(self.loaded),
            "rejected": self.rejected,
            "ownership_flips": self.flips,
            "ownership_disagreements": self.disagreements,
            "missing_occ_key": sum(r.missing_occ_key for r in self.loaded),
            "repriced": self.repriced,
            "overrides_applied": self.overrides_applied,
        }


def open_store(db: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db, isolation_level=None)
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    schema.ensure(conn)
    return conn


def _lock(db: Path) -> int:
    fd = os.open(f"{db}.lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise IngestLocked(f"another ingest holds {db}.lock") from exc
    payload = json.dumps({"pid": os.getpid(), "started_ms": int(time.time() * 1000)}).encode()
    os.ftruncate(fd, 0)
    os.write(fd, payload)  # QRY-40: unbuffered, so a reader sees it as soon as the lock is held
    return fd


def ingest(
    bundles: Iterable[Bundle] = (),
    reprice: bool = False,
    *,
    db: Path | str | None = None,
    pricer: Pricer | None = None,
    parser_version: str | None = None,
    locked: bool = False,
    overrides: Mapping[str, Mapping[str, str]] | None = None,
    roots: Mapping[str, str] | None = None,
) -> IngestResult:
    """Load `bundles` (consumed lazily, in order) into the store.

    `locked=True`: the caller already holds `lock(db)`. `roots` = `{provider: root}` of the scanned providers.
    `overrides` = `{provider: {session_id: name}}` renames from outside the bundles (DM-12).
    """
    path = location.resolve_db(db)
    with nullcontext() if locked else lock(path):
        conn = open_store(path)
        try:
            return _run(
                conn, bundles, reprice, pricer or Pricer(), parser_version, overrides or {}, roots or {}
            )
        finally:
            conn.close()


@contextmanager
def lock(db: Path | str | None = None) -> Iterator[None]:
    """Hold the store's exclusive ingest lock (creates the db's parent dir); `IngestLocked` if taken."""
    path = location.resolve_db(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _lock(path)
    try:
        yield
    finally:
        os.ftruncate(fd, 0)  # QRY-40: not running any more
        os.close(fd)  # closing the descriptor releases the flock


def store_version(db: Path | str | None = None) -> int | None:
    """`meta.schema_version` of the store at `db`, read-only; None when no store or no version."""
    path = location.resolve_db(db)
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return schema._version(conn)
    finally:
        conn.close()


def needs_rebuild(db: Path | str | None = None) -> bool:
    """True when the store is missing or not of `schema.SCHEMA_VERSION` (DM-4)."""
    return store_version(db) != schema.SCHEMA_VERSION


def _rebuild_files(db: Path) -> list[Path]:
    base = f"{db}.rebuild"
    return [Path(base), Path(f"{base}-wal"), Path(f"{base}-shm"), Path(f"{base}-journal")]


def discard_rebuild(db: Path | str | None = None) -> None:
    """Delete `<db>.rebuild` and its sidecar files."""
    for f in _rebuild_files(location.resolve_db(db)):
        f.unlink(missing_ok=True)


def rebuild_target(db: Path | str | None = None) -> Path:
    """Path of the new store for a rebuild of `db`; a leftover from an earlier run is removed first."""
    path = location.resolve_db(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    discard_rebuild(path)
    return Path(f"{path}.rebuild")


def finish_rebuild(db: Path | str | None = None) -> None:
    """ANALYZE, VACUUM and un-WAL `<db>.rebuild`, then replace `<db>` with it (DM-4, DM-5, DM-16)."""
    path = location.resolve_db(db)
    target = Path(f"{path}.rebuild")
    conn = sqlite3.connect(target, isolation_level=None)
    try:
        conn.execute("ANALYZE")
        conn.execute("VACUUM")
        mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        assert mode == "delete", f"journal_mode is {mode!r} after PRAGMA journal_mode=DELETE"
    finally:
        conn.close()
    os.replace(target, path)
    # a file in WAL mode next to the old store's -shm could be opened with stale shared memory
    for sidecar in (f"{path}-wal", f"{path}-shm"):
        Path(sidecar).unlink(missing_ok=True)
    for sidecar in _rebuild_files(path)[1:]:
        sidecar.unlink(missing_ok=True)


def apply_overrides(conn: sqlite3.Connection, provider: str, overrides: Mapping[str, str]) -> int:
    """DM-12: a rename from outside the bundle (Codex `session_index.jsonl`) is the level-1 title of a stored
    session. Returns the number of sessions whose title changed. Empty names are ignored."""
    changed = 0
    with load.write_txn(conn):
        for session_id, name in overrides.items():
            title = refs.clip_preview(" ".join(name.split()))
            if not title:
                continue
            row = conn.execute(
                "SELECT rowid, op_rowid, title, title_source, display_title FROM sessions"
                " WHERE provider=? AND session_id=?",
                (provider, session_id),
            ).fetchone()
            if row is None or row[2:] == (title, "rename", title):
                continue
            conn.execute(
                "UPDATE sessions SET title=?, title_source='rename', display_title=? WHERE rowid=?",
                (title, title, row[0]),
            )
            conn.execute("UPDATE ops SET title=? WHERE rowid=?", (title, row[1]))
            changed += 1
    return changed


def _run(conn, bundles, reprice, pricer, parser_version, overrides, roots) -> IngestResult:
    result = IngestResult(started_ms=int(time.time() * 1000))
    seen: set[str] = set()
    for bundle in bundles:
        result.bundles_changed += 1
        key = load.bundle_key(bundle)
        try:
            if key in seen:
                raise load.BundleInvalid(f"bundle {key} already loaded in this run")
            seen.add(key)
            result.loaded.append(load.load_bundle(conn, bundle))
        except (stream.StreamError, load.BundleInvalid) as exc:
            # FALLBACK-OK: build-plan §3 store.load FAILURE — rejected, previous rows kept, run goes on
            result.rejected.append({"bundle": key, "error": f"{type(exc).__name__}: {exc}"[:500]})
    for provider, names in overrides.items():
        result.overrides_applied += apply_overrides(conn, provider, names)
    with load.write_txn(conn):  # DM-60: rewritten from `sessions` at the end of every run
        projects.refresh(conn)
    if reprice:
        result.repriced = reprice_store(conn, pricer)
    result.ended_ms = int(time.time() * 1000)
    last_run = {
        "started_ms": result.started_ms,
        "ended_ms": result.ended_ms,
        "bundles_changed": result.bundles_changed,
        "rejected": result.rejected,
    }
    with load.write_txn(conn):
        conn.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            [
                ("last_ingest_ms", str(result.ended_ms)),
                ("n_ownership_disagreements", str(result.disagreements)),
                ("last_run", json.dumps(last_run)),
                ("price_fingerprint", pricer.fingerprint),
                *([("parser_version", parser_version)] if parser_version is not None else []),
                *([("source_roots", json.dumps(_merged_roots(conn, roots)))] if roots else []),
            ],
        )
    if result.bundles_changed or result.repriced:
        conn.execute("ANALYZE")  # DM-5
    return result


def _merged_roots(conn: sqlite3.Connection, roots: Mapping[str, str]) -> dict[str, str]:
    row = conn.execute("SELECT value FROM meta WHERE key='source_roots'").fetchone()
    return {**(json.loads(row[0]) if row else {}), **roots}


_COST_COLS = ("cost_usd", "cost_in_usd", "cost_out_usd", "cost_cache_read_usd", "cost_cache_write_usd")


def reprice_store(conn: sqlite3.Connection, pricer: Pricer) -> int:
    """Re-cost computed|missing llm_calls priced under another fingerprint; re-fold their bundles."""
    tok = (
        "in_tok",
        "out_tok",
        "cache_read_tok",
        "cache_write_tok",
        "cache_write_5m_tok",
        "cache_write_1h_tok",
    )
    rows = conn.execute(
        f"SELECT o.rowid, s.bundle, o.provider, o.model, {','.join('o.' + c for c in tok)} FROM ops o"
        " JOIN sessions s ON s.rowid = o.root_rowid WHERE o.kind='llm_call'"
        " AND o.cost_basis IN ('computed','missing') AND o.price_fingerprint IS NOT ?",
        (pricer.fingerprint,),
    ).fetchall()
    updates, bundles = [], set()
    for rowid, bundle, provider, model, *counts in rows:
        rates = pricer.resolve(model, provider)
        cost = pricer.cost(dict(zip(tok, counts, strict=True)), rates)
        values = [getattr(cost, c) for c in _COST_COLS] if cost else [None] * len(_COST_COLS)
        basis = "computed" if cost else "missing"
        alias = rates.price_alias if rates else None
        updates.append((*values, basis, alias, pricer.fingerprint, rowid))
        bundles.add(bundle)
    with load.write_txn(conn):
        conn.executemany(
            f"UPDATE ops SET {','.join(c + '=?' for c in _COST_COLS)}, cost_basis=?, price_alias=?,"
            " price_fingerprint=? WHERE rowid=?",
            updates,
        )
        for bundle in sorted(bundles):
            load.refold(conn, bundle)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('price_fingerprint', ?)", (pricer.fingerprint,)
        )
    return len(updates)


_SOURCE_COLS = (
    "path",
    "provider",
    "bundle_id",
    "size",
    "mtime_ns",
    "sha_first_4k",
    "ingested_at_ms",
    "status",
    "reject_reason",
)


def source_files(db: Path | str | None = None) -> dict[str, dict]:
    """Provider-file manifest: `{path: {provider, bundle_id, size, mtime_ns, sha_first_4k, status, ...}}`."""
    path = location.resolve_db(db)
    if not path.exists():
        return {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if schema._version(conn) != schema.SCHEMA_VERSION:
            return {}  # FALLBACK-OK: DM-4 — a store of another version is rebuilt, never opened for writing
        rows = conn.execute(
            "SELECT path, provider, bundle_id, size, mtime_ns, sha_first_4k, status, reject_reason"
            " FROM source_files"
        )
        keys = ("provider", "bundle_id", "size", "mtime_ns", "sha_first_4k", "status", "reject_reason")
        return {r[0]: dict(zip(keys, r[1:], strict=True)) for r in rows}
    finally:
        conn.close()


def record_source_files(db: Path | str | None, rows: Iterable[dict]) -> None:
    """Upsert provider-file rows (keys = `_SOURCE_COLS`)."""
    conn = open_store(location.resolve_db(db))
    try:
        with load.write_txn(conn):
            conn.executemany(
                f"INSERT INTO source_files({','.join(_SOURCE_COLS)})"
                f" VALUES ({','.join('?' * len(_SOURCE_COLS))})"
                " ON CONFLICT(path) DO UPDATE SET " + ",".join(f"{c}=excluded.{c}" for c in _SOURCE_COLS[1:]),
                [tuple(r[c] for c in _SOURCE_COLS) for r in rows],
            )
    finally:
        conn.close()


def status(db: Path | str | None = None) -> dict:
    """Read-only ingest status (types.ts IngestStatus without `freshness`)."""
    path = location.resolve_db(db)
    if not path.exists():
        return {"sources": []}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if schema._version(conn) != schema.SCHEMA_VERSION:
            return {
                "sources": []
            }  # FALLBACK-OK: schema.py CONTRACT — an old store is rebuilt by the next ingest
        row = conn.execute("SELECT value FROM meta WHERE key='source_roots'").fetchone()
        roots = json.loads(row[0]) if row else {}
        sources = [
            {
                "provider": provider,
                "root": roots.get(
                    provider, ""
                ),  # FALLBACK-OK: D52 — bundles loaded without the pipeline have no root
                "files": files,
                "bundles": bundles,
                "live": live,
                "last_ingest_ms": last_ms,
                "rejected": rejected,
            }
            for provider, files, bundles, live, last_ms, rejected in conn.execute(
                "SELECT provider, count(*), count(DISTINCT bundle_id), coalesce(sum(status='live'), 0),"
                " max(ingested_at_ms), count(DISTINCT CASE WHEN reject_reason IS NOT NULL THEN bundle_id END)"
                " FROM source_files GROUP BY provider ORDER BY provider"
            )
        ]
        out: dict = {"sources": sources}
        row = conn.execute("SELECT value FROM meta WHERE key='last_run'").fetchone()
        if row is not None:
            out["last_run"] = json.loads(row[0])
        return out
    finally:
        conn.close()
