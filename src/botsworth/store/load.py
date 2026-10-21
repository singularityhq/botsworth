"""CONTRACT: fold one canonical bundle into store rows and replace it atomically (design §4.1, §4.6, §8).

GUARANTEES:
- `load_bundle(conn, bundle)` runs in one write transaction: the bundle's old
  rows are replaced whole or not at all. Before writing, `stream.validate`
  and the tree check (one session root, every op reachable, no cycle, depth
  <= 32, D18) run; any defect raises and the previous rows stay.
- Ownership is decided by `store.ownership` against the whole store; the
  bundle's copies get `duplicate_of`, and every other bundle whose owned set
  changed is re-folded from its stored rows in the same transaction.
- After commit, for every op (design §4.1):
  inv 3 — `incl_<m> = own <m> (owned leaf) + Σ children incl_<m>` for every
  token column, `tok_total`, and every cost column; null when nothing in the
  subtree contributes. Duplicates contribute nothing.
  inv 4 — time from `store.intervals`: own intervals are owned leaves'
  `[start_ms, end_ms]`; `busy_sum_ms = incl_llm_ms + incl_tool_ms` (Σ own
  interval lengths `end_ms − start_ms`, D24; the adapter's `dur_ms` is kept as
  reported and not used), so `busy_sum_ms >= active_ms` by construction;
  `active_ms` = union of own intervals; `wall_ms` = union of the
  subtree's container `segments` (a container without segments contributes its
  `[start_ms, end_ms]`) and own intervals (D19); `blocked_ms`/`idle_ms` from
  the gaps between the own intervals of the op's own transcript (`agent_id`
  equal) against all other own intervals; `max_parallel`. Container
  `start_ms`/`end_ms` widen to cover their subtree.
  inv 6 — `incl_tok_prov` (from owned llm_calls) and `incl_time_prov` (owned
  leaves) are the weakest of the subtree, `missing` when none;
  `cost_lower_bound` = some owned llm_call in the subtree has null cost.
  Counts `n_llm_calls, n_tool_calls, n_engagements, n_errors, n_steps,
  n_duplicates, n_inverted` cover the subtree.
- `pre`/`post` (DM-15) number the op tree: `pre` = 0-based pre-order position (children in stream
  order), `post` = the largest `pre` in the subtree; descendants of X are the ops of its root with `pre`
  between X's `pre` and `post`. A refold keeps them.
- Compact rows (schema v7, DM-8, DM-9, DM-13, DM-14): a content reference is stored as
  (`src_file`, `src_loc`, `src_h`) with `src_files` interned on load and never deleted; `cwd`, `account`,
  `path_sig` are `names` ids; `caused_by` is `caused_by_rowid`; `occ_key` is `occ_hash`; `parent`,
  `session_id`, `bundle`, `project` are not stored on ops (read through `sessions`). `ops.command` and
  `ops.file_path` and `steps.target` are clipped to 160 characters (`refs.clip_preview`), `args_json` is not
  stored; the stream keeps the full values.
- `ops.turn_kind`, `turn_origin`, `user_wait` (DM-40, D54) and `sessions.entrypoint`, `originator` are
  written from the stream.
- B3a derived columns (data-model.md §4): `ops.display_ix` (DM-24: main human/message turns numbered by
  `(start_ms, seq)`); `ops.attr_turn_rowid` on owned leaves and `attr_cost_usd`, `attr_tok_total`,
  `attr_n_llm_calls`, `attr_cost_lower_bound` on main turns (DM-45, `store.derive.attr_walk`; a revisited
  engagement or a `caused_by` that is not a tool_call raises `BundleInvalid`); `steps.skill_load` from
  `skill_invocation` events that name a `step_id` (DM-30; an unknown `step_id` raises `BundleInvalid`);
  `sessions.title` (the stream title), `title_source`, `display_title` (DM-10 levels 1-4, `derive.title`),
  `n_human_asks`, `is_automated` (DM-25), `activity_bin_ms`, `activity_tok` (DM-44); `sessions.first_ms` and
  `last_ms` equal the session op's widened `start_ms`/`end_ms`, which cover every leaf (DM-62).
  `refold` recomputes display_ix, attribution, bins, bounds and rollup_time; it keeps titles, human-ask
  counts and `skill_load` (they do not depend on ownership).
- B3b columns (`store.timesplit`), written by the same code on load and on refold (DM-43), so a load and a
  later refold of the same rows give the same figures: the root's `time_split` rows are deleted and
  inserted: scope `session` on the session op (DM-51), scope `window` on each main turn that holds a window
  and scope `span` on each main turn with both bounds (DM-53), one row per (state, substate) above 0 ms;
  `ops.span_wall_ms` on those turns; `stretches` of the root (DM-52, with `state` and `substate`). The
  inputs come from `timesplit.collect` over the folded rows and the root's stored `notice` events
  (`store.notices`, D55; events are written before the time split), the function the query layer calls on the
  stored rows. A turn with a start but no label (human) or no `display_ix` (non-harness) raises
  AssertionError. `ops.max_silence_ms` (DM-40) is written at load only, for derived-duration
  tool_calls, from the sorted union of the bundle's `source_files.ts_list` (the ops' start and end times
  when no file has one); a refold keeps it.
- `fold(rows, steps_per_turn)` returns `(root, order)`; `order` is the pre-order rows, each carrying
  `start_ms`, `end_ms`, `agent_id`, `kind`, `turn_kind`, `is_async`, `rowid`, `pre`, `post`,
  `duplicate_of` and `owned` (leaf with no `duplicate_of`) for B3b.
- `rollup_time` rows for the root are rebuilt (owned leaves with a start, one row per 15-minute epoch bucket
  `derive.bucket15` and no zone (DM-41), grouped by
  provider, account,
  model, project, agent_type of the nearest engagement; v6 adds the owned
  llm_calls' `n_tok_missing` and `n_time_derived|estimated|missing`, a null
  dur_ms counting as missing; DM-47 `timed_out_tok`, `timed_ms`, `n_ttft`, `ttft_ms_sum`);
  one `sessions` row per
  session op carries its inclusive columns; `events` get `provider`/`project`
  from the session op; `source_files` rows are written with status `live` when
  the stream had a partial trailing line, else `settled`.
- `path_sig` = the agent path to the op (`main` then each enclosing
  engagement's `agent_type or agent_name or "agent"`) plus the op's
  `tool_name or model or kind`, joined by `/`.
- Rowids of op_ids already in the store are reused.
FAILURE BEHAVIOR: validator defects -> `StreamDefects`; tree defects or an
  op_id held by another bundle -> `BundleInvalid`; either way nothing is
  written. A leaf with `end_ms < start_ms` is kept with `end_ms = start_ms`,
  `dur_ms = null`, `time_prov = missing` and `n_inverted = 1`, outside all time
  math.
DOES NOT: read provider files; price (only `store.ingest --reprice` does);
  decide ownership itself; compute projects (`store.projects`, called by ingest).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields

from botsworth import pointer, stream
from botsworth.model import TOKEN_FIELDS, Bundle, Event, Op, SourceFile, Step
from botsworth.store import derive, intervals, notices, ownership, refs, schema, timesplit

log = logging.getLogger(__name__)
MAX_DEPTH = 32  # D18
LEAVES = ("llm_call", "tool_call")
COST_COLUMNS = ("cost_usd", "cost_in_usd", "cost_out_usd", "cost_cache_read_usd", "cost_cache_write_usd")
ADDITIVE = (*TOKEN_FIELDS, "tok_total", *COST_COLUMNS)
INCL = {
    m: "incl_tok_total" if m == "tok_total" else f"incl_{m.removesuffix('_tok')}_tok" for m in TOKEN_FIELDS
}
INCL.update({"tok_total": "incl_tok_total"}, **{c: f"incl_{c}" for c in COST_COLUMNS})
COUNTS = ("n_llm_calls", "n_tool_calls", "n_engagements", "n_errors", "n_steps", "n_duplicates", "n_inverted")
TIME = (
    "incl_llm_ms",
    "incl_tool_ms",
    "wall_ms",
    "active_ms",
    "busy_sum_ms",
    "blocked_ms",
    "idle_ms",
    "max_parallel",
)
DERIVED = (
    "parent_rowid",
    "root_rowid",
    "depth",
    "path_sig",
    "pre",
    "post",
    "start_ms",
    "end_ms",
    "dur_ms",
    "time_prov",
    "duplicate_of",
    "tok_total",
    *INCL.values(),
    *TIME,
    *COUNTS,
    "incl_tok_prov",
    "incl_time_prov",
    "cost_lower_bound",
    "display_ix",  # DM-24
    "attr_turn_rowid",  # DM-45
    "attr_cost_usd",
    "attr_tok_total",
    "attr_n_llm_calls",
    "attr_cost_lower_bound",
)
OP_FIELDS = tuple(f.name for f in fields(Op))
# Op fields that live on `sessions` only (the store has no `ops` column for them)
_SESSION_FIELDS = ("title_source", "entrypoint", "originator")
# the Op fields refold reads back (through `ops_v`): everything `fold` and the rollup need
_REFOLD_FIELDS = tuple(f for f in OP_FIELDS if f not in ("occ_key", "src", "args_json", *_SESSION_FIELDS))
_PREVIEW_OP_FIELDS = ("command", "file_path")
SESSION_INCL = (
    "incl_in_tok",
    "incl_cache_read_tok",
    "incl_cache_write_tok",
    "incl_out_tok",
    "incl_reasoning_tok",
    "incl_tok_total",
    "incl_cost_usd",
    "incl_llm_ms",
    "incl_tool_ms",
    *TIME[2:],
    "n_llm_calls",
    "n_tool_calls",
    "n_engagements",
    "n_errors",
    "n_steps",
    "incl_tok_prov",
    "incl_time_prov",
    "cost_lower_bound",
)
_STRENGTH = {"present": 3, "derived": 2, "estimated": 1, "missing": 0}
ROLLUP_SUMS = (
    "in_tok",
    "out_tok",
    "cache_read_tok",
    "cache_write_tok",
    "reasoning_tok",
    "tok_total",
    "cost_usd",
)


class BundleInvalid(ValueError):
    """A bundle whose tree or identity cannot be stored."""


@dataclass(slots=True)
class LoadResult:
    bundle: str
    ops: int
    flips: int
    promotions: int
    disagreements: int
    refolded: list[str]
    n_inverted: int
    missing_occ_key: int = 0


def bundle_key(bundle: Bundle) -> str:
    return f"{bundle.provider}/{bundle.bundle_id}"


@contextmanager
def write_txn(conn: sqlite3.Connection) -> Iterator[None]:
    assert not conn.in_transaction, "caller already holds a transaction"
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# --- tree -------------------------------------------------------------------------------------------


def _tree(rows: list[dict]) -> tuple[dict, dict[str, list[dict]], list[dict]]:
    """Root, children map, rows in pre-order. Raises BundleInvalid on cycle/depth/disconnection."""
    roots = [r for r in rows if r["parent"] is None]
    if len(roots) != 1 or roots[0]["kind"] != "session":
        raise BundleInvalid(f"need one session root, got {[r['op_id'] for r in roots][:5]}")
    children: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["parent"] is not None:
            children[r["parent"]].append(r)
    order: list[dict] = []
    stack = [(roots[0], 0)]
    seen: set[str] = set()
    while stack:
        r, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise BundleInvalid(f"depth > {MAX_DEPTH} at {r['op_id']}")
        seen.add(r["op_id"])
        r["depth"] = depth
        r["pre"] = len(order)
        order.append(r)
        stack.extend((c, depth + 1) for c in reversed(children[r["op_id"]]))
    if len(seen) != len(rows):
        lost = [r["op_id"] for r in rows if r["op_id"] not in seen]
        raise BundleInvalid(f"cycle or unreachable ops: {lost[:5]}")
    return roots[0], children, order


# --- fold -------------------------------------------------------------------------------------------


def _weakest(values: list[str | None]) -> str:
    known = [v for v in values if v is not None]
    return min(known, key=_STRENGTH.__getitem__) if known else "missing"


def _add(total: float | None, value: float | None) -> float | None:
    if value is None:
        return total
    return value if total is None else total + value


def _own_tok_total(r: dict) -> int | None:
    if r["in_tok"] is None or r["out_tok"] is None:
        return None
    # FALLBACK-OK: design §4.8 cache tiers/writes missing for Codex and omp; absent read/write = 0
    return r["in_tok"] + r["out_tok"] + (r["cache_read_tok"] or 0) + (r["cache_write_tok"] or 0)


def fold(rows: list[dict], steps_per_turn: dict[str, int]) -> tuple[dict, list[dict]]:
    """Compute every DERIVED column in place. `rows` carry record fields, `rowid`,
    `segments` (list) and `duplicate_of` (already decided). Returns (root, pre-order)."""
    root, children, order = _tree(rows)
    by_id = {r["op_id"]: r for r in rows}
    for r in order:  # pre-order: parents before children
        parent = by_id.get(r["parent"]) if r["parent"] is not None else None
        r["parent_rowid"] = parent["rowid"] if parent else None
        r["root_rowid"] = root["rowid"]
        if r["kind"] == "engagement":
            agent = r["agent_type"] or r["agent_name"] or "agent"  # FALLBACK-OK: design §4.2 type optional
            r["_agents"] = (*parent["_agents"], agent)
            r["_agent_type"] = r["agent_type"]
        else:
            r["_agents"] = parent["_agents"] if parent else ("main",)
            r["_agent_type"] = parent["_agent_type"] if parent else None
        r["path_sig"] = "/".join((*r["_agents"], r["tool_name"] or r["model"] or r["kind"]))
    for r in reversed(order):  # DM-15: post = the largest pre in the subtree
        r["post"] = max([r["pre"], *(k["post"] for k in children[r["op_id"]])])
    # post-order fold; each node collects (own-agent intervals, all intervals, segments) of its subtree
    acc: dict[str, tuple[list, list, list]] = {}
    for r in reversed(order):
        kids = children[r["op_id"]]
        leaf = r["kind"] in LEAVES
        owned = leaf and r["duplicate_of"] is None
        inverted = False
        if leaf:
            r["tok_total"] = _own_tok_total(r) if r["kind"] == "llm_call" else None
            # a refold sees end = start already; the previous fold's count (own + subtree) keeps the flag
            stored = r.get("n_inverted")
            inverted = (
                r["start_ms"] is not None and r["end_ms"] is not None and r["end_ms"] < r["start_ms"]
            ) or (stored is not None and stored > sum(k["n_inverted"] for k in kids))
            if inverted:
                r["end_ms"], r["dur_ms"], r["time_prov"] = r["start_ms"], None, "missing"
        else:
            r["tok_total"] = None
        mine: list[tuple[int, int, str | None]] = []
        if owned and not inverted and r["start_ms"] is not None and r["end_ms"] is not None:
            mine.append((r["start_ms"], r["end_ms"], r["agent_id"]))
        segs: list[tuple[int, int]] = []
        if not leaf and r["segments"]:
            segs += [(a, b) for a, b in r["segments"] if b >= a]
        for k in kids:
            k_ivs, k_segs = acc.pop(k["op_id"])
            mine += k_ivs
            segs += k_segs
        acc[r["op_id"]] = (mine, segs)
        for m in ADDITIVE:
            total = r[m] if owned else None
            for k in kids:
                total = _add(total, k[INCL[m]])
            r[INCL[m]] = total
        own_dur = 0  # D24: busy time from the leaf interval, never the adapter's dur_ms
        if owned and not inverted and r["start_ms"] is not None and r["end_ms"] is not None:
            own_dur = r["end_ms"] - r["start_ms"]
        r["incl_llm_ms"] = (own_dur if r["kind"] == "llm_call" else 0) + sum(k["incl_llm_ms"] for k in kids)
        r["incl_tool_ms"] = (own_dur if r["kind"] == "tool_call" else 0) + sum(
            k["incl_tool_ms"] for k in kids
        )
        r["busy_sum_ms"] = r["incl_llm_ms"] + r["incl_tool_ms"]
        # `_lo`/`_hi` = extent of the whole subtree, carried up through leaves (agent under a tool_call).
        # DM-62: every container takes it, so sessions.first_ms/last_ms and engagements cover every leaf.
        # DM-53, D38 H1: a `turn` op keeps the stream's own start/end (agent time after the turn's end is a
        # Waterfall tail); its extent still goes up to its parents.
        spans = (
            [(r["start_ms"], r["end_ms"])] if r["start_ms"] is not None and r["end_ms"] is not None else []
        )
        spans += [(k["_lo"], k["_hi"]) for k in kids if k["_lo"] is not None]
        r["_lo"] = min((a for a, _ in spans), default=None)
        r["_hi"] = max((b for _, b in spans), default=None)
        if spans and not leaf and r["kind"] != "turn":
            r["start_ms"], r["end_ms"] = r["_lo"], r["_hi"]
        if (
            not leaf
            and not r["segments"]
            and r["start_ms"] is not None
            and r["end_ms"] is not None
            and r["end_ms"] >= r["start_ms"]
        ):
            # FALLBACK-OK: design §4.1 inv 4 — a file without resume = its single [start, end]. Taken after
            # the widening above, so a load and a refold (which reads the stored, widened bounds) agree.
            segs.append((r["start_ms"], r["end_ms"]))
        ivs = [(a, b) for a, b, _ in mine]
        r["active_ms"] = intervals.union_ms(ivs)
        r["max_parallel"] = intervals.max_parallel(ivs)
        r["wall_ms"] = intervals.wall_ms(segs, ivs)
        own_agent = [(a, b) for a, b, ag in mine if ag == r["agent_id"]]
        others = [(a, b) for a, b, ag in mine if ag != r["agent_id"]]
        r["blocked_ms"], r["idle_ms"] = intervals.blocked_idle(own_agent, others)
        r["n_llm_calls"] = int(owned and r["kind"] == "llm_call") + sum(k["n_llm_calls"] for k in kids)
        r["n_tool_calls"] = int(owned and r["kind"] == "tool_call") + sum(k["n_tool_calls"] for k in kids)
        r["n_engagements"] = int(r["kind"] == "engagement") + sum(k["n_engagements"] for k in kids)
        r["n_errors"] = int(owned and bool(r["is_error"])) + sum(k["n_errors"] for k in kids)
        r["n_steps"] = steps_per_turn.get(r["op_id"], 0) + sum(k["n_steps"] for k in kids)
        r["n_duplicates"] = int(leaf and not owned) + sum(k["n_duplicates"] for k in kids)
        r["n_inverted"] = int(inverted) + sum(k["n_inverted"] for k in kids)
        tok = [r["tok_prov"]] if owned and r["kind"] == "llm_call" else []
        tim = [r["time_prov"]] if owned else []
        tok += [k["_tok_provs"] for k in kids if k["_tok_provs"] is not None]
        tim += [k["_time_provs"] for k in kids if k["_time_provs"] is not None]
        r["_tok_provs"] = _weakest(tok) if tok else None
        r["_time_provs"] = _weakest(tim) if tim else None
        r["incl_tok_prov"] = r["_tok_provs"] or "missing"
        r["incl_time_prov"] = r["_time_provs"] or "missing"
        r["cost_lower_bound"] = int(
            (owned and r["kind"] == "llm_call" and r["cost_usd"] is None)
            or any(k["cost_lower_bound"] for k in kids)
        )
        r["owned"] = owned
    _display_ix(order)
    _attribute(order)
    return root, order


def _display_ix(order: list[dict]) -> None:
    """DM-24: number the main human/message turns in time order; every other op gets NULL."""
    turns = [r for r in order if r["kind"] == "turn" and r["agent_id"] is None]
    keys = [(t["turn_kind"], t["start_ms"], t["seq"] if t["seq"] is not None else t["pre"]) for t in turns]
    for r in order:
        r["display_ix"] = None
    for t, ix in zip(turns, derive.display_ix(keys), strict=True):
        t["display_ix"] = ix


def _attribute(order: list[dict]) -> None:
    """DM-45: attributed turn of every owned leaf, and the per-turn sums over its owned llm_calls."""
    for r in order:
        main = r["kind"] == "turn" and r["agent_id"] is None
        r["attr_turn_rowid"] = None
        r["attr_cost_usd"] = r["attr_tok_total"] = None
        r["attr_n_llm_calls"] = 0 if main else None
        r["attr_cost_lower_bound"] = 0 if main else None
    try:
        walk = derive.attr_walk(order)
    except derive.AttributionError as exc:
        raise BundleInvalid(str(exc)) from exc
    turns = {r["rowid"]: r for r in order if r["attr_n_llm_calls"] is not None}
    for r in order:
        if r["op_id"] not in walk:
            continue
        r["attr_turn_rowid"] = walk[r["op_id"]]
        if r["kind"] != "llm_call" or r["attr_turn_rowid"] is None:
            continue
        t = turns[r["attr_turn_rowid"]]
        t["attr_n_llm_calls"] += 1
        t["attr_cost_usd"] = _add(t["attr_cost_usd"], r["cost_usd"])
        t["attr_tok_total"] = _add(t["attr_tok_total"], r["tok_total"])
        t["attr_cost_lower_bound"] |= int(r["cost_usd"] is None)


def _rollup_rows(root: dict, order: list[dict]) -> list[tuple]:
    groups: dict[tuple, dict] = {}
    for r in order:
        if r["kind"] not in LEAVES or r["duplicate_of"] is not None or r["start_ms"] is None:
            continue
        dims = (
            r["provider"],
            r["account"] or root["account"],  # FALLBACK-OK: design §4.11 account is the session's
            r["model"],
            r["project"] or root["project"],  # FALLBACK-OK: design §4.2 project = session's first cwd
            r["_agent_type"],
        )
        g = groups.setdefault((derive.bucket15(r["start_ms"]), *dims), defaultdict(lambda: None))
        llm = r["kind"] == "llm_call"
        g["n_llm_calls"] = (g["n_llm_calls"] or 0) + llm
        g["n_tool_calls"] = (g["n_tool_calls"] or 0) + (not llm)
        for m in ROLLUP_SUMS:
            g[m] = _add(g[m], r[m])
        g["n_unpriced"] = (g["n_unpriced"] or 0) + (llm and r["cost_usd"] is None)
        # v6 provenance counts (owned llm_calls): weakest token/time prov of a group without an op scan
        tok_missing = llm and (r["tok_prov"] == "missing" or r["tok_total"] is None)
        g["n_tok_missing"] = (g["n_tok_missing"] or 0) + tok_missing
        tprov = "missing" if r["dur_ms"] is None else r["time_prov"]
        for p in ("derived", "estimated", "missing"):
            g[f"n_time_{p}"] = (g[f"n_time_{p}"] or 0) + (llm and tprov == p)
        ms = "llm_ms" if llm else "tool_ms"
        g[ms] = _add(g[ms], r["dur_ms"])
        g["n_errors"] = (g["n_errors"] or 0) + bool(r["is_error"])
        if r["dur_ms"] is not None:
            g["max_dur_ms"] = max(g["max_dur_ms"] or 0, r["dur_ms"])
        # DM-47: speed and first-token sums over owned llm_calls, so period queries do not scan ops
        timed = llm and r["dur_ms"] is not None and r["dur_ms"] > 0 and r["out_tok"] is not None
        if timed:
            g["timed_out_tok"] = _add(g["timed_out_tok"], r["out_tok"])
            g["timed_ms"] = _add(g["timed_ms"], r["dur_ms"])
        g["n_ttft"] = (g["n_ttft"] or 0) + (llm and r["ttft_ms"] is not None)
        if llm and r["ttft_ms"] is not None:
            g["ttft_ms_sum"] = _add(g["ttft_ms_sum"], r["ttft_ms"])
    cols = ("n_llm_calls", "n_tool_calls", *ROLLUP_SUMS[:5], "tok_total", "cost_usd", "n_unpriced")
    cols += ("llm_ms", "tool_ms", "n_errors", "max_dur_ms")
    cols += ("n_tok_missing", "n_time_derived", "n_time_estimated", "n_time_missing")
    cols += ("timed_out_tok", "timed_ms", "n_ttft", "ttft_ms_sum")
    return [(root["rowid"], *k, *(g[c] for c in cols)) for k, g in groups.items()]


_ROLLUP_INSERT = (
    "INSERT INTO rollup_time(root_rowid, bucket_start_ms, provider, account, model,"
    " project,"
    " agent_type, n_llm_calls, n_tool_calls, in_tok, out_tok, cache_read_tok, cache_write_tok, reasoning_tok,"
    " tok_total, cost_usd, n_unpriced, llm_ms, tool_ms, n_errors, max_dur_ms,"
    " n_tok_missing, n_time_derived, n_time_estimated, n_time_missing, timed_out_tok, timed_ms, n_ttft,"
    " ttft_ms_sum)"
    f" VALUES ({','.join('?' * 29)})"
)


# --- writes -----------------------------------------------------------------------------------------


def _op_row(op: Op) -> dict:
    d = {name: getattr(op, name) for name in OP_FIELDS}
    for flag in ("is_fork", "is_async", "cut_off", "is_error", "interrupted"):
        if d[flag] is not None:
            d[flag] = int(d[flag])
    return d


def _root_rowid(conn: sqlite3.Connection, bundle: str) -> int | None:
    row = conn.execute("SELECT rowid FROM sessions WHERE bundle=?", (bundle,)).fetchone()
    return None if row is None else row[0]


def _delete(conn: sqlite3.Connection, bundle: str) -> None:
    root = _root_rowid(conn, bundle)
    if root is not None:  # every child table is deleted through its indexed root, not a scan
        for table in ("rollup_time", "stretches", "time_split", "steps", "events", "ops"):
            conn.execute(f"DELETE FROM {table} WHERE root_rowid=?", (root,))
    conn.execute("DELETE FROM sessions WHERE bundle=?", (bundle,))
    conn.execute("DELETE FROM source_files WHERE bundle_id=?", (bundle,))


_OP_COLS = tuple(c for c in schema.OP_TABLE_COLUMNS if c != "rowid")
_SEGMENTS_AT = _OP_COLS.index("segments")


class _Refs:
    """Per-load cache of interned `src_files` and `names` ids (one SELECT/INSERT per distinct value)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.files: dict[str, tuple[int, str, bytes | None]] = {}
        self.names: dict[str | None, int | None] = {}

    def src(self, uri: str) -> tuple[int, str, bytes | None]:
        """`(src_file, src_loc, src_h)` of a content reference."""
        try:
            provider, relpath, prefix, loc, h = refs.to_parts(uri)
        except (pointer.PointerError, ValueError) as exc:
            raise BundleInvalid(f"content reference {uri!r}: {exc}") from exc
        fid = self.files.get(prefix)
        if fid is None:
            fid = self.files[prefix] = refs.intern_file(self.conn, provider, relpath, prefix)
        return fid, loc, h

    def name(self, s: str | None) -> int | None:
        if s not in self.names:
            self.names[s] = refs.intern_name(self.conn, s)
        return self.names[s]


