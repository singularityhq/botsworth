"""The REPL fixture store (T1080): synthetic Claude, Codex and omp transcripts through the real adapters.

`repl_db` is the path of a store built once per test session by `pipeline.run` with the bundled `Pricer`:
- the E1 sessions of the three providers (every effect), with the Claude model renamed to a bundled model and
  the
  Codex session given the model `gpt-5`, so their costs are `computed` from the bundled price list;
- one more Claude session whose main thread starts agent `a0`, which starts agent `a1` (a nested engagement),
  with
  models spelled as a catalog id, a dated id and an alias.
Synthetic only; the real store is never opened.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_adapter_claude import assistant, result, tool, user, write

from botsworth import pipeline
from botsworth.pricing import Pricer

E1 = Path(__file__).parents[1] / "fixtures" / "E1"
OPUS = "claude-opus-4-5-20251101"  # a catalog id with a date
SESSION = "nest"


def nested_session(root: Path) -> None:
    """Main -> a0 -> a1: agent a1 is started by agent a0, which main started."""
    main = [
        user("u", 0, "go"),
        assistant("m1", 1, model="claude-opus-4-5", content=[tool("sp0", "Agent", prompt="p0")]),
        result("sp0", 20, toolUseResult={"agentId": "a0"}),
        assistant("m2", 30, model="claude-opus-4-5"),
    ]
    a0 = [
        user("a0u", 2, "p0"),
        assistant("a0r", 3, model="haiku", content=[tool("sp1", "Agent", prompt="p1")]),
        result("sp1", 10, toolUseResult={"agentId": "a1"}),
        assistant("a0s", 12, model="haiku"),
    ]
    a1 = [user("a1u", 4, "p1"), assistant("a1r", 5, model=OPUS), assistant("a1s", 6, model=OPUS)]
    folder = root / "-repo"
    path = write(folder / f"{SESSION}.jsonl", main)
    for aid, rows in (("a0", a0), ("a1", a1)):
        agent = write(path.with_suffix("") / "subagents" / f"agent-{aid}.jsonl", rows)
        agent.with_suffix(".meta.json").write_text(json.dumps({}))


def build_roots(base: Path) -> dict[str, Path]:
    roots = {"claude_code": base / "claude", "codex": base / "codex", "omp": base / "omp"}
    for name, sub in (("claude_code", "claude"), ("codex", "codex"), ("omp", "omp")):
        shutil.copytree(E1 / sub, roots[name])
    for f in roots["claude_code"].rglob("*.jsonl"):
        f.write_text(f.read_text().replace("fixture-a", OPUS))
    for f in roots["codex"].rglob("*.jsonl"):
        lines = f.read_text().splitlines()
        meta = {
            "timestamp": "2026-01-01T00:00:00.500Z",
            "type": "turn_context",
            "payload": {"model": "gpt-5"},
        }
        f.write_text("\n".join([lines[0], json.dumps(meta), *lines[1:]]) + "\n")
    nested_session(roots["claude_code"])
    return roots


@pytest.fixture(scope="session")
def repl_db(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("repl_src")
    roots = build_roots(base)
    db = tmp_path_factory.mktemp("repl_db") / "botsworth.sqlite"
    report = pipeline.run(list(roots), roots, db=db, pricer=Pricer())
    assert all(p["rejected"] == [] for p in report["providers"].values()), report
    return db
