# ruff: noqa: F811
"""FIND-PERF parity: `find` returns the same result for every kind on every fixture session as before the
speed-up (QRY-38, API-25).

`tests/unit/data/find_parity.json` was captured from base commit 62b7cb7 (the code before FIND-PERF) with
`FIND_PARITY_WRITE=1 uv run pytest tests/unit/test_find_parity.py`. Do not regenerate it from changed code.
`freshness` is left out: it describes the store build, not the hits.

D54 recapture (2026-10-21), the only time: the fixture gained an agent permission call in `sstretch` and
`sslow1`'s AskUserQuestion became a `waits` hit. 11 of the 345 entries changed, all explained by that:
`sslow1` `waits` 0 -> 1 (the 15 min ask tool row); `sstretch` `waits` 5 -> 7 and `biggest|time` 12 -> 14 (the
40 min agents stretch is now agents, you/permission, agents), `biggest|tokens` same count with the agent
row's 240 tokens, `search` for `a` 8 -> 10, `agent` 4 -> 6, `bash` 0 -> 2, `tool request` 1 -> 2 (the new
Bash tool row and its call).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from q2c_store import q2cstore  # noqa: F401 (fixture)
from qstore import qstore  # noqa: F401 (the QF fixture)

from botsworth.query import _conn
from botsworth.query.views.find import KINDS, MEASURES, find

EXPECTED = Path(__file__).parent / "data" / "find_parity.json"
TERMS = ["a", "bash", "read", "test", "skill", "tool request", "thinking", "pytest", "ENDMARK-D42", "agent"]
LIMITS = (2000, 3)


def _sessions(db: Path) -> list[str]:
    with _conn(db) as conn:
        return [r[0] for r in conn.execute("SELECT op_id FROM ops WHERE kind = 'session' ORDER BY op_id")]


def _cases() -> list[tuple[str, str, str, str, int]]:
    """(kind, measure, text, ...) combinations; `biggest` varies the measure, `search` the term."""
    out: list[tuple[str, str, str, int]] = []
    for kind in KINDS:
        if kind == "biggest":
            out += [(kind, m, "", 2000) for m in MEASURES] + [(kind, "cost", "", 3)]
        elif kind == "search":
            out += [(kind, "cost", t, 2000) for t in TERMS] + [(kind, "cost", TERMS[0], 3)]
        else:
            out += [(kind, "cost", "", 2000), (kind, "cost", "", 3)]
    return out  # type: ignore[return-value]


def _capture(db: Path, label: str) -> dict:
    out: dict = {}
    for session in _sessions(db):
        for kind, measure, text, limit in _cases():
            res = asdict(find(session, kind, measure, text, limit, db=db))
            res.pop("freshness")
            out[f"{label}|{session}|{kind}|{measure}|{text}|{limit}"] = res
    return out


def test_find_matches_the_pre_change_output(qstore, q2cstore):
    db2, _ = q2cstore
    got = {**_capture(qstore, "qf"), **_capture(db2, "q2c")}
    if os.environ.get("FIND_PARITY_WRITE"):
        EXPECTED.write_text(json.dumps(got, sort_keys=True, indent=0))
    want = json.loads(EXPECTED.read_text())
    assert sorted(got) == sorted(want)
    # sanity: the captured set exercises every kind with real hits
    hit_kinds = {k.split("|")[2] for k, v in want.items() if v["total"] > 0}
    assert hit_kinds == set(KINDS)
    assert json.loads(json.dumps(got, sort_keys=True)) == want