def _compact(rows: list[dict], cache: _Refs) -> None:
    """Replace each row's v6 text columns by their v7 ids, hashes and previews (DM-8, DM-9, DM-14)."""
    by_id = {r["op_id"]: r for r in rows}
    for r in rows:
        r["src_file"], r["src_loc"], r["src_h"] = cache.src(r["src"])
        r["cwd_id"] = cache.name(r["cwd"])
        r["account_id"] = cache.name(r["account"])
        r["path_sig_id"] = cache.name(r["path_sig"])
        r["occ_hash"] = refs.occ_hash(r["occ_key"]) if r["occ_key"] is not None else None
        r["caused_by_rowid"] = by_id[r["caused_by"]]["rowid"] if r["caused_by"] is not None else None
        for name in _PREVIEW_OP_FIELDS:
            r[name] = refs.clip_preview(r[name])


def _insert_ops(conn: sqlite3.Connection, rows: list[dict]) -> None:
    sql = f"INSERT INTO ops(rowid, {','.join(_OP_COLS)}) VALUES ({','.join('?' * (len(_OP_COLS) + 1))})"
    out = []
    for r in rows:
        # FALLBACK-OK: data-model.md §2.2 — the columns a B3 task fills (attr_*, span_*, display_ix, ...) are
        # not row keys yet and are stored NULL
        vals = [r.get(c) for c in _OP_COLS]
        vals[_SEGMENTS_AT] = json.dumps(r["segments"]) if r["segments"] is not None else None
        out.append([r["rowid"], *vals])
    try:
        conn.executemany(sql, out)
    except sqlite3.IntegrityError as exc:
        raise BundleInvalid(
            f"{rows[0]['op_id']}: {exc} (op_id held by another bundle or duplicate owner)"
        ) from exc


