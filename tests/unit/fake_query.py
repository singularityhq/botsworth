"""A stand-in for `botsworth.query` (T5) used by the T6 CLI/HTTP tests.

Records every call and returns payloads shaped like frontend/src/api/types.ts.
Unknown ids raise `NotFound`; a query with `"select": "bad"` raises QueryError.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from botsworth.query import NotFound, QueryError, SourceMissing

# Legacy v3 wire types frozen from 0d890bd; v4 wire is checked by Q0. Delete when legacy routes go.
TYPES_TS = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "legacy_types.ts"


FRESH = {"last_ingest_ms": 1791100000000, "parser_version": "1", "price_fingerprint": "abc"}
AVAIL = {"rows": [{"metric": "ttft_ms", "claude_code": "missing", "codex": "missing", "omp": "present"}]}
PROJECT = {
    "project": "p1",
    "n_sessions": 3,
    "providers": ["claude_code", "omp"],
    "first_ms": 10,
    "last_ms": 20,
    "incl_cost_usd": 1.5,
    "tok_total": 100,
}


def op(op_id: str, kind: str = "session", **extra: Any) -> dict[str, Any]:
    return {
        "op_id": op_id,
        "kind": kind,
        "label": f"label {op_id}",
        "start_ms": 1,
        "provider": "omp",
        **extra,
    }


class FakeQuery:
    QueryError = QueryError
    NotFound = NotFound

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.dbs: set[Any] = set()

    def _rec(self, name: str, *a: Any, db: Any = None, **kw: Any) -> None:
        self.dbs.add(db)
        self.calls.append((name, a, kw))

    def _known(self, i: str) -> None:
        if i.startswith("missing"):
            raise NotFound(f"no op {i}")

    def query(self, q: dict[str, Any], db: Any = None) -> dict[str, Any]:
        self._rec("query", q, db=db)
        if q.get("select") == "bad":
            raise QueryError("select must be one of ...")
        rows = [{"op": op("s1")}]
        return self._resp(rows)

    def _resp(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "freshness": FRESH,
            "scope_totals": {},
            "rows": rows,
            "next_cursor": None,
            "availability": AVAIL,
        }

    def sessions(self, db: Any = None, **kw: Any) -> dict[str, Any]:
        self._rec("sessions", db=db, **kw)
        s1 = op("s1", title="t", git_branch="main", harness_version="1", status="done")
        return {"freshness": FRESH, "sessions": [s1]}

    def projects(self, db: Any = None) -> list[dict[str, Any]]:
        self._rec("projects", db=db)
        return [PROJECT]

    def availability(self) -> dict[str, Any]:
        """T5: a static matrix (design §4.8); takes no db."""
        self._rec("availability", db="no-db")
        return AVAIL

    def show(self, op_id: str, db: Any = None) -> dict[str, Any]:
        self._rec("show", op_id, db=db)
        self._known(op_id)
        return {"op": op(op_id), "ancestors": [], "children_counts": {"turn": 2}, "events": []}

    def timeline(self, op_id: str, depth: int = 3, db: Any = None) -> dict[str, Any]:
        self._rec("timeline", op_id, depth=depth, db=db)
        self._known(op_id)
        return {"root": op(op_id), "ops": [op("t1", "turn")], "events": []}

    def steps(
        self, turn_id: str, limit: int = 200, cursor: str | None = None, db: Any = None
    ) -> dict[str, Any]:
        self._rec("steps", turn_id, limit=limit, cursor=cursor, db=db)
        self._known(turn_id)
        step = {
            "step_id": f"{turn_id}#1",
            "verb": "run",
            "target": "pytest",
            "outcome": "exit:1",
            "dur_ms": 4200,
        }
        return {"turn": op(turn_id, "turn"), "steps": [step], "next_cursor": None}

    def source(
        self, id_or_src: str, part: str | None = None, offset: int = 0, max_bytes: int = 65536, db: Any = None
    ):
        self._rec("source", id_or_src, part=part, offset=offset, max_bytes=max_bytes, db=db)
        self._known(id_or_src)
        if id_or_src.startswith("gone"):
            raise SourceMissing(id_or_src)
        text = "0123456789"
        return {
            "text": text[offset : offset + max_bytes],
            "total_bytes": len(text),
            "drift": False,
            "file": "f.jsonl",
            "locator": "L1",
        }

    def outline(self, scope_id: str, depth: int = 1, db: Any = None) -> dict[str, Any]:
        self._rec("outline", scope_id, depth=depth, db=db)
        self._known(scope_id)
        return {
            "rows": [
                {
                    "display_id": "T12.2",
                    "id": "x#2",
                    "verb": "run",
                    "target": "pytest",
                    "outcome": "exit:1",
                    "dur_ms": 4200,
                },
                {
                    "display_id": "T12",
                    "id": "x",
                    "kind": "turn",
                    "target": "Fixed",
                    "outcome": "ok",
                    "dur_ms": 41000,
                    "cost_usd": 0.38,
                },
            ]
        }


def ts_keys(interface: str) -> set[str]:
    """Top-level field names of one `export interface X { ... }` in types.ts."""
    text = TYPES_TS.read_text()
    m = re.search(rf"export interface {interface}\b[^{{]*\{{(.*?)\n\}}", text, re.S)
    assert m, interface
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", m.group(1), flags=re.S)
    depth, keys = 0, set()
    for line in body.splitlines():
        if depth == 0:
            km = re.match(r"\s*(\w+)\??:", line)
            if km:
                keys.add(km.group(1))
        depth += line.count("{") + line.count("<") - line.count("}") - line.count(">")
    return keys
