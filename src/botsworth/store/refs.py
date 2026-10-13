"""CONTRACT: compact content references, interned strings and stored previews (DM-6, DM-8, DM-9, DM-14).

A content reference (`src://<provider>/<relpath>#<locator>[/<json.path>][?h=<hash>]`, `botsworth.pointer`)
is stored as three columns: `src_file` (rowid in `src_files`), `src_loc` (the text after `#` and before
`?`) and `src_h` (the 8 bytes of `h`, NULL when the URI has none).

GUARANTEES:
- `to_parts(uri)` and `uri(prefix, src_loc, src_h)` are inverses byte for byte:
  `uri(prefix, loc, h) == original` for the `(provider, relpath, prefix, loc, h)` that `to_parts(original)`
  returns. `prefix` is the original text before `#`, so a non-canonical percent-encoding survives.
- `uri` is the only Python code that formats a URI from stored parts; `SRC_SQL` / `src_sql` are the same
  rule as a SQL expression (views, queries). Nothing else concatenates the parts.
- `intern_file(conn, provider, relpath, prefix)` returns the stable `src_files.rowid` of
  `(provider, relpath)`; a reload of a bundle gets the same rowid. `src_files` rows are never deleted.
- `intern_name(conn, s)` returns the `names.id` of `s`, or None for None.
- `occ_hash(occ_key)` is the first 8 bytes of SHA-256 over the UTF-8 key (DM-14).
- `clip_preview(text, n)` cuts at `n` characters and strips trailing whitespace (DM-9); None stays None
  and an all-whitespace result is the empty string.
EXPECTS: well-formed `src://` URIs whose `h`, when present, is 16 hex digits (`pointer.content_hash`).
FAILURE BEHAVIOR: a URI outside the grammar raises `pointer.PointerError`; an `h` that is not 16 hex
  digits raises `ValueError`.
DOES NOT: read provider files, resolve a pointer, or know provider roots.
"""

from __future__ import annotations

import hashlib
import sqlite3
from urllib.parse import unquote

from botsworth import pointer
from botsworth.model import PREVIEW_MAX

OCC_HASH_BYTES = 8

# The DM-8 expression over the alias `{t}` holding src_loc/src_h and the alias `{f}` of its `src_files` row.
SRC_SQL = (
    "({f}.prefix || '#' || {t}.src_loc"
    " || CASE WHEN {t}.src_h IS NULL THEN '' ELSE '?h=' || lower(hex({t}.src_h)) END)"
)


def src_sql(t: str = "o", f: str = "sf") -> str:
    return SRC_SQL.format(t=t, f=f)


def to_parts(uri_text: str) -> tuple[str, str, str, str, bytes | None]:
    """`(provider, relpath, prefix, src_loc, src_h)` of a content reference."""
    p = pointer.parse(uri_text)  # validates the grammar
    prefix, _, rest = uri_text.partition("#")
    src_loc, _, query = rest.partition("?")
    src_h = None
    if p.h is not None:
        if len(p.h) != pointer.HASH_LEN:
            raise ValueError(f"h must be {pointer.HASH_LEN} hex digits: {uri_text!r}")
        src_h = bytes.fromhex(p.h)
    assert query == (f"h={p.h}" if p.h is not None else ""), uri_text
    return p.provider, unquote(prefix.removeprefix(pointer.SCHEME).partition("/")[2]), prefix, src_loc, src_h


def uri(prefix: str, src_loc: str, src_h: bytes | None) -> str:
    return f"{prefix}#{src_loc}" + ("" if src_h is None else f"?h={src_h.hex()}")


def occ_hash(occ_key: str) -> bytes:
    return hashlib.sha256(occ_key.encode("utf-8")).digest()[:OCC_HASH_BYTES]


def clip_preview(text: str | None, n: int = PREVIEW_MAX) -> str | None:
    if text is None:
        return None
    return text[:n].rstrip()


def intern_file(conn: sqlite3.Connection, provider: str, relpath: str, prefix: str) -> int:
    conn.execute(
        "INSERT INTO src_files(provider, relpath, prefix) VALUES (?,?,?) ON CONFLICT DO NOTHING",
        (provider, relpath, prefix),
    )
    return conn.execute(
        "SELECT rowid FROM src_files WHERE provider=? AND relpath=?", (provider, relpath)
    ).fetchone()[0]


def intern_name(conn: sqlite3.Connection, s: str | None) -> int | None:
    if s is None:
        return None
    conn.execute("INSERT INTO names(s) VALUES (?) ON CONFLICT DO NOTHING", (s,))
    return conn.execute("SELECT id FROM names WHERE s=?", (s,)).fetchone()[0]