_SESSION_COLS = (
    "rowid",
    "op_rowid",
    "bundle",
    "provider",
    "session_id",
    "project",
    "title",
    "git_branch",
    "harness_version",
    "entrypoint",
    "originator",
    "first_line_ms",
    "account",
    "cwd_first",
    "first_ms",
    "last_ms",
    "status",
    "n_malformed",
    "n_duplicates",
    *SESSION_INCL,
    "title_source",
    "display_title",
    "n_human_asks",
    "is_automated",
    "activity_bin_ms",
    "activity_tok",
)


def _session_derived(root: dict, order: list[dict]) -> dict:
    """Session columns that come from the folded rows: title (DM-10), human asks (DM-25), bins (DM-44)."""
    asks = [r for r in order if r["kind"] == "turn" and r["agent_id"] is None and r["turn_kind"] == "human"]
    asks.sort(
        key=lambda r: (r["start_ms"] is None, r["start_ms"], r["pre"])
    )  # DM-10: first ask in time order
    display, source = derive.title(root["title"], root["title_source"], [a["label"] for a in asks])
    return {
        "title_source": source,
        "display_title": display,
        "n_human_asks": len(asks),
        "is_automated": int(not asks),
        **_activity(root, order),
    }


def _activity(root: dict, order: list[dict]) -> dict:
    """DM-44: per-session activity bins over `[first_ms, last_ms]` from the owned llm_calls."""
    if root["start_ms"] is None or root["end_ms"] is None:
        return {"activity_bin_ms": None, "activity_tok": None}
    calls = [
        (r["start_ms"], r["tok_total"])
        for r in order
        if r["kind"] == "llm_call" and r["owned"] and r["start_ms"] is not None
    ]
    bin_ms, bins = derive.activity_bins(root["start_ms"], root["end_ms"], calls)
    return {"activity_bin_ms": bin_ms, "activity_tok": json.dumps(bins)}


