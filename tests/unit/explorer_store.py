"""Q1 test fixtures: copies of the QF store (tests/unit/qstore.py) with the clock pinned.

QRY-14 ranks projects over the 30 local days ending at `meta.last_ingest_ms`, which the QF build sets to the
real time of the test run. A test that asserts a colour slot would then fail once the fixture days (6-10 Oct
2026) leave that window. `xstore` is a copy whose `last_ingest_ms` is 2026-10-12 06:30 UTC; nothing else
changes. `xstore_tie` and `xstore_null_tok` change one figure each, named in the fixture, to reach a case
that the QF streams do not contain.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

PINNED_INGEST_MS = 1791786600000  # 2026-10-12T06:30:00Z, 12:00 IST


def _copy(src: Path, dst: Path) -> Path:
    source = sqlite3.connect(f"{src.as_uri()}?mode=ro", uri=True)
    target = sqlite3.connect(dst)
    source.backup(target)
    source.close()
    target.execute("UPDATE meta SET value = ? WHERE key = 'last_ingest_ms'", (str(PINNED_INGEST_MS),))
    target.commit()
    target.close()
    return dst


@pytest.fixture(scope="session")
def xstore(qstore: Path, tmp_path_factory) -> Path:
    return _copy(qstore, tmp_path_factory.mktemp("xstore") / "botsworth.sqlite")


@pytest.fixture(scope="session")
def xstore_tie(qstore: Path, tmp_path_factory) -> Path:
    """`ompproj` costs exactly what `harnessproj` costs (0.60 on one day row each): a tie at rank 6/7."""
    db = _copy(qstore, tmp_path_factory.mktemp("xstore_tie") / "botsworth.sqlite")
    conn = sqlite3.connect(db)
    for project in ("/u/f/harnessproj", "/u/j/ompproj"):
        conn.execute("UPDATE rollup_time SET cost_usd = NULL WHERE project = ?", (project,))
        conn.execute(
            "UPDATE rollup_time SET cost_usd = 0.6 WHERE rowid = (SELECT MIN(rowid) FROM rollup_time "
            "WHERE project = ? AND n_llm_calls > 0)",
            (project,),
        )
    conn.commit()
    conn.close()
    return db


@pytest.fixture(scope="session")
def xstore_null_tok(qstore: Path, tmp_path_factory) -> Path:
    """`scodex` has no token total (QRY-11: tokens are null when no call has tokens)."""
    db = _copy(qstore, tmp_path_factory.mktemp("xstore_null") / "botsworth.sqlite")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE sessions SET incl_tok_total = NULL WHERE session_id = 'scodex'")
    conn.commit()
    conn.close()
    return db
