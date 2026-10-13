"""CONTRACT: the adapter protocol and registry (design §6.4, build-plan §3 adapters).

GUARANTEES:
- `Adapter` is the protocol every provider adapter implements: `provider`,
  `discover(root)` -> `BundleRef`s, `build(ref, pricer)` -> `Bundle` (pure:
  files in, canonical records out).
- `get(provider)` returns the adapter object `ADAPTER` from
  `botsworth.adapters.<package>.adapter` (`PACKAGES` maps provider -> package).
- `session_start_ms(ref)` is the first top-level `timestamp` (ISO string or epoch ms) among the first
  `PEEK_LINES` lines of `ref.files[0]`, the bundle's main file in all three adapters; None when there is
  none. The pipeline sorts bundles by it before building (D23: the main file's first timestamped line). It
  reads no more than those lines and builds nothing.
- `BundleRejected(op_ids)` is the one rejection error (cycle after the §4.5
  rule, depth > 32, D18).
EXPECTS: each provider package defines a module-level `ADAPTER` in `adapter.py`.
FAILURE BEHAVIOR: unknown provider -> KeyError.
DOES NOT: parse files; rollups; ownership; storage.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from botsworth.model import Bundle
from botsworth.pricing import Pricer

PACKAGES: dict[str, str] = {"claude_code": "claude", "codex": "codex", "omp": "omp"}


@dataclass(frozen=True, slots=True)
class BundleRef:
    """One discovered bundle: the files that make up one session."""

    provider: str
    bundle_id: str
    session_id: str
    files: tuple[Path, ...]


PEEK_LINES = 200


def session_start_ms(ref: BundleRef) -> int | None:
    """Cheap bundle start: first top-level `timestamp` of the main file (see the CONTRACT)."""
    try:
        with ref.files[0].open("rb") as f:
            for _, line in zip(range(PEEK_LINES), f, strict=False):
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # FALLBACK-OK: D23 no usable timestamp on a line: skip it
                ts = rec.get("timestamp") if isinstance(rec, dict) else None
                if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                    return int(ts)
                if isinstance(ts, str):
                    try:
                        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
                    except ValueError:
                        continue  # FALLBACK-OK: D23 as above
    except OSError:
        return (
            None  # FALLBACK-OK: T11 contract — a vanished file is handled by the pipeline as source_missing
        )
    return None  # FALLBACK-OK: design §4.6 time missing


class BundleRejected(ValueError):
    def __init__(self, reason: str, op_ids: Iterable[str]):
        self.op_ids = list(op_ids)
        super().__init__(f"{reason}: {self.op_ids[:5]}")


class Adapter(Protocol):
    provider: str

    def discover(self, root: Path) -> Iterable[BundleRef]: ...

    def build(self, ref: BundleRef, pricer: Pricer) -> Bundle: ...


def get(provider: str) -> Adapter:
    module = importlib.import_module(f"botsworth.adapters.{PACKAGES[provider]}.adapter")
    return module.ADAPTER
