"""Test helper: load canonical stream files (synthetic fixtures) into a store.

Product code reads no streams folder (D52); the fixtures under `tests/fixtures/*/streams` are test inputs,
so the tests read them here and pass the bundles to `store.ingest.ingest`.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from botsworth import stream
from botsworth.store import ingest


def load_streams(streams: Iterable[Path | str] = (), **kw) -> ingest.IngestResult:
    """`ingest.ingest` over every `*.jsonl` bundle (names starting `_` skipped) of the `streams` folders."""
    bundles = [b for d in streams for b in stream.read_bundles(d)]
    return ingest.ingest(bundles, **kw)
