"""CONTRACT: record attribution — which records are a transcript's own (design §4.4, F9, S23).

GUARANTEES:
- `load(main, agents)` returns the bundle's transcripts in owner order: the
  main transcript first, then agents by (spawnDepth, normal before fork, first
  timestamp, file name). This order decides which transcript owns a record
  or call that appears in several files of the bundle.
- Within one file, a repeated `uuid` line is dropped (first copy wins).
- In a fork transcript (`meta.isFork`), `fork-context-ref` records and
  assistant records without `attributionAgent` (the inherited parent seed)
  are dropped.
- A remaining record whose `uuid` an earlier transcript of the bundle already
  holds stays visible with `Line.copy = True`; the earlier transcript owns it.
- Each transcript reports its malformed, repeated-uuid and fork-dropped line
  counts; an unreadable `.meta.json` is counted and read as `{}`.
EXPECTS: `agents` are `subagents/agent-<id>.jsonl` paths with an optional
  sibling `agent-<id>.meta.json`.
FAILURE BEHAVIOR: unreadable transcript file -> OSError (the bundle fails).
DOES NOT: own calls across bundles (the store's ownership does, by occ_key);
  build ops.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from botsworth.adapters.claude import records
from botsworth.adapters.claude.records import Line


@dataclass(slots=True)
class Transcript:
    path: Path
    agent_id: str | None
    meta: dict[str, Any]
    meta_path: Path | None
    lines: list[Line] = field(default_factory=list)
    n_malformed: int = 0
    n_dup_uuid: int = 0
    n_fork_dropped: int = 0
    meta_unreadable: bool = False

    @property
    def owner(self) -> str:
        return self.agent_id or "main"

    @property
    def is_fork(self) -> bool:
        return bool(self.meta.get("isFork"))

    @property
    def spawn_depth(self) -> int:
        return int(self.meta.get("spawnDepth") or 0)  # FALLBACK-OK: design §4.4 main / no meta = depth 0


def agent_id(path: Path) -> str:
    return path.name.removeprefix("agent-").removesuffix(".jsonl")


def first_ts(path: Path) -> str:
    """Timestamp text of the first timestamped record (an ordering key only)."""
    with path.open("rb") as f:
        for raw in f:
            try:
                ts = json.loads(raw).get("timestamp")
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                continue  # FALLBACK-OK: design §4.4 order key ignores unreadable lines
            if ts:
                return ts
    return "~"  # FALLBACK-OK: design §4.4 untimestamped transcripts order last


def _meta(path: Path) -> tuple[dict[str, Any], Path | None, bool]:
    mp = path.with_suffix(".meta.json")
    if not mp.exists():
        return {}, None, False
    try:
        meta = json.loads(mp.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}, mp, True  # FALLBACK-OK: build-plan §3 adapters FAILURE (counted, never fatal)
    return (meta if isinstance(meta, dict) else {}), mp, not isinstance(meta, dict)


def load(main: Path | None, agents: list[Path]) -> list[Transcript]:
    order: list[Transcript] = []
    if main is not None:
        order.append(Transcript(main, None, {}, None))
    agent_ts = []
    for p in agents:
        meta, mp, bad = _meta(p)
        agent_ts.append(Transcript(p, agent_id(p), meta, mp, meta_unreadable=bad))
    agent_ts.sort(key=lambda t: (t.spawn_depth, t.is_fork, first_ts(t.path), t.path.name))
    order += agent_ts
    uuid_owner: dict[str, int] = {}
    for index, t in enumerate(order):
        lines, t.n_malformed = records.read(t.path)
        seen: set[str] = set()
        for line in lines:
            r = line.rec
            uuid = r.get("uuid")
            if uuid:
                if uuid in seen:
                    t.n_dup_uuid += 1
                    continue
                seen.add(uuid)
            if t.is_fork and (
                r.get("type") == "fork-context-ref"
                or (r.get("type") == "assistant" and not r.get("attributionAgent"))
            ):
                t.n_fork_dropped += 1
                continue
            if uuid:
                line.copy = uuid_owner.setdefault(uuid, index) != index
            t.lines.append(line)
    return order