def _session_row(root: dict, bundle: str, status: str, n_malformed: int, derived: dict) -> tuple:
    head = (root["rowid"], root["rowid"], bundle, root["provider"], root["session_id"], root["project"])
    head += (root["title"], root["git_branch"], root["harness_version"])
    head += (root["entrypoint"], root["originator"], root["first_line_ms"])
    head += (
        root["account"],
        root["cwd"],
        root["start_ms"],  # DM-62: the fold widened the root over every leaf and container
        root["end_ms"],
        status,
        n_malformed,
        root["n_duplicates"],
    )
    return head + tuple(root[c] for c in SESSION_INCL) + tuple(derived[c] for c in _SESSION_COLS[-6:])


def _write_session(conn: sqlite3.Connection, row: tuple) -> None:
    sql = f"INSERT INTO sessions({','.join(_SESSION_COLS)}) VALUES ({','.join('?' * len(_SESSION_COLS))})"
    conn.execute(sql, row)


def _write_time(conn: sqlite3.Connection, root: dict, order: list[dict]) -> None:
    """Write the DM-51 session and window rows, the DM-53 span rows and `span_wall_ms` on main turns, and the
    DM-52 stretches of one root, replacing the previous values (load and refold, DM-43)."""
    rowid = root["rowid"]
    inp = timesplit.collect(order, notices.stored(conn, rowid))  # the events of the root are already written
    sp = timesplit.split(
        inp.wall,
        inp.main_llm,
        inp.main_tool,
        inp.agent_leaf,
        inp.waits,
        inp.turns,
        inp.main_bg,
        inp.stalls,
        inp.classes,
    )
    conn.execute("UPDATE ops SET span_wall_ms=NULL WHERE root_rowid=? AND kind='turn'", (rowid,))
    conn.executemany(
        "UPDATE ops SET span_wall_ms=? WHERE rowid=?",
        [(t.end_ms - t.start_ms, t.rowid) for t in inp.turns if t.end_ms is not None],
    )
    conn.execute("DELETE FROM time_split WHERE root_rowid=?", (rowid,))
    scoped = [("session", rowid, sp.session)]
    scoped += [("window", turn, acc) for turn, acc in sp.windows.items()]
    scoped += [("span", turn, acc) for turn, acc in sp.spans.items()]
    conn.executemany(
        "INSERT INTO time_split(root_rowid, op_rowid, scope, state, substate, ms) VALUES (?,?,?,?,?,?)",
        [
            (rowid, op, scope, state, sub, ms)
            for scope, op, acc in scoped
            for state, sub, ms in timesplit.ordered(acc)
        ],
    )
    conn.execute("DELETE FROM stretches WHERE root_rowid=?", (rowid,))
    conn.executemany(
        "INSERT INTO stretches(root_rowid, turn_rowid, state, substate, start_ms, end_ms)"
        " VALUES (?,?,?,?,?,?)",
        [(rowid, *st) for st in sp.stretches],
    )


