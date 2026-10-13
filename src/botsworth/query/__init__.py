"""CONTRACT: the public query API (design §5, §6.1, §6.1a) — the one layer HTTP, CLI and agents use.

GUARANTEES:
- `query(q)` implements design §5 (scope, select incl. sessions|events|steps,
  where incl. ranges, measure, metrics incl. ratios, group_by, bucket.time,
  agg incl. distribution, outliers, compare, order, cursor paging); `share`
  ≤ 1 over a complete children list; missing metrics null with prov missing;
  every response carries `freshness`. Response keys = frontend/src/api/types.ts
  plus `shapes.WIRE_EXTRA`.
- `show`, `timeline`, `steps`, `source`, `outline` per §5.2 / §6.1a; display ids
  (`T12.3`, qualified `<session_id>:T12.3`) and stable ids (op_id,
  step_id = `<turn_id>#<ordinal>`) are both accepted wherever an id is.
- `sessions`, `projects`, `availability` serve the HTTP listings (§6.3).
- Each call opens the store read-only and closes it, unless given an open
  `sqlite3.Connection` (`db=`), which it leaves open.
EXPECTS: a store built by botsworth.store. Store choice (D20): explicit `db=`
  (path or connection) > `botsworth.store.location.resolve_db()` (CLI --db >
  `configure(db)` > env BOTSWORTH_DB > default), (store.location).
- v4 view functions (interfaces.md §2.1, §5.2) are re-exported from `botsworth.query.views`; their wire
  types are `botsworth.query.wire`. A view not built yet is a stub (`fn.__stub__ = True`, listed in
  `STUBS`) that raises `NotImplementedError`.
- Store state checks run first in every function (QRY-22): `_conn` raises `NoStore` for a missing
  file and `SchemaMismatch` when `meta.schema_version` differs from `schema.SCHEMA_VERSION`.
  `store_state(db)` reports the state without raising (HTTP and `ingest_state` use it).
FAILURE BEHAVIOR: invalid query JSON -> `QueryError` (HTTP 400, CLI exit 1);
  unknown op/step/display id -> `NotFound(QueryError)` (HTTP 404, CLI exit 1);
  missing store -> `NoStore(QueryError)` (HTTP 503 `no_store`, CLI exit 2); schema mismatch ->
  `SchemaMismatch(QueryError)` (HTTP 503 `schema_mismatch`, CLI exit 2); missing source file ->
  `SourceMissing` (re-exported from botsworth.pointer; HTTP 404).
DOES NOT: ingest; read source files except in `source()`.
KNOWN LIMITATIONS: no rolling windows (D16); no per-skill cost/time (D15);
  container `segments` (D19) are not served — the schema has no column.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from botsworth.pointer import SourceMissing
from botsworth.store.location import resolve_db
from botsworth.store.schema import SCHEMA_VERSION


class QueryError(ValueError):
    """A request the query layer rejects (400-class)."""


class NotFound(QueryError):
    """An op, step or display id that does not exist in the store (404-class)."""


class NoStore(QueryError):
    """No store file at the resolved path (503-class, QRY-22)."""


class SchemaMismatch(QueryError):
    """The store's `meta.schema_version` differs from the code's (503-class, QRY-22)."""


# Submodules import QueryError from this partly initialized package, so they load
# after it. Loading `outline.py` binds `botsworth.query.outline` to the module; the
# public function of the same name is defined below and rebinds it.
from botsworth.query import analytics, service, shapes, zone  # noqa: E402
from botsworth.query import outline as _outline  # noqa: E402

Db = str | Path | sqlite3.Connection | None


def _meta(conn: sqlite3.Connection) -> dict[str, str] | None:
    """The `meta` table, or None when it is missing or the file is not a database."""
    try:
        return dict(conn.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.DatabaseError:  # includes OperationalError (no such table: meta)
        return None  # FALLBACK-OK: interfaces.md QRY-22 — no readable meta is a schema mismatch


def _is_current(meta: dict[str, str] | None) -> bool:
    return meta is not None and meta.get("schema_version") == str(SCHEMA_VERSION)


def _mismatch(meta: dict[str, str] | None) -> SchemaMismatch:
    found = meta.get("schema_version", "none") if meta is not None else "none"
    return SchemaMismatch(f"store schema {found}, botsworth needs {SCHEMA_VERSION}; run `botsworth ingest`")


def _open_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def store_state(db: Db = None) -> tuple[str, dict[str, Any]]:
    """`(state, freshness)`; state is `missing`, `schema_mismatch` or `ready`. Never raises for a bad store.

    Freshness for a store that is not ready carries no ingest time and the code's schema version (API-30).
    """
    if isinstance(db, sqlite3.Connection):
        meta = _meta(db)
        state = "ready" if _is_current(meta) else "schema_mismatch"
        # FALLBACK-OK: interfaces.md API-30 — a connection without meta reports the code's version
        return state, shapes.freshness_from_meta(meta or {"schema_version": str(SCHEMA_VERSION)})
    path = Path(db) if db is not None else resolve_db()
    if not path.is_file():
        return "missing", shapes.freshness_from_meta({})
    conn = _open_ro(path)
    try:
        meta = _meta(conn)
    finally:
        conn.close()
    if _is_current(meta):
        return "ready", shapes.freshness_from_meta(meta or {})  # meta is not None when current
    # FALLBACK-OK: interfaces.md API-30 — a store not ready reports no ingest time, the code's version
    return "schema_mismatch", shapes.freshness_from_meta(
        {k: v for k, v in (meta or {}).items() if k != "last_ingest_ms"}
        | {"schema_version": str(SCHEMA_VERSION)}
    )


@contextmanager
def _conn(db: Db) -> Iterator[sqlite3.Connection]:
    """QRY-22: the state check runs before any view reads the store."""
    if isinstance(db, sqlite3.Connection):
        meta = _meta(db)
        if not _is_current(meta):
            raise _mismatch(meta)
        db.row_factory = sqlite3.Row
        analytics.register(db)
        yield db
        return
    if db is None:
        db = resolve_db()
    path = Path(db)
    if not path.is_file():
        raise NoStore(f"no store at {path}; run `botsworth ingest`")
    conn = _open_ro(path)
    try:
        meta = _meta(conn)
        if not _is_current(meta):
            raise _mismatch(meta)
        conn.row_factory = sqlite3.Row
        analytics.register(conn)
        yield conn
    finally:
        conn.close()


def check_store(db: Db = None) -> dict[str, Any]:
    """QRY-22 for callers that do not read the store themselves: the freshness, or the 503-class error."""
    with _conn(db) as conn:
        return shapes.freshness(conn)


def query(q: dict[str, Any], db: Db = None) -> dict[str, Any]:

    parsed = shapes.parse_query(q)
    with _conn(db) as conn:
        return service.run(conn, parsed)


def show(op_id: str, db: Db = None) -> dict[str, Any]:

    with _conn(db) as conn:
        return service.show(conn, op_id)


def timeline(op_id: str, depth: int = 3, db: Db = None) -> dict[str, Any]:

    with _conn(db) as conn:
        return service.timeline(conn, op_id, depth)


def steps(turn_id: str, limit: int = 200, cursor: str | None = None, db: Db = None) -> dict[str, Any]:

    with _conn(db) as conn:
        return service.steps(conn, turn_id, limit, cursor)


def source(
    id_or_src: str,
    part: str | None = None,
    offset: int = 0,
    max_bytes: int = 65536,
    db: Db = None,
    roots: dict[str, Path] | None = None,
) -> dict[str, Any]:

    with _conn(db) as conn:
        return views_text.source(id_or_src, part, offset, max_bytes, conn, roots)


def outline(scope_id: str, depth: int = 1, db: Db = None) -> dict[str, Any]:

    with _conn(db) as conn:
        return _outline.outline(conn, scope_id, depth)


def sessions(
    provider: str | None = None,
    project: str | None = None,
    account: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
    order: str = "-start_ms",
    limit: int = 200,
    db: Db = None,
) -> dict[str, Any]:

    filters = {"provider": provider, "project": project, "account": account, "from": from_ms, "to": to_ms}
    with _conn(db) as conn:
        return service.sessions(conn, {k: v for k, v in filters.items() if v is not None}, order, limit)


def projects(db: Db = None, tz: str | None = None) -> list[dict[str, Any]]:

    # FALLBACK-OK: D50 — a request without a zone takes the answering machine's zone (QRY-3)
    zone_name = zone.resolve_tz() if tz is None else tz
    zone.zone_of(zone_name)
    with _conn(db) as conn:
        return service.projects(conn, zone_name)


def availability() -> dict[str, Any]:

    return shapes.AVAILABILITY


from botsworth.query.views import (  # noqa: E402
    STUBS,
    agent_rows,
    day_series,
    detail,
    facets,
    find,
    histogram,
    ingest_state,
    list_sessions,
    palette,
    period_summary,
    project_slow,
    resolve,
    session_breakdown,
    session_detail,
    session_turns,
    top_tasks,
    turn_rows,
)
from botsworth.query.views import text as views_text  # noqa: E402
from botsworth.query.views.ingest import ingest_lock  # noqa: E402
from botsworth.query.views.projects import resolve_project  # noqa: E402

__all__ = [
    "STUBS",
    "NoStore",
    "NotFound",
    "QueryError",
    "SchemaMismatch",
    "SourceMissing",
    "agent_rows",
    "availability",
    "check_store",
    "day_series",
    "detail",
    "facets",
    "find",
    "histogram",
    "ingest_lock",
    "ingest_state",
    "list_sessions",
    "palette",
    "period_summary",
    "project_slow",
    "resolve",
    "resolve_project",
    "session_breakdown",
    "session_detail",
    "session_turns",
    "store_state",
    "top_tasks",
    "turn_rows",
    "outline",
    "projects",
    "query",
    "sessions",
    "show",
    "source",
    "steps",
    "timeline",
]
