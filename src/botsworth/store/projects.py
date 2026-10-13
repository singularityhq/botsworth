"""CONTRACT: friendly project names and per-project counts (DM-60, DM-61).

GUARANTEES:
- `names(paths, home)`: each path maps to its last path component; while two paths share a name, only the
  colliding paths take one more parent component (`bizdemo/src`). A path equal to `home` is `~`. Distinct
  paths always end with distinct names.
- `refresh(conn, home)` rewrites `projects` from `sessions`, one row per distinct non-NULL `project`:
  `n_sessions` and `n_automated` count only sessions with `n_llm_calls > 0` (`n_automated` those with
  `is_automated = 1`), so a project with no such session has `n_sessions = 0` and is never all-automated;
  `first_ms` / `last_ms` are the min of `first_ms` and max of `last_ms` over all its sessions.
- No path list names a helper project (DM-61); readers compare `n_automated = n_sessions > 0`.
EXPECTS: `conn` holds the v7 schema. Paths are absolute, `/`-separated strings.
DOES NOT: read provider files; decide `is_automated` (DM-25 does); commit (the caller owns the transaction).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path


def _parts(path: str) -> list[str]:
    parts = [p for p in path.split("/") if p]
    assert parts or path == "/", f"empty project path {path!r}"
    return parts


def names(paths: Iterable[str], home: str) -> dict[str, str]:
    uniq = sorted(set(paths))
    home_norm = "/" + "/".join(_parts(home))
    out: dict[str, str] = {}
    parts: dict[str, list[str]] = {}
    for p in uniq:
        if "/" + "/".join(_parts(p)) == home_norm:
            out[p] = "~"
        else:
            parts[p] = _parts(p)
    depth = {p: 1 for p in parts}

    def name(p: str) -> str:
        pp = parts[p]
        return "/".join(pp[-depth[p] :]) if pp else "/"

    while True:
        by_name: dict[str, list[str]] = {}
        for p in parts:
            by_name.setdefault(name(p), []).append(p)
        grew = False
        for group in by_name.values():
            if len(group) > 1:
                for p in group:
                    if depth[p] < len(parts[p]):
                        depth[p] += 1
                        grew = True
        if not grew:
            break
    for p in parts:
        out[p] = name(p)
    assert len(set(out.values())) == len(out), "project names are not unique"
    return out


def refresh(conn: sqlite3.Connection, home: str = str(Path.home())) -> None:
    rows = conn.execute(
        "SELECT project,"
        " COUNT(CASE WHEN n_llm_calls > 0 THEN 1 END),"
        " COUNT(CASE WHEN n_llm_calls > 0 AND is_automated = 1 THEN 1 END),"
        " MIN(first_ms), MAX(last_ms)"
        " FROM sessions WHERE project IS NOT NULL GROUP BY project"
    ).fetchall()
    named = names([r[0] for r in rows], home)
    conn.execute("DELETE FROM projects")
    conn.executemany(
        "INSERT INTO projects(project, name, n_sessions, n_automated, first_ms, last_ms)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(r[0], named[r[0]], r[1], r[2], r[3], r[4]) for r in rows],
    )