def _write_max_silence(conn: sqlite3.Connection, bundle: Bundle, order: list[dict]) -> None:
    """DM-40: `max_silence_ms` on every derived-duration tool_call, from the bundle's record timestamps."""
    stamps = sorted({t for f in bundle.source_files for t in (f.ts_list or ())})
    if not stamps:
        # FALLBACK-OK: DM-40 — a bundle without `ts_list` uses the start and end of its ops and steps
        stamps = sorted({t for o in bundle.ops for t in (o.start_ms, o.end_ms) if t is not None})
    calls = [
        (r["op_id"], r["start_ms"], r["end_ms"])
        for r in order
        if r["kind"] == "tool_call"
        and r["time_prov"] == "derived"
        and r["start_ms"] is not None
        and r["end_ms"] is not None
    ]
    by_id = {r["op_id"]: r["rowid"] for r in order}
    conn.executemany(
        "UPDATE ops SET max_silence_ms=? WHERE rowid=?",
        [(ms, by_id[op_id]) for op_id, ms in timesplit.max_silence(calls, stamps).items()],
    )


def _skill_loads(bundle: Bundle) -> dict[str, str]:
    """DM-30: `{step_id: skill}` from the bundle's `skill_invocation` events that name a step."""
    steps = {f"{s.turn_id}#{s.ordinal}" for s in bundle.steps}
    out: dict[str, str] = {}
    for e in bundle.events:
        # FALLBACK-OK: DM-30 — a skill event without `step_id` (slash command, turn without a step) sets no
        # `skill_load`; its turn op is the anchor
        step_id = e.fields.get("step_id") if e.kind == "skill_invocation" else None
        if step_id is None:
            continue
        if step_id not in steps:
            raise BundleInvalid(f"{e.event_id}: step_id {step_id} is not a step of the bundle")
        out.setdefault(step_id, e.fields["skill"])
    return out


