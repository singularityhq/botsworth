"""CONTRACT: the Codex adapter (build-plan §3 adapters.<provider>.adapter; design §6.4).

GUARANTEES:
- `ADAPTER.discover(root)` -> one `BundleRef` per root thread (`discover.py`).
- `ADAPTER.build(ref, pricer)` -> the bundle's canonical records (`tree.py`): pure
  (files in, `Bundle` out); one `llm_call` per provider call key (§4.4); results
  paired by `call_id`; every record has `src`; `project` = the root thread's
  first cwd (D10); steps per §4.10 with raw `tool_name`, and `command_norm` +
  `command_key` on every run tool_call (D17); `occ_key` on every leaf;
  containers carry per-file `segments` and no metrics (D19); events per §4.11
  with nested `fields` (D15/D16); engagements parented by spawn `call_id`
  (`caused_by_basis` id | name | none).
- Session `title` / `title_source` (DM-10, D43): `<codex home>/session_index.jsonl`
  `thread_name` (last line for the id) -> else the last `thread_name_updated` in
  the root rollout; both `rename`. Else `threads.title` from `state_5.sqlite`
  (opened read-only) with `thread_title`. Else both None; the first user message
  is no longer a title (the store derives `first_ask`).
- `session_overrides(root)` -> `{session_id: thread_name}` from
  `<root>/session_index.jsonl`, last line per id wins, `{}` when the file is
  absent (DM-12). `build` reads the index through the same function
  (`index_names`), so a build title equals its override. The index is not a
  bundle file: a rename never rebuilds a bundle.
- Turn labels (amended gate, Kai 2026-10-12): every user-started turn (a
  `user_message`) carries an ask label; child-agent turns started by an agent
  message are exempt. An `answer` step exists on every completed (not
  interrupted) turn that has assistant text: its last say.
- `build_with_stats` also returns per-bundle counters (unknown record types,
  malformed lines) for reports.
EXPECTS: a `Pricer` (models.dev snapshot) and a ref from `discover`.
FAILURE BEHAVIOR: thread cycle or op depth > 32 -> `BundleRejected` (D18);
  unknown record types counted, never fatal.
DOES NOT: rollups, cross-bundle ownership (the store decides; in-bundle
  `duplicate_of` is a hint), storage.
KNOWN LIMITATIONS: llm_call timing derived (gap between token events, tool
  time included); old rollouts without call ids get derived ids.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from botsworth.adapters import BundleRef
from botsworth.adapters.codex import discover as _discover
from botsworth.adapters.codex import tree
from botsworth.adapters.common.taxonomy import clip
from botsworth.model import LABEL_MAX, Bundle
from botsworth.pricing import Pricer

ROOT_DIRS = ("sessions", "archived_sessions")


def relpath(path: Path) -> str:
    """Path relative to the Codex home: from the last `sessions`/`archived_sessions` component."""
    parts = path.parts
    idx = max(i for i, p in enumerate(parts) if p in ROOT_DIRS)
    return "/".join(parts[idx:])


def codex_home(path: Path) -> Path:
    """The Codex home holding a rollout `path`: everything before the last `sessions` component."""
    parts = path.parts
    idx = max(i for i, p in enumerate(parts) if p in ROOT_DIRS)
    return Path(*parts[:idx])


def index_names(home: Path) -> dict[str, str]:
    """`{session_id: thread_name}` from `<home>/session_index.jsonl`; the last usable line per id wins."""
    index = home / "session_index.jsonl"
    if not index.is_file():
        return {}  # FALLBACK-OK: DM-12 no index file = no renames
    out: dict[str, str] = {}
    for raw in index.read_bytes().split(b"\n"):
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue  # FALLBACK-OK: DM-12 a malformed or partial index line names no rename
        name = clip(rec.get("thread_name"), LABEL_MAX) if isinstance(rec, dict) else None
        if isinstance(rec, dict) and isinstance(rec.get("id"), str) and name:
            out[rec["id"]] = name
    return out


def session_overrides(root: Path) -> dict[str, str]:
    """DM-12: renames that live outside any bundle. Pure: reads one file."""
    return index_names(Path(root))


def state_title(path: Path, thread_id: str) -> str | None:
    """`threads.title` from the state db of the Codex home holding `path`, or None."""
    db = codex_home(path) / "state_5.sqlite"
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT title FROM threads WHERE id = ?", (thread_id,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None  # optional source: a locked or older db leaves the title unset (DM-10)
    return clip(row[0], LABEL_MAX) if row and row[0] else None


class CodexAdapter:
    provider = "codex"

    def discover(self, root: Path) -> Iterable[BundleRef]:
        return _discover.discover(Path(root)).refs

    def build_with_stats(self, ref: BundleRef, pricer: Pricer) -> tuple[Bundle, Counter]:
        parses = [tree.parse_file(Path(f), relpath(Path(f)), pricer) for f in ref.files]
        stats: Counter = Counter()
        for p in parses:
            stats.update(p.unknown)
        first = Path(ref.files[0])
        rename = index_names(codex_home(first)).get(ref.bundle_id)
        thread_title = state_title(first, ref.bundle_id)
        return tree.assemble(ref.bundle_id, parses, rename, thread_title), stats

    def build(self, ref: BundleRef, pricer: Pricer) -> Bundle:
        return self.build_with_stats(ref, pricer)[0]


ADAPTER = CodexAdapter()
