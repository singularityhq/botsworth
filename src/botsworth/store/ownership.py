"""CONTRACT: corpus-wide occurrence ownership — the single enforcer of invariant 5 (design §4.1, §4.4).

GUARANTEES:
- For every `(provider, kind, occ_hash)` of a leaf (`llm_call`, `tool_call`) there is
  exactly one owner in the store after any sequence of bundle loads. The owner
  is the candidate with the smallest `rank`: (session `first_line_ms`, falling
  back to the session `start_ms` when null (D23); session `start_ms`; spawn_depth,
  non-fork before fork, first ts of the op's own transcript, bundle key,
  op_id). Missing timestamps rank last. Every other candidate carries
  `duplicate_of = <owner op_id>` and is excluded from every rollup by the fold.
- `resolve` decides ownership for one bundle being loaded against every other
  bundle in the store, including keys the bundle owned before its reload and
  no longer contains (a remaining copy is promoted). The decision depends only
  on the set of loaded bundles, never on load order (F5 both orders).
- `apply` writes the decision to the other bundles' rows: losers first, then
  promoted owners, so the unique `owner_key` index holds at every statement.
  `Decision.affected` names the other bundles whose owned set changed (a flip
  or a promotion); their rollups must be recomputed in the same transaction.
- An adapter's `duplicate_of` is a hint only: a non-null hint that differs from
  the decision is counted in `Decision.disagreements` and logged. A null hint is
  not a claim (adapters do not see other bundles).
EXPECTS: leaves carry `occ_hash` (DM-14; leaves without one are never duplicates); the
  bundle's previous rows are already deleted when `resolve` runs; the caller
  holds the write transaction.
FAILURE BEHAVIOR: sqlite3 errors propagate (the caller rolls back).
DOES NOT: compute rollups; read stream or provider files.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

LEAVES = ("llm_call", "tool_call")
Key = tuple[str, str, bytes]  # provider, kind, occ_hash (refs.occ_hash of the occ_key, DM-14)
_CHUNK = 500


def _ts(value: int | None) -> float:
    return math.inf if value is None else value  # FALLBACK-OK: design §4.6 time may be missing; ranks last


def session_order(first_line_ms: int | None, start_ms: int | None) -> tuple:
    """D23: the main file's first timestamped line, then the session start (null first line -> start)."""
    return (_ts(first_line_ms if first_line_ms is not None else start_ms), _ts(start_ms))


def rank(
    session: tuple,
    spawn_depth: int | None,
    is_fork: bool | int | None,
    transcript_first_ms: int | None,
    bundle: str,
    op_id: str,
) -> tuple:
    """Invariant 5 owner order; smaller wins."""
    depth = spawn_depth or 0  # FALLBACK-OK: design §4.2 spawn_depth absent on the main transcript = 0
    return (*session, depth, bool(is_fork), _ts(transcript_first_ms), bundle, op_id)


@dataclass(slots=True)
class Candidate:
    op_id: str
    bundle: str
    rank: tuple
    duplicate_of: str | None  # current value (store) or adapter hint (new bundle)


@dataclass(slots=True)
class Decision:
    dup_of: dict[str, str | None] = field(default_factory=dict)  # new bundle op_id -> owner op_id
    updates: list[tuple[str, str | None]] = field(default_factory=list)  # other bundles: op_id -> dup
    affected: set[str] = field(default_factory=set)
    flips: int = 0  # store owners demoted by the new bundle
    promotions: int = 0  # store copies promoted to owner
    disagreements: int = 0


def key_of(op: dict) -> Key | None:
    if op["kind"] not in LEAVES or op.get("occ_hash") is None:
        return None
    return (op["provider"], op["kind"], op["occ_hash"])


def owned_keys(conn: sqlite3.Connection, bundle: str) -> set[Key]:
    rows = conn.execute(
        "SELECT o.provider, o.kind, o.occ_hash"
        # INDEXED BY: a store without sqlite_stat1 (any rebuild until its closing ANALYZE) otherwise scans
        # every leaf of the store through ops_kind_cost for each bundle (3 s a bundle at 580k ops)
        " FROM ops o INDEXED BY ops_root_kind"
        " WHERE o.root_rowid IN (SELECT rowid FROM sessions WHERE bundle=?)"
        " AND o.kind IN ('llm_call','tool_call')"
        " AND o.duplicate_of IS NULL AND o.occ_hash IS NOT NULL",
        (bundle,),
    )
    return {tuple(r) for r in rows}