def _step_row(s: Step, root_rowid: int, by_id: dict[str, dict], cache: _Refs, loads: dict[str, str]) -> tuple:
    subs = None
    if s.sub_items is not None:
        subs = json.dumps([{"tool": x.tool, "target": x.target, "outcome": x.outcome} for x in s.sub_items])
    src_file, src_loc, src_h = cache.src(s.src)
    return (
        root_rowid,
        by_id[s.turn_id]["rowid"],
        s.ordinal,
        by_id[s.op_id]["rowid"],
        s.verb,
        s.tool_name,
        refs.clip_preview(s.target),
        s.outcome,
        s.summary,
        refs.clip_preview(s.snippet),
        s.start_ms,
        s.dur_ms,
        s.skill,
        loads.get(f"{s.turn_id}#{s.ordinal}"),
        subs,
        src_file,
        src_loc,
        src_h,
    )


_STEP_INSERT = (
    "INSERT INTO steps(root_rowid, turn_rowid, ordinal, op_rowid, verb, tool_name, target, outcome,"
    " summary, snippet, start_ms, dur_ms, skill, skill_load, sub_items, src_file, src_loc,"
    f" src_h) VALUES ({','.join('?' * 18)})"
)


def _event_row(e: Event, root: dict, dups: set[str], cache: _Refs) -> tuple:
    copied = e.op_id in dups or bool(e.fields.get("in_copied_op"))
    src_file, src_loc, src_h = cache.src(e.src)
    return (root["rowid"], e.event_id, e.kind, e.ts_ms, e.session_id, e.op_id, root["provider"]) + (
        e.account,
        root["project"],
        e.basis,
        json.dumps(e.fields, ensure_ascii=False),
        src_file,
        src_loc,
        src_h,
        int(copied),
    )


