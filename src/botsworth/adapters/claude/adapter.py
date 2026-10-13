"""CONTRACT: the Claude Code adapter — one session in, one canonical Bundle out (build-plan §3).

GUARANTEES:
- `ADAPTER.discover(root)` yields one `BundleRef` per session (discover.py);
  `ADAPTER.build(ref, pricer)` is pure: it reads only `ref.files` (plus
  sibling `.meta.json` files) and returns a `Bundle` whose records satisfy
  `stream.validate`.
- One `llm_call` per requestId per transcript, one `tool_call` per tool_use
  id, results paired by id; every record carries `src`; `project` = the
  session's first cwd (D10), else the decoded project folder (no `cwd` on the
  session op marks it derived); steps per §4.10 with raw `tool_name`;
  `command_norm`/`command_key` on every Bash call; `occ_key`, `spawn_depth`,
  `is_fork` on every leaf; containers carry per-file `segments` (D19) and no
  metrics; engagements parented per §4.5 incl. the cycle rule, with
  `caused_by_basis`; events per §4.11 with nested `fields`.
- `source_files` lists every transcript and meta file with `size`,
  `mtime_ns` and `sha_first_4k`, taken before the file is read, and `ts_list`
  (DM-40): the sorted epoch ms of every timestamped record the transcript
  holds (any record type, so a `queue-operation` counts); `[]` for a meta file.
- `run(root, out_dir, pricer)` (CLI: `python -m botsworth.adapters.claude.adapter`)
  writes every bundle with `stream.write_bundle` after `stream.defects` is
  empty, and writes `<out>/_summary.json` with counts, link bases, depth,
  defects and rejections.
EXPECTS: a price snapshot (`Pricer`).
FAILURE BEHAVIOR: cycle after the §4.5 rule or depth > 32 ->
  `BundleRejected(op_ids)` (D18); `run` records the rejection and continues.
  Malformed lines and unreadable meta files are counted, never fatal.
DOES NOT: rollups, cross-bundle ownership (the store decides by `occ_key`;
  in-bundle `duplicate_of` is a hint), storage.
KNOWN LIMITATIONS: llm_call timing is derived from record gaps; `ttft_ms`
  missing; compound Bash commands are not split (R-outline, deferred);
  cross-bundle copies (resumed sessions)
  repeat their events (events carry no occ_key).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import time
from collections import Counter
from pathlib import Path

from botsworth import stream
from botsworth.adapters import BundleRef, BundleRejected
from botsworth.adapters.claude import attribution, calls, discover, events, steps, tree
from botsworth.adapters.claude.attribution import Transcript
from botsworth.adapters.claude.records import ts_ms
from botsworth.model import Bundle, Kind, SourceFile
from botsworth.pointer import DEFAULT_ROOTS
from botsworth.pricing import Pricer

PROVIDER = "claude_code"
PARSER_VERSION = "claude/1 (T1: D15-D19)"


def decode_project(folder: str) -> str:
    """Claude encodes a project path by replacing every non-alphanumeric char with '-';
    decoding is ambiguous, so resolve greedily against the filesystem (longest
    existing entry first); an unresolved tail uses '/'."""
    parts = folder.lstrip("-").split("-")
    path, i = Path("/"), 0
    while i < len(parts):
        for j in range(len(parts), i, -1):
            chunk = parts[i:j]
            names = ("-".join(chunk), ".".join(chunk), "_".join(chunk), "." + "-".join(chunk))
            hit = next((n for n in names if n and (path / n).exists()), None)
            if hit:
                path, i = path / hit, j
                break
        else:
            return str(path / "/".join(parts[i:]))
    return str(path)


def _project(transcripts: list[Transcript], folder: Path) -> tuple[str, bool]:
    for t in transcripts:
        cwd = next(
            (ln.rec["cwd"] for ln in t.lines if isinstance(ln.rec.get("cwd"), str) and ln.rec["cwd"]), None
        )
        if cwd:
            return cwd, True
    return decode_project(
        folder.name
    ), False  # FALLBACK-OK: build-plan §2 discover (no-cwd sessions: decoded folder)


def _source_file(path: Path) -> SourceFile:
    st = path.stat()
    with path.open("rb") as f:
        sha = hashlib.sha256(f.read(4096)).hexdigest()
    return SourceFile(
        provider=PROVIDER,
        path=str(path.absolute()),
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        sha_first_4k=sha,
    )


def build_bundle(ref: BundleRef, pricer: Pricer) -> tuple[Bundle, Counter]:
    lay = discover.layout(ref)
    files = [p for p in ref.files] + [m for p in lay.agents if (m := p.with_suffix(".meta.json")).exists()]
    sources = [_source_file(p) for p in files]
    transcripts = attribution.load(lay.main, lay.agents)
    stamps = {
        t.path: sorted(ms for ln in t.lines if (ms := ts_ms(ln.rec.get("timestamp"))) is not None)
        for t in transcripts
    }
    for sf in sources:
        # a meta file holds no records: its list is empty (DM-40: ts_list covers every file of the bundle)
        sf.ts_list = stamps.get(Path(sf.path), [])
    project, cwd_present = _project(transcripts, lay.folder)
    ctx = tree.Ctx(ref.session_id, lay.root, project, transcripts)
    shape = tree.build(ctx, cwd_present)
    leaves = calls.build(ctx, shape, pricer)
    tree.link(ctx, shape, leaves.sends)
    ops, moved = tree.finalize(ctx, shape, leaves.ops)
    step_rows = steps.build(ctx, leaves)
    event_rows = events.build(ctx, shape, leaves, step_rows)
    bundle = Bundle(
        provider=PROVIDER,
        bundle_id=ref.bundle_id,
        session_id=ref.session_id,
        ops=ops,
        steps=step_rows,
        events=event_rows,
        source_files=sources,
        n_malformed=sum(t.n_malformed for t in transcripts),
    )
    stats = Counter(leaves.stats)
    stats["engagement_cycle"] += moved
    stats["project_derived"] += not cwd_present
    stats["dup_uuid_lines"] += sum(t.n_dup_uuid for t in transcripts)
    stats["fork_dropped_lines"] += sum(t.n_fork_dropped for t in transcripts)
    stats["meta_unreadable"] += sum(t.meta_unreadable for t in transcripts)
    return bundle, stats


class ClaudeAdapter:
    provider = PROVIDER

    def discover(self, root: Path):
        return discover.discover(root)

    def build(self, ref: BundleRef, pricer: Pricer) -> Bundle:
        return build_bundle(ref, pricer)[0]


ADAPTER = ClaudeAdapter()


def _count(stats: Counter, bundle: Bundle) -> None:
    stats["bundles"] += 1
    stats["malformed_lines"] += bundle.n_malformed
    for o in bundle.ops:
        stats[f"op:{o.kind}"] += 1
        if o.duplicate_of:
            stats[f"copy_hint:{o.kind}"] += 1
        if o.kind == Kind.ENGAGEMENT:
            stats[f"basis:{o.caused_by_basis}"] += 1
        if o.kind == Kind.LLM_CALL and not o.duplicate_of:
            stats["own_llm_calls"] += 1
            stats["unpriced_llm_calls"] += o.cost_usd is None
        if o.kind == Kind.TOOL_CALL and o.tool_name == "Bash":
            stats["bash_calls"] += 1
            stats["bash_command_norm"] += o.command_norm is not None
            stats["bash_command_key"] += o.command_key is not None
        stats["max_depth"] = max(stats["max_depth"], o.depth or 0)
    stats["rec:step"] += len(bundle.steps)
    for s in bundle.steps:
        stats[f"verb:{s.verb}"] += 1
    for e in bundle.events:
        stats[f"event:{e.kind}"] += 1
        if e.kind == "skill_invocation":
            stats[f"skill_via:{e.fields['via']}"] += 1


def run(root: Path, out_dir: Path, pricer: Pricer, session: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    stats: Counter = Counter()
    defects: Counter = Counter()
    rejected: list[dict] = []
    own_cost = 0.0
    occ_cost: dict[str, float | None] = {}
    out_bytes = 0
    for ref in discover.discover(root):
        if session and ref.session_id != session:
            continue
        try:
            bundle, extra = build_bundle(ref, pricer)
        except BundleRejected as exc:
            rejected.append({"bundle_id": ref.bundle_id, "reason": str(exc), "op_ids": exc.op_ids[:20]})
            continue
        found = stream.defects(bundle)
        if found:
            defects.update(d.kind for d in found)
            rejected.append({"bundle_id": ref.bundle_id, "reason": "defects", "first": str(found[0])})
            continue
        out_bytes += stream.write_bundle(bundle, out_dir).stat().st_size
        stats.update(extra)
        _count(stats, bundle)
        for o in bundle.ops:
            if o.kind == Kind.LLM_CALL and not o.duplicate_of:
                own_cost += o.cost_usd or 0.0
                occ_cost.setdefault(o.occ_key, o.cost_usd)
    summary = {
        "provider": PROVIDER,
        "adapter": "botsworth.adapters.claude",
        "parser_version": PARSER_VERSION,
        "price_fingerprint": pricer.fingerprint,
        "python": sys.version.split()[0],
        "bundles": stats["bundles"],
        "rejected": rejected,
        "defects": dict(defects),
        "counts": dict(sorted(stats.items())),
        "own_cost_usd": round(own_cost, 6),
        "distinct_llm_occ_keys": len(occ_cost),
        "distinct_occ_cost_usd": round(sum(c or 0.0 for c in occ_cost.values()), 6),
        "wall_s": round(time.perf_counter() - started, 2),
        "max_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 1),
        "out_bytes": out_bytes,
    }
    (out_dir / "_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Claude Code transcripts -> canonical stream bundles")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOTS[PROVIDER])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--session", help="only this session id")
    ap.add_argument("--prices", type=Path, help="price snapshot (default: newest in pricing/data)")
    args = ap.parse_args(argv)
    summary = run(args.root, args.out, Pricer(args.prices), args.session)
    print(json.dumps({k: v for k, v in summary.items() if k != "counts"}, indent=2))
    return 1 if summary["rejected"] else 0


if __name__ == "__main__":
    sys.exit(main())