def _store_candidates(conn: sqlite3.Connection, keys: set[Key], bundle: str) -> dict[Key, list[Candidate]]:
    by_occ = defaultdict(set)
    for provider, kind, occ in keys:
        by_occ[occ].add((provider, kind))
    occs = list(by_occ)
    rows = []
    for i in range(0, len(occs), _CHUNK):
        chunk = occs[i : i + _CHUNK]
        rows += conn.execute(
            "SELECT o.op_id, o.provider, o.kind, o.occ_hash, o.spawn_depth, o.is_fork, o.agent_id,"
            # INDEXED BY: with bound parameters the planner otherwise scans ops_kind_cost (100x slower)
            " s.bundle, o.duplicate_of, s.first_line_ms, s.first_ms, o.root_rowid"
            " FROM ops o INDEXED BY ops_occ"
            " JOIN sessions s ON s.rowid = o.root_rowid"
            f" WHERE o.occ_hash IN ({','.join('?' * len(chunk))}) AND o.kind IN ('llm_call','tool_call')"
            " AND s.bundle != ?",
            [*chunk, bundle],
        ).fetchall()
    firsts: dict[tuple[int, str | None], int | None] = {}
    out: dict[Key, list[Candidate]] = defaultdict(list)
    for op_id, provider, kind, occ, depth, fork, agent, other, dup, first_line_ms, first_ms, root in rows:
        if (provider, kind) not in by_occ[occ]:
            continue
        if (root, agent) not in firsts:
            firsts[(root, agent)] = conn.execute(
                "SELECT min(start_ms) FROM ops WHERE root_rowid=? AND agent_id IS ?", (root, agent)
            ).fetchone()[0]
        r = rank(session_order(first_line_ms, first_ms), depth, fork, firsts[(root, agent)], other, op_id)
        out[(provider, kind, occ)].append(Candidate(op_id, other, r, dup))
    return out


def transcript_first_ms(ops: Iterable[dict]) -> dict[str | None, int | None]:
    """First timestamp per transcript (`agent_id`) of one bundle."""
    out: dict[str | None, int | None] = {}
    for op in ops:
        a, t = op.get("agent_id"), op.get("start_ms")
        if a not in out or (t is not None and (out[a] is None or t < out[a])):
            out[a] = t
    return out


def resolve(
    conn: sqlite3.Connection,
    bundle: str,
    session: tuple,
    ops: list[dict],
    previously_owned: set[Key],
) -> Decision:
    firsts = transcript_first_ms(ops)
    mine: dict[Key, list[Candidate]] = defaultdict(list)
    for op in ops:
        key = key_of(op)
        if key is None:
            continue
        r = rank(
            session,
            op.get("spawn_depth"),
            op.get("is_fork"),
            firsts[op.get("agent_id")],
            bundle,
            op["op_id"],
        )
        mine[key].append(Candidate(op["op_id"], bundle, r, op.get("duplicate_of")))
    keys = set(mine) | previously_owned
    store = _store_candidates(conn, keys, bundle)
    d = Decision()
    for key in keys:
        cands = mine.get(key, []) + store.get(key, [])
        if not cands:
            continue
        owner = min(cands, key=lambda c: c.rank)
        for c in mine.get(key, []):
            decided = None if c is owner else owner.op_id
            d.dup_of[c.op_id] = decided
            if c.duplicate_of is not None and c.duplicate_of != decided:  # adapters mark only what they saw
                d.disagreements += 1
                log.info("ownership hint %s: adapter %s, store %s", c.op_id, c.duplicate_of, decided)
        for c in store.get(key, []):
            decided = None if c is owner else owner.op_id
            if c.duplicate_of == decided:
                continue
            d.updates.append((c.op_id, decided))
            if c.duplicate_of is None:
                d.flips += 1
                d.affected.add(c.bundle)
            elif decided is None:
                d.promotions += 1
                d.affected.add(c.bundle)
    return d


def apply(conn: sqlite3.Connection, decision: Decision) -> None:
    demote = [(dup, op_id) for op_id, dup in decision.updates if dup is not None]
    promote = [(op_id,) for op_id, dup in decision.updates if dup is None]
    conn.executemany("UPDATE ops SET duplicate_of=? WHERE op_id=?", demote)
    conn.executemany("UPDATE ops SET duplicate_of=NULL WHERE op_id=?", promote)
