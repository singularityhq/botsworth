"""The QF fixture store (TST-1): canonical streams in tests/fixtures/qstore/streams ingested into tmp.

`qstore` is the path of a v7 store built once per test session by `store.ingest.ingest` with
`BOTSWORTH_TZ=Asia/Kolkata`; tests treat it as read-only. `qroots` maps each provider to the directory the
streams' `src://` pointers are relative to. The real store is never opened.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from stream_load import load_streams

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "qstore"
STREAMS = FIXTURE / "streams"
SRC = FIXTURE / "src"


@pytest.fixture(scope="session")
def qstore(tmp_path_factory) -> Path:
    db = tmp_path_factory.mktemp("qstore") / "botsworth.sqlite"
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("BOTSWORTH_TZ", "Asia/Kolkata")
        result = load_streams(streams=[STREAMS], db=db)
    assert result.rejected == [], result.rejected
    return db


@pytest.fixture(scope="session")
def qroots() -> dict[str, Path]:
    return {p.name: p for p in sorted(SRC.iterdir()) if p.is_dir()}


def expected() -> dict:
    """Hand-derived numbers, `tests/fixtures/qstore/expected.json`."""
    return json.loads((FIXTURE / "expected.json").read_text())
