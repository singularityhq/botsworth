"""CONTRACT: Claude Code bundle discovery and layout (design §4.2, §6.4).

GUARANTEES:
- `discover(root)` yields one `BundleRef` per session under
  `<root>/<project-folder>/`: `bundle_id = session_id` = the main transcript
  stem `<sid>.jsonl`, or the name of a `<sid>/` directory that has a
  `subagents/` directory (subagents-only sessions). `files` = the main
  transcript (when present) followed by `<sid>/subagents/agent-*.jsonl`,
  sorted. Sessions are yielded sorted by (project folder, session id).
  A session with no transcript file at all is not yielded.
- `layout(ref)` recovers (root, project folder, main path or None, agent
  paths) from the file paths alone, so `build` needs no other state.
EXPECTS: the Claude projects layout `<root>/<folder>/<sid>.jsonl` and
  `<root>/<folder>/<sid>/subagents/agent-<id>.jsonl`.
FAILURE BEHAVIOR: a ref whose files do not follow the layout -> ValueError.
DOES NOT: read transcript contents.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from botsworth.adapters import BundleRef

PROVIDER = "claude_code"


def discover(root: Path) -> Iterator[BundleRef]:
    root = Path(root)
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        mains = {p.stem: p for p in folder.glob("*.jsonl")}
        dirs = {p.name for p in folder.iterdir() if p.is_dir() and (p / "subagents").is_dir()}
        for sid in sorted(set(mains) | dirs):
            agents = sorted((folder / sid / "subagents").glob("agent-*.jsonl")) if sid in dirs else []
            files = ([mains[sid]] if sid in mains else []) + agents
            if files:
                yield BundleRef(PROVIDER, sid, sid, tuple(files))


@dataclass(frozen=True, slots=True)
class Layout:
    root: Path
    folder: Path
    main: Path | None
    agents: list[Path]


def layout(ref: BundleRef) -> Layout:
    main = next((p for p in ref.files if p.parent.name != "subagents"), None)
    agents = [p for p in ref.files if p.parent.name == "subagents"]
    folder = main.parent if main is not None else agents[0].parents[2]
    if main is not None and main.stem != ref.session_id:
        raise ValueError(f"main transcript {main} does not match session {ref.session_id}")
    if any(p.parents[1].name != ref.session_id or p.parents[2] != folder for p in agents):
        raise ValueError(f"agent transcripts outside {folder}/{ref.session_id}/subagents")
    return Layout(folder.parent, folder, main, agents)