_EVENT_INSERT = (
    "INSERT INTO events(root_rowid, event_id, kind, ts_ms, session_id, op_id, provider,"
    " account, project, basis, fields, src_file, src_loc, src_h, copied)"
    f" VALUES ({','.join('?' * 15)})"
)

_EVENT_COPIED = (
    "UPDATE events SET copied = (op_id IN (SELECT op_id FROM ops WHERE root_rowid=?1"
    " AND duplicate_of IS NOT NULL) OR coalesce(json_extract(fields, '$.in_copied_op'), 0) != 0)"
    " WHERE root_rowid=?1"
)


def _source_row(f: SourceFile, bundle: str, now_ms: int, status: str) -> tuple:
    return (f.path, f.provider, bundle, f.size, f.mtime_ns, f.sha_first_4k, now_ms, status)


def load_bundle(conn: sqlite3.Connection, bundle: Bundle) -> LoadResult:
    stream.validate(bundle)
    key = bundle_key(bundle)
    rows = [_op_row(o) for o in bundle.ops]
    missing_occ = 0
    for r in rows:
        if r["kind"] in LEAVES and r["occ_key"] is None:
            # FALLBACK-OK: T4 brief (Kai) — leaves without occ_key: a declared copy shares its owner's
            # key, else the op's own id; counted and logged until adapters emit occ_key everywhere
            r["occ_key"] = r["duplicate_of"] or r["op_id"]
            missing_occ += 1
        r["occ_hash"] = refs.occ_hash(r["occ_key"]) if r["occ_key"] is not None else None
    if missing_occ:
        log.warning("%s: %d leaves without occ_key (duplicate_of fallback)", bundle_key(bundle), missing_occ)
    for r in rows:
        r["rowid"] = None
    _tree(rows)  # reject before opening the transaction
    root_op = next(r for r in rows if r["kind"] == "session")
    first_ms = root_op[
        "start_ms"
    ]  # the adapter's session start: ownership order uses it, not the widened one
    loads = _skill_loads(bundle)
    steps_per_turn: dict[str, int] = defaultdict(int)
    for s in bundle.steps:
        steps_per_turn[s.turn_id] += 1
    status = "live" if bundle.n_partial else "settled"
    now_ms = int(time.time() * 1000)
    with write_txn(conn):
        old_root = _root_rowid(conn, key)
        existing = (
            {}
            if old_root is None
            else dict(conn.execute("SELECT op_id, rowid FROM ops WHERE root_rowid=?", (old_root,)))
        )
        previously = ownership.owned_keys(conn, key)
        _delete(conn, key)
        # the bundle's own old rowids are reused below, so new ones must start above them too, even when
        # they were the highest in the table (a reloaded, grown bundle that was loaded last)
        high = max(
            conn.execute("SELECT coalesce(max(rowid), 0) FROM ops").fetchone()[0], *existing.values(), 0
        )
        for r in rows:
            if r["op_id"] in existing:
                r["rowid"] = existing[r["op_id"]]
            else:
                high += 1
                r["rowid"] = high
        session = ownership.session_order(root_op["first_line_ms"], first_ms)
        decision = ownership.resolve(conn, key, session, rows, previously)
        ownership.apply(conn, decision)
        for r in rows:
            r["duplicate_of"] = decision.dup_of.get(r["op_id"])  # non-leaves and keyless leaves own
        root, order = fold(rows, steps_per_turn)
        dups = {r["op_id"] for r in rows if r["duplicate_of"] is not None}
        by_id = {r["op_id"]: r for r in rows}
        cache = _Refs(conn)
        _compact(rows, cache)
        _insert_ops(conn, rows)
        derived = _session_derived(root, order)
        _write_session(conn, _session_row(root, key, status, bundle.n_malformed, derived))
        conn.executemany(_ROLLUP_INSERT, _rollup_rows(root, order))
        conn.executemany(_EVENT_INSERT, [_event_row(e, root, dups, cache) for e in bundle.events])
        _write_time(conn, root, order)  # reads the root's `notice` events (DM-41)
        _write_max_silence(conn, bundle, order)
        conn.executemany(
            _STEP_INSERT, [_step_row(s, root["rowid"], by_id, cache, loads) for s in bundle.steps]
        )
        conn.executemany(
            "INSERT OR REPLACE INTO source_files(path, provider, bundle_id, size, mtime_ns, sha_first_4k,"
            " ingested_at_ms, status) VALUES (?,?,?,?,?,?,?,?)",
            [_source_row(f, key, now_ms, status) for f in bundle.source_files],
        )
        refolded = sorted(decision.affected)
        for other in refolded:
            refold(conn, other)
    return LoadResult(
        bundle=key,
        ops=len(rows),
        flips=decision.flips,
        promotions=decision.promotions,
        disagreements=decision.disagreements,
        refolded=refolded,
        n_inverted=root["n_inverted"],
        missing_occ_key=missing_occ,
    )


