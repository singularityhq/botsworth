"""CONTRACT: Codex corpus -> bundles (design §4.5, §6.4).

GUARANTEES:
- `discover(root)` reads only the first line of every `root/sessions/**/*.jsonl`
  and `root/archived_sessions/*.jsonl`. A file whose first record is a
  `session_meta` contributes one thread: `id`, parent =
  `source.subagent.thread_spawn.parent_thread_id`, `depth`, `forked_from_id`,
  `timestamp`. Files without one (v0 rollouts: no timestamps or token events)
  are skipped and counted in `Discovery.skipped`.
- One `BundleRef` per root thread (no parent, or a parent not in the corpus):
  bundle_id = session_id = the root thread id; `files` = the root's file first,
  then every descendant thread's file ordered by (spawn depth, non-fork first,
  creation time, path) — the ownership order of invariant 5.
- A parent chain that loops is not resolved here: its threads become one
  bundle rooted at the smallest id, and `build` rejects it (D18).
- A thread id seen in two files keeps the first file in sorted path order
  (sessions before archived_sessions); the other is counted in `Discovery.shadowed`.
EXPECTS: the Codex home layout (`CODEX_HOME`, default `~/.codex`).
FAILURE BEHAVIOR: an unreadable first line counts as skipped.
DOES NOT: parse beyond the first line; read `state_5.sqlite` (session_meta
  carries every spawn edge it lists: 159/159 on 2026-10-12).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from botsworth.adapters import BundleRef

PROVIDER = "codex"


@dataclass(slots=True)
class Thread:
    sid: str
    path: Path
    parent: str | None
    depth: int | None
    forked_from: str | None
    created: str


@dataclass(slots=True)
class Discovery:
    refs: list[BundleRef] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    shadowed: list[Path] = field(default_factory=list)


def rollout_files(root: Path) -> list[Path]:
    return sorted(root.glob("sessions/**/*.jsonl")) + sorted(root.glob("archived_sessions/*.jsonl"))


def first_record(path: Path) -> dict[str, Any] | None:
    with path.open("rb") as f:
        line = f.readline()
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None  # FALLBACK-OK: design §9 malformed line -> skip, count
    return rec if isinstance(rec, dict) else None


def thread_of(meta: dict[str, Any], path: Path) -> Thread:
    src = meta.get("source")
    spawn = (src.get("subagent") or {}) if isinstance(src, dict) else {}
    spawn = spawn.get("thread_spawn") or {} if isinstance(spawn, dict) else {}
    return Thread(
        sid=meta["id"],
        path=path,
        parent=spawn.get("parent_thread_id"),
        depth=spawn.get("depth"),
        forked_from=meta.get("forked_from_id"),
        created=meta.get("timestamp") or "",
    )


def order_key(t: Thread) -> tuple:
    return (t.depth or 0, t.forked_from is not None, t.created, str(t.path))


def group(threads: dict[str, Thread]) -> dict[str, list[Thread]]:
    """root id -> threads of that bundle (root first, then ownership order)."""

    def root_of(sid: str) -> str:
        seen = [sid]
        while (p := threads[seen[-1]].parent) in threads and p != seen[-1]:
            if p in seen:  # cycle: build() rejects the bundle loudly
                return min(seen[seen.index(p) :])
            seen.append(p)
        return seen[-1]

    out: dict[str, list[Thread]] = {}
    for t in threads.values():
        out.setdefault(root_of(t.sid), []).append(t)
    for root, members in out.items():
        members.sort(key=lambda t: (t.sid != root, *order_key(t)))
    return out


def discover(root: Path) -> Discovery:
    found = Discovery()
    threads: dict[str, Thread] = {}
    for path in rollout_files(Path(root)):
        rec = first_record(path)
        if rec is None or rec.get("type") != "session_meta" or not isinstance(rec.get("payload"), dict):
            found.skipped.append(path)
            continue
        t = thread_of(rec["payload"], path)
        if t.sid in threads:
            found.shadowed.append(path)
            continue
        threads[t.sid] = t
    for root_id, members in sorted(group(threads).items()):
        found.refs.append(
            BundleRef(
                provider=PROVIDER,
                bundle_id=root_id,
                session_id=root_id,
                files=tuple(t.path for t in members),
            )
        )
    return found
