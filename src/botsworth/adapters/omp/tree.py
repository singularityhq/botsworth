"""CONTRACT: the omp file tree -> engagement links, and the bundle depth check.

GUARANTEES:
- `link_files(top, facts)` returns one `FileNode` per file, parents before
  children. A child file is `<parent-stem>/<id>.jsonl`. Its cause, in order
  (design §4.5, gated extract-omp rules):
  `id`   — a `task` toolResult of the parent names the child in
           `details.results[].id` or `details.progress[].id`;
  `name` — else the latest `eval` call containing `agent(` that started
           before the child's header and whose code or result text contains the
           child's file stem;
  `time-heuristic` — else the latest such `eval` call regardless of name;
  `none` — else no cause (the engagement is parented to the session).
  Files under the bundle that no walk reaches are linked with basis `none`.
- `spawn_depth` = number of engagement hops from the main transcript.
- `check_depth(ops)` returns op depth by op_id (session = 0) and raises
  `BundleRejected` on a parent cycle or depth > 32 (D18).
EXPECTS: `facts` holds a parsed `FileFacts` for every bundle file.
DOES NOT: build ops; read files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from botsworth.adapters import BundleRejected
from botsworth.adapters.omp.records import FileFacts, ToolFacts, iso_ms
from botsworth.model import LinkBasis, Op

MAX_DEPTH = 32


@dataclass(slots=True)
class FileNode:
    path: Path
    parent: Path | None
    cause_call: str | None  # toolCallId in the parent file
    basis: str | None  # None for the main transcript
    spawn_depth: int


def _eval_spawns(fp: FileFacts, child_ms: int | None) -> list[ToolFacts]:
    return [
        tc
        for tc in fp.tools.values()
        if tc.name == "eval"
        and "agent(" in json.dumps(tc.args)
        and (tc.start_ms or tc.issued_ms or 0) <= (child_ms or 0)  # FALLBACK-OK: extract-omp gated rule
    ]


def _started(tc: ToolFacts) -> int:
    return tc.start_ms or tc.issued_ms or 0  # FALLBACK-OK: extract-omp gated rule, latest-before


def link_files(top: Path, facts: dict[Path, FileFacts]) -> list[FileNode]:
    nodes = [FileNode(top, None, None, None, 0)]
    queue = [nodes[0]]
    while queue:
        node = queue.pop(0)
        fp = facts[node.path]
        child_dir = node.path.with_suffix("")
        children = sorted(p for p in facts if p.parent == child_dir)
        for cp in children:
            cid = fp.task_children.get(cp.stem)
            basis = LinkBasis.ID
            if cid is None:
                child_ms = iso_ms(facts[cp].header.get("timestamp"))
                evals = _eval_spawns(fp, child_ms)
                named = [
                    tc
                    for tc in evals
                    if cp.stem in json.dumps(tc.args) + json.dumps((tc.result or {}).get("content"))
                ]
                if named:
                    cid, basis = max(named, key=_started).call_id, LinkBasis.NAME
                elif evals and child_ms is not None:
                    cid, basis = max(evals, key=_started).call_id, LinkBasis.TIME_HEURISTIC
                else:
                    basis = LinkBasis.NONE
            child = FileNode(cp, node.path, cid, basis.value, node.spawn_depth + 1)
            nodes.append(child)
            queue.append(child)
    reached = {n.path for n in nodes}
    for p in sorted(facts):
        if p not in reached:
            nodes.append(FileNode(p, top, None, LinkBasis.NONE.value, 1))
    return nodes


def check_depth(ops: list[Op]) -> dict[str, int]:
    parent = {o.op_id: o.parent for o in ops}
    depth: dict[str, int] = {}
    for start in parent:
        chain: list[str] = []
        cur: str | None = start
        while cur is not None and cur not in depth:
            if cur in chain:
                raise BundleRejected("parent cycle", chain)
            chain.append(cur)
            cur = parent.get(cur)
        base = -1 if cur is None else depth[cur]
        for i, op_id in enumerate(reversed(chain)):
            depth[op_id] = base + 1 + i
            if depth[op_id] > MAX_DEPTH:
                raise BundleRejected(f"depth > {MAX_DEPTH}", reversed(chain))
    return depth
