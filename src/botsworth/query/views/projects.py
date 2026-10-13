"""CONTRACT: resolving a user-typed project value to a project key (D49, CLI-5).

GUARANTEES:
- `resolve_project(value, db)` returns one `projects.project` key. The first rule that matches wins:
  an exact key; an exact `projects.name` (ASCII case-insensitive); a key that ends with `/` + value
  (a suffix on a `/` boundary). A rule that matches several projects is ambiguous: it does not fall
  through to the next rule.
- An ambiguous or unknown value raises `QueryError` naming up to 5 candidates as `key (name)`. For an
  ambiguous value the candidates are the matches; for an unknown value they are the keys or names that
  contain the value as a substring (ASCII case-insensitive), shortest key first.
EXPECTS: schema v7 (`projects.project`, `projects.name`); `value` is not empty.
FAILURE BEHAVIOR: `QueryError` (exit 1 in the CLI); the store-state errors of `_conn` (QRY-22).
DOES NOT: filter any view; the caller passes the returned key as the scope's project.
"""

from __future__ import annotations

from botsworth.query import Db, QueryError, _conn

MAX_CANDIDATES = 5


def _lower(text: str) -> str:
    """ASCII-only lower-casing: non-ASCII letters keep their case (D49)."""
    return "".join(c.lower() if c.isascii() else c for c in text)


def _label(row: tuple[str, str]) -> str:
    return f"{row[0]} ({row[1]})"


def _fail(value: str, kind: str, rows: list[tuple[str, str]]) -> QueryError:
    shown = ", ".join(_label(r) for r in rows[:MAX_CANDIDATES])
    more = f" and {len(rows) - MAX_CANDIDATES} more" if len(rows) > MAX_CANDIDATES else ""
    if rows:
        return QueryError(f"--project {value!r} is {kind}; candidates: {shown}{more}")
    return QueryError(f"--project {value!r} is {kind}; no project key or name contains it")


def resolve_project(value: str, db: Db = None) -> str:
    assert value, "resolve_project needs a value"
    with _conn(db) as conn:
        rows = [(r[0], r[1]) for r in conn.execute("SELECT project, name FROM projects ORDER BY project")]
    for r in rows:
        if r[0] == value:
            return r[0]
    low = _lower(value)
    by_name = [r for r in rows if _lower(r[1]) == low]
    if len(by_name) == 1:
        return by_name[0][0]
    if by_name:
        raise _fail(value, "ambiguous", by_name)
    by_suffix = [r for r in rows if r[0].endswith("/" + value)]
    if len(by_suffix) == 1:
        return by_suffix[0][0]
    if by_suffix:
        raise _fail(value, "ambiguous", by_suffix)
    near = sorted(
        (r for r in rows if low in _lower(r[0]) or low in _lower(r[1])), key=lambda r: (len(r[0]), r[0])
    )
    raise _fail(value, "unknown", near)