def refold(conn: sqlite3.Connection, bundle: str) -> None:
    """Recompute derived columns, rollup_time and the sessions row of a stored bundle from its
    stored rows (ownership already applied). Runs inside the caller's
    transaction. Titles, `n_human_asks` and `skill_load` do not depend on ownership and are kept."""
    assert conn.in_transaction, "refold must run inside the load transaction"
    root_rowid = _root_rowid(conn, bundle)
    assert root_rowid is not None, f"refold of an unknown bundle {bundle}"
    cur = conn.execute(
        f"SELECT rowid, {','.join(_REFOLD_FIELDS)}, n_inverted FROM ops_v WHERE root_rowid=? ORDER BY rowid",
        (root_rowid,),
    )
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, v, strict=True)) for v in cur]
    for r in rows:
        r["segments"] = json.loads(r["segments"]) if r["segments"] is not None else None
    steps_per_turn = dict(
        conn.execute(
            "SELECT t.op_id, count(*) FROM steps s JOIN ops t ON t.rowid = s.turn_rowid"
            " WHERE s.root_rowid=? GROUP BY s.turn_rowid",
            (root_rowid,),
        )
    )
    root, order = fold(rows, steps_per_turn)
    kept = ("parent_rowid", "root_rowid", "depth", "path_sig", "pre", "post")  # the tree does not change
    cols = [c for c in DERIVED if c not in kept]
    conn.executemany(
        f"UPDATE ops SET {','.join(c + '=?' for c in cols)} WHERE rowid=?",
        [[r[c] for c in cols] + [r["rowid"]] for r in rows],
    )
    conn.execute(_EVENT_COPIED, (root["rowid"],))
    conn.execute("DELETE FROM rollup_time WHERE root_rowid=?", (root["rowid"],))
    conn.executemany(_ROLLUP_INSERT, _rollup_rows(root, order))
    _write_time(conn, root, order)  # DM-43: the time split follows the same fold as rollup_time
    act = _activity(root, order)
    conn.execute(
        "UPDATE sessions SET first_ms=?, last_ms=?, n_duplicates=?, activity_bin_ms=?, activity_tok=?, "
        + ",".join(c + "=?" for c in SESSION_INCL)
        + " WHERE rowid=?",
        (
            root["start_ms"],
            root["end_ms"],
            root["n_duplicates"],
            act["activity_bin_ms"],
            act["activity_tok"],
            *(root[c] for c in SESSION_INCL),
            root["rowid"],
        ),
    )
