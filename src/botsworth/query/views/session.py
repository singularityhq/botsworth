"""CONTRACT: session blade views (owner Q2a): session header, turn list, row-id resolution, model lines.

GUARANTEES:
- `session_detail(session)` (QRY-29; API-20): `cost.main` / `cost.agents` split the owned llm_call cost by
  `agent_id IS NULL`; `cost.unattributed` = cost of owned llm_calls with `attr_turn_rowid IS NULL`; all
  three are None when the session has no owned llm_call (QRY-11). `time_split` is the session's
  `model_ms`..`none_ms`. `none_split.you|agentmsg|limit` = Σ of the session's `none` stretches with that
  label (QRY-41); `none` = `none_ms` minus those, so `before_first` counts under `none`. A `longest` target
  exists only at 2 min or more; `model`/`tool` target the longest main-thread owned call, the other five
  the longest stretch with that label (`none` covers `none` and `before_first`).
- `session_turns(session, min_gap_ms)` (QRY-30; API-21): every main turn (harness included) and every gap
  row, in time order. Turns sort by `start_ms` then `seq`, or `pre` when `seq` is NULL (Codex ops carry no
  `seq`; DM-24); a turn with a null start takes the position of the turn before it in that order. A gap row
  precedes the turn that starts at its `end_ms`. Gap rows are `none` stretches of at least `min_gap_ms` that
  end where a main turn starts or are labelled `before_first`.
  Turn figures: `wall_ms` = `span_wall_ms`, `time_split` = the four span columns, `waiting_ms` =
  `span_agents_ms + span_none_ms`, `cost_usd` = `attr_cost_usd`; all null for a turn with a null start.
  `n_agents` counts causing tool_calls (agent rows) of engagements whose cause is attributed to the turn;
  `agent_runs` lists those engagements. `n_failed` counts failed tool_calls attributed to the turn, any depth.
- `turn_row(conn, turn)` is one turn's `TurnRow`; `session_turns` builds equal rows in one batch.
- `resolve(session, id)` (QRY-38a; API-29) accepts an op_id, a provider request id or tool_use id (the suffix
  of an llm_call or tool_call op_id), an engagement op_id, or `gap-<start_ms>`. A tool_call that caused
  engagements and an engagement with a cause resolve to the `agent` row whose id is the first engagement of
  that cause (QRY-34). `depth` = engagement ancestors of the row (the cause for an agent row), at most 3.
- `model_lines(conn, where_sql, args)` (QRY-33): `where_sql` is a condition over `ops` (alias `o`) with `?`
  placeholders in `args`; it is combined with owned llm_calls (`kind = 'llm_call'`, `duplicate_of IS NULL`).
  One line per model, most costly first, then by model.
EXPECTS: schema v7 filled by B3a (`turn_kind`, `turn_origin`, `display_ix`, `attr_*`, `skill_load`) and B3b
  (`span_*`, session `model_ms`..`none_ms`, `stretches`).
FAILURE BEHAVIOR: unknown session -> NotFound; ambiguous or short session key -> QueryError (QRY-6);
  `min_gap_ms` under 60000 -> QueryError; an id that names no row, or a row that belongs to no turn
  (unattributed agent work, DM-45), or a non-main turn -> NotFound; a missing derived value that B3 always
  writes (a numbered turn without `display_ix` or `attr_n_llm_calls`) -> AssertionError.
DOES NOT: write; store or cache stretch labels (QRY-41); read `stretches.turn_rowid`; build SQL in `http/`.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from botsworth.query import Db, NotFound, QueryError, _conn, shapes, wire, zone
from botsworth.query.views.common import lookup_session, money, project_ref, session_name
from botsworth.query.views.status import failed_sql
from botsworth.query.views.stretches import display_id, stretches_for

MIN_GAP_MS = 60_000
LONGEST_FLOOR_MS = 120_000  # SES-27: a jump target exists only at 2 min or more
MAX_DEPTH = 3
_FAILED = failed_sql()  # QRY-37 (views/status.py; the Python form is rows.tool_status)
_PROV_RANK = ("present", "derived", "estimated")  # DM-47: weakest = highest rank


# ── QRY-33 ───────────────────────────────────────────────────────────────────


def model_lines(conn: sqlite3.Connection, where_sql: str, args: Sequence[object]) -> list[wire.ModelLine]:
    qualifies = "o.dur_ms > 0 AND o.out_tok IS NOT NULL"
    rank = "CASE o.time_prov WHEN 'present' THEN 0 WHEN 'derived' THEN 1 WHEN 'estimated' THEN 2 ELSE 3 END"
    rows = conn.execute(
        f"""
        SELECT o.model AS model, count(*) AS n_calls, count(o.cost_usd) AS n_priced,
               total(o.cost_usd) AS cost, total(o.dur_ms) AS model_ms,
               sum(CASE WHEN {qualifies} THEN o.out_tok END) AS out_tok,
               sum(CASE WHEN {qualifies} THEN o.dur_ms END) AS dur_ms,
               max(CASE WHEN {qualifies} THEN {rank} END) AS weakest,
               avg(o.ttft_ms) AS ttft_mean
        FROM ops o
        WHERE o.kind = 'llm_call' AND o.duplicate_of IS NULL AND ({where_sql})
        GROUP BY o.model
        ORDER BY cost DESC, o.model
        """,
        list(args),
    ).fetchall()
    lines = []
    for r in rows:
        if r["dur_ms"] is None:
            # FALLBACK-OK: interfaces.md QRY-33 (speed is null when no call qualifies); the basis
            # then reads 'missing', a time_prov value (data-model.md)
            speed, basis = None, "missing"
        else:
            assert r["weakest"] < len(_PROV_RANK), f"model {r['model']}: a timed call has time_prov missing"
            speed, basis = r["out_tok"] / r["dur_ms"] * 1000, _PROV_RANK[r["weakest"]]
        m = money(r["cost"], r["n_priced"] < r["n_calls"], r["n_calls"] - r["n_priced"])
        # FALLBACK-OK: none, spec gap: QRY-33 does not name a call without a model (Q2a gate)
        model = r["model"] if r["model"] is not None else "unknown"
        lines.append(
            wire.ModelLine(
                m.cost_usd, m.cost_lower_bound, m.n_unpriced, model, r["n_calls"], int(r["model_ms"]), speed,
                basis, r["ttft_mean"],
            )
        )  # fmt: skip
    return lines


# ── turns ────────────────────────────────────────────────────────────────────

_MAIN_TURNS = """
SELECT t.* FROM sessions s JOIN ops t ON t.parent_rowid = s.op_rowid AND t.kind = 'turn'
WHERE s.rowid = ? ORDER BY t.start_ms, COALESCE(t.seq, t.pre)
"""


def _order_key(r: sqlite3.Row) -> int:
    # FALLBACK-OK: QRY-30 (DM-24): `pre` orders turns when `seq` is NULL (Codex ops carry no `seq`)
    return r["seq"] if r["seq"] is not None else r["pre"]


def time_order(rows: Sequence[sqlite3.Row]) -> list[sqlite3.Row]:
    """QRY-30: `start_ms`, then `seq` (`pre` if NULL); a null start sits after the turn before it."""
    eff: dict[int, int] = {}
    last = -1
    for r in sorted(rows, key=_order_key):
        if r["start_ms"] is not None:
            last = r["start_ms"]
        eff[r["rowid"]] = last
    return sorted(rows, key=lambda r: (eff[r["rowid"]], _order_key(r)))


def _main_turns(conn: sqlite3.Connection, root: int) -> list[sqlite3.Row]:
    return time_order(conn.execute(_MAIN_TURNS, (root,)).fetchall())


def _turn_facts(conn: sqlite3.Connection, root: int) -> dict[str, dict]:
    """Per-turn aggregates for the session, keyed by turn rowid."""
    main: dict[int, dict[str, tuple[float, int]]] = defaultdict(dict)
    for r in conn.execute(
        """
        SELECT parent_rowid, model, total(cost_usd) AS cost, count(cost_usd) AS priced, count(*) AS n
        FROM ops WHERE root_rowid = ? AND kind = 'llm_call' AND duplicate_of IS NULL AND agent_id IS NULL
        GROUP BY parent_rowid, model
        """,
        (root,),
    ):
        # FALLBACK-OK: none — spec gap, as in model_lines (a call without a model)
        main[r["parent_rowid"]][r["model"] if r["model"] is not None else "unknown"] = (r["cost"], r["n"])
    unpriced = dict(
        conn.execute(
            """
            SELECT attr_turn_rowid, count(*) FROM ops
            WHERE root_rowid = ? AND kind = 'llm_call' AND duplicate_of IS NULL
              AND attr_turn_rowid IS NOT NULL AND cost_usd IS NULL
            GROUP BY attr_turn_rowid
            """,
            (root,),
        ).fetchall()
    )
    failed = dict(
        conn.execute(
            f"""
            SELECT attr_turn_rowid, count(*) FROM ops
            WHERE root_rowid = ? AND kind = 'tool_call' AND duplicate_of IS NULL
              AND attr_turn_rowid IS NOT NULL AND {_FAILED}
            GROUP BY attr_turn_rowid
            """,
            (root,),
        ).fetchall()
    )
    runs: dict[int, list[sqlite3.Row]] = defaultdict(list)
    causes: dict[int, set[int]] = defaultdict(set)
    for r in conn.execute(
        """
        SELECT c.attr_turn_rowid AS turn, c.rowid AS cause, e.op_id, e.agent_name, e.start_ms, e.end_ms
        FROM ops e JOIN ops c ON c.rowid = e.caused_by_rowid
        WHERE e.root_rowid = ? AND e.kind = 'engagement' AND c.attr_turn_rowid IS NOT NULL
          AND e.start_ms IS NOT NULL AND e.end_ms IS NOT NULL
        ORDER BY e.start_ms, e.op_id
        """,
        (root,),
    ):
        runs[r["turn"]].append(r)
        causes[r["turn"]].add(r["cause"])
    skills = _skill_loads_by_turn(conn, root)
    return {
        "main": main,
        "unpriced": unpriced,
        "failed": failed,
        "runs": runs,
        "causes": causes,
        "skills": skills,
    }


def _skill_loads_by_turn(conn: sqlite3.Connection, root: int) -> dict[int, int]:
    """SES-55: steps with `skill_load`, plus slash `skill_invocation` events whose turn has no such step."""
    out: dict[int, int] = defaultdict(int)
    for turn, n in conn.execute(
        "SELECT turn_rowid, count(*) FROM steps "
        "WHERE root_rowid = ? AND skill_load IS NOT NULL GROUP BY turn_rowid",
        (root,),
    ):
        out[turn] += n
    for turn, n in conn.execute(
        """
        SELECT t.rowid, count(*) FROM events ev JOIN ops t ON t.op_id = ev.op_id
        WHERE ev.root_rowid = ? AND ev.kind = 'skill_invocation' AND ev.copied = 0
          AND json_extract(ev.fields, '$.via') = 'slash' AND t.kind = 'turn'
          AND NOT EXISTS (SELECT 1 FROM steps s WHERE s.turn_rowid = t.rowid AND s.skill_load IS NOT NULL)
        GROUP BY t.rowid
        """,
        (root,),
    ):
        out[turn] += n
    return out


def _turn_row(turn: sqlite3.Row, facts: dict[str, dict]) -> wire.TurnRow:
    tid = turn["rowid"]
    models = sorted(facts["main"].get(tid, {}).items(), key=lambda kv: (-kv[1][0], kv[0]))
    cost_main = sum(c for c, _ in (v for _, v in models)) if models else None
    n_unpriced = facts["unpriced"].get(tid, 0)
    n_llm = turn["attr_n_llm_calls"]
    assert n_llm is not None, f"turn {turn['op_id']} has no attr_n_llm_calls (DM-45)"
    cost = money(turn["attr_cost_usd"], turn["attr_cost_lower_bound"], n_unpriced)
    spanned = turn["span_wall_ms"] is not None
    if spanned:
        split = wire.TimeSplit(
            turn["span_model_ms"], turn["span_tool_ms"], turn["span_agents_ms"], turn["span_none_ms"]
        )
    return wire.TurnRow(
        cost.cost_usd,
        cost.cost_lower_bound,
        cost.n_unpriced,
        turn=turn["op_id"],
        display_id=display_id(turn),
        kind=turn["turn_kind"],
        origin=turn["turn_origin"],
        label=turn["label"],
        start_ms=turn["start_ms"],
        end_ms=turn["end_ms"],
        wall_ms=turn["span_wall_ms"] if spanned else None,
        time_split=split if spanned else None,
        waiting_ms=split.agents_ms + split.none_ms if spanned else None,
        cost_main=cost_main,
        tok_total=turn["attr_tok_total"],
        n_llm_calls=n_llm,
        models=[wire.TurnModel(m, c) for m, (c, _) in models],
        n_agents=len(facts["causes"].get(tid, ())),
        n_skill_loads=facts["skills"].get(tid, 0),
        n_failed=facts["failed"].get(tid, 0),
        agent_runs=[
            wire.AgentRun(r["op_id"], r["agent_name"], r["start_ms"], r["end_ms"])
            for r in facts["runs"].get(tid, ())
        ],
    )


def turn_row(conn: sqlite3.Connection, turn: sqlite3.Row) -> wire.TurnRow:
    """One main turn's `TurnRow`. `turn` is an `ops` row (`SELECT *`) of a main turn."""
    return _turn_row(turn, _turn_facts(conn, turn["root_rowid"]))


def _is_gap_row(s: wire.Stretch, min_gap_ms: int) -> bool:
    """SES-36: a `none` stretch that ends where a main turn starts, or lies before the first numbered turn."""
    return (
        s.state == "none"
        and s.dur_ms >= min_gap_ms
        and (s.next_turn is not None or s.label == "before_first")
    )


def session_turns(session: str, min_gap_ms: int = 120000, db: Db = None) -> wire.TurnList:
    """QRY-30; API-21. `min_gap_ms` is at least 60000."""
    if min_gap_ms < MIN_GAP_MS:
        raise QueryError(f"min_gap_ms {min_gap_ms} is below the stored floor {MIN_GAP_MS}")
    with _conn(db) as conn:
        s = lookup_session(conn, session)
        root = s["rowid"]
        turns = _main_turns(conn, root)
        facts = _turn_facts(conn, root)
        keyed: list[tuple[tuple[int, int, int], wire.TurnRow | wire.GapRow]] = []
        eff = {t["rowid"]: i for i, t in enumerate(turns)}
        position = {t["start_ms"]: eff[t["rowid"]] for t in reversed(turns) if t["start_ms"] is not None}
        for t in turns:
            keyed.append(((eff[t["rowid"]], 1, 0), _turn_row(t, facts)))
        for g in stretches_for(conn, root, min_ms=min_gap_ms):
            if not _is_gap_row(g, min_gap_ms):
                continue
            # a gap sits above the turn that starts at its end; otherwise (before_first without a match)
            # at the first turn that starts after it
            before = position.get(g.end_ms)
            if before is None:
                before = next(
                    (eff[t["rowid"]] for t in turns if (t["start_ms"] or 0) > g.start_ms), len(turns)
                )
            keyed.append(((before, 0, 0), gap_row(g)))
        keyed.sort(key=lambda kv: kv[0])
        return wire.TurnList(wire.Freshness(**shapes.freshness(conn)), [item for _, item in keyed])


def gap_row(g: wire.Stretch) -> wire.GapRow:
    return wire.GapRow(
        g.start_ms,
        g.end_ms,
        g.dur_ms,
        g.state,
        g.label,
        g.turn,
        g.next_turn,
        g.running,
        id=f"gap-{g.start_ms}",
    )


# ── session header ───────────────────────────────────────────────────────────


def _longest_call(conn: sqlite3.Connection, root: int, kind: str, ref: str) -> wire.LongestTarget | None:
    r = conn.execute(
        """
        SELECT o.op_id AS op_id, o.dur_ms AS dur_ms, t.op_id AS turn
        FROM ops o LEFT JOIN ops t ON t.rowid = o.attr_turn_rowid
        WHERE o.root_rowid = ? AND o.kind = ? AND o.agent_id IS NULL AND o.duplicate_of IS NULL
          AND o.dur_ms >= ?
        ORDER BY o.dur_ms DESC, o.start_ms, o.op_id LIMIT 1
        """,
        (root, kind, LONGEST_FLOOR_MS),
    ).fetchone()
    if r is None:
        return None
    assert r["turn"] is not None, f"main-thread {kind} {r['op_id']} has no attributed turn (DM-45)"
    return wire.LongestTarget(wire.RowRef(ref, r["op_id"]), r["turn"], r["dur_ms"])


def _longest_stretch(stretches: list[wire.Stretch], labels: tuple[str, ...]) -> wire.LongestTarget | None:
    best = None
    for s in stretches:
        if s.label in labels and s.dur_ms >= LONGEST_FLOOR_MS and (best is None or s.dur_ms > best.dur_ms):
            best = s
    if best is None or best.turn is None:
        # FALLBACK-OK: interfaces.md QRY-29 (a target needs a turn; SES-37 gives none
        # when the session has no numbered turn)
        return None
    return wire.LongestTarget(wire.RowRef("gap", f"gap-{best.start_ms}"), best.turn, best.dur_ms)


def session_detail(session: str, db: Db = None, tz: str | None = None) -> wire.SessionDetail:
    """QRY-29; API-20. `session` is any key QRY-6 accepts; `tz` is the request zone (absent: the machine)."""
    # FALLBACK-OK: D50 — a request without a zone takes the answering machine's zone (QRY-3)
    tz = zone.resolve_tz() if tz is None else tz
    zone.zone_of(tz)
    with _conn(db) as conn:
        s = lookup_session(conn, session)
        root = s["rowid"]
        agg = conn.execute(
            """
            SELECT count(*) AS n, count(cost_usd) AS priced, total(cost_usd) AS cost,
                   total(CASE WHEN agent_id IS NULL THEN cost_usd END) AS main,
                   total(CASE WHEN attr_turn_rowid IS NULL THEN cost_usd END) AS unattributed
            FROM ops WHERE root_rowid = ? AND kind = 'llm_call' AND duplicate_of IS NULL
            """,
            (root,),
        ).fetchone()
        has_calls = agg["n"] > 0
        total = money(agg["cost"] if has_calls else None, agg["priced"] < agg["n"], agg["n"] - agg["priced"])
        cost = wire.SessionCost(
            total.cost_usd,
            total.cost_lower_bound,
            total.n_unpriced,
            main=agg["main"] if has_calls else None,
            agents=agg["cost"] - agg["main"] if has_calls else None,
            unattributed=agg["unattributed"] if has_calls else None,
        )
        stretches = stretches_for(conn, root)
        labelled = {k: 0 for k in ("you", "agentmsg", "limit")}
        for st in stretches:
            if st.state == "none" and st.label in labelled:
                labelled[st.label] += st.dur_ms
        assert s["none_ms"] is not None, f"session {s['op_id']} has no none_ms (DM-51)"
        none_split = wire.NoneSplit(**labelled, none=s["none_ms"] - sum(labelled.values()))
        longest = wire.Longest(
            model=_longest_call(conn, root, "llm_call", "call"),
            tool=_longest_call(conn, root, "tool_call", "tool"),
            agents=_longest_stretch(stretches, ("agents",)),
            you=_longest_stretch(stretches, ("you",)),
            agentmsg=_longest_stretch(stretches, ("agentmsg",)),
            limit=_longest_stretch(stretches, ("limit",)),
            none=_longest_stretch(stretches, ("none", "before_first")),
        )
        kinds = dict(
            conn.execute(
                "SELECT turn_kind = 'harness', count(*) FROM ops WHERE parent_rowid = ? AND kind = 'turn' "
                "GROUP BY 1",
                (s["op_rowid"],),
            ).fetchall()
        )
        n_agents = conn.execute(
            "SELECT count(DISTINCT agent_id) FROM ops WHERE root_rowid = ? AND kind = 'engagement'", (root,)
        ).fetchone()[0]
        n_failed = conn.execute(
            f"SELECT count(*) FROM ops WHERE root_rowid = ? AND kind = 'tool_call' AND duplicate_of IS NULL "
            f"AND {_FAILED}",
            (root,),
        ).fetchone()[0]
        name, title_source = session_name(s)
        info = wire.SessionInfo(
            project=s["project"],
            project_name=s["project_name"],
            color_slot=project_ref(conn, s["project"], tz).color_slot,
            session=s["op_id"],
            session_id=s["session_id"],
            provider=s["provider"],
            name=name,
            title_source=title_source,
            cwd=s["cwd_first"],
            git_branch=s["git_branch"],
            harness_version=s["harness_version"],
            entrypoint=s["entrypoint"],
            originator=s["originator"],
            start_ms=s["first_ms"],
            end_ms=s["last_ms"],
            ends_after_days=(
                date.fromisoformat(zone.local_day(s["last_ms"], tz))
                - date.fromisoformat(zone.local_day(s["first_ms"], tz))
            ).days,
            is_automated=bool(s["is_automated"]),
            n_turns=kinds.get(0, 0),
            n_harness_turns=kinds.get(1, 0),
            cost=cost,
            tok_total=s["incl_tok_total"],
            wall_ms=s["wall_ms"],
            active_ms=s["active_ms"],
            time_split=wire.TimeSplit(s["model_ms"], s["tool_ms"], s["agents_ms"], s["none_ms"]),
            none_split=none_split,
            longest=longest,
            n_agents=n_agents,
            n_skill_loads=sum(_skill_loads_by_turn(conn, root).values()),
            n_failed=n_failed,
            n_llm_calls=s["n_llm_calls"],
            models=model_lines(conn, "o.root_rowid = ?", (root,)),
        )
        return wire.SessionDetail(wire.Freshness(**shapes.freshness(conn)), info)


# ── QRY-38a ──────────────────────────────────────────────────────────────────


def _suffix_match(conn: sqlite3.Connection, root: int, kind: str, ident: str) -> sqlite3.Row | None:
    tail = "/" + ident
    return conn.execute(
        "SELECT * FROM ops WHERE root_rowid = ? AND kind = ? AND substr(op_id, -?) = ? "
        "ORDER BY duplicate_of IS NOT NULL, op_id LIMIT 1",
        (root, kind, len(tail), tail),
    ).fetchone()


def engagement_depth(conn: sqlite3.Connection, op: sqlite3.Row) -> int:
    n = conn.execute(
        "SELECT count(*) FROM ops WHERE root_rowid = ? AND kind = 'engagement' AND pre < ? AND post >= ?",
        (op["root_rowid"], op["pre"], op["pre"]),  # DM-15: `post` is the last `pre` in the subtree
    ).fetchone()[0]
    return min(n, MAX_DEPTH)


def _first_engagement(conn: sqlite3.Connection, cause_rowid: int) -> str | None:
    r = conn.execute(
        "SELECT op_id FROM ops WHERE caused_by_rowid = ? AND kind = 'engagement' "
        "ORDER BY start_ms, op_id LIMIT 1",
        (cause_rowid,),
    ).fetchone()
    return r["op_id"] if r is not None else None


def _turn_of(conn: sqlite3.Connection, op: sqlite3.Row, ident: str) -> str:
    if op["attr_turn_rowid"] is None:
        raise NotFound(f"row {ident!r} belongs to no turn (agent work with no causing turn)")
    return conn.execute("SELECT op_id FROM ops WHERE rowid = ?", (op["attr_turn_rowid"],)).fetchone()[0]


def resolve(session: str, id: str, db: Db = None) -> wire.Resolved:
    """QRY-38a; API-29."""
    with _conn(db) as conn:
        s = lookup_session(conn, session)
        root = s["rowid"]
        fresh = wire.Freshness(**shapes.freshness(conn))
        op = conn.execute("SELECT * FROM ops WHERE op_id = ? AND root_rowid = ?", (id, root)).fetchone()
        if op is None:
            op = _suffix_match(conn, root, "llm_call", id) or _suffix_match(conn, root, "tool_call", id)
        if op is None and id.startswith("gap-"):
            return _resolve_gap(conn, root, id, fresh)
        if op is None:
            raise NotFound(f"no row {id!r} in session {s['op_id']}")
        kind = op["kind"]
        if kind == "llm_call":
            return wire.Resolved(
                fresh, wire.RowRef("call", op["op_id"]), _turn_of(conn, op, id), engagement_depth(conn, op)
            )
        if kind == "turn" and op["parent_rowid"] == s["op_rowid"]:
            return wire.Resolved(fresh, wire.RowRef("turn", op["op_id"]), op["op_id"], 0)
        if kind == "tool_call":
            first = _first_engagement(conn, op["rowid"])
            row = wire.RowRef("agent", first) if first is not None else wire.RowRef("tool", op["op_id"])
            return wire.Resolved(fresh, row, _turn_of(conn, op, id), engagement_depth(conn, op))
        if kind == "engagement":
            cause = (
                conn.execute("SELECT * FROM ops WHERE rowid = ?", (op["caused_by_rowid"],)).fetchone()
                if op["caused_by_rowid"] is not None
                else None
            )
            if cause is None:
                return wire.Resolved(
                    fresh,
                    wire.RowRef("agent", op["op_id"]),
                    _turn_of(conn, op, id),
                    engagement_depth(conn, op),
                )
            first = _first_engagement(conn, cause["rowid"])
            assert first is not None
            return wire.Resolved(
                fresh, wire.RowRef("agent", first), _turn_of(conn, cause, id), engagement_depth(conn, cause)
            )
        raise NotFound(f"{id!r} is not a row of the session blade ({kind})")


def _resolve_gap(conn: sqlite3.Connection, root: int, ident: str, fresh: wire.Freshness) -> wire.Resolved:
    try:
        start = int(ident[len("gap-") :])
    except ValueError:
        raise NotFound(f"no row {ident!r}") from None
    for g in stretches_for(conn, root):
        if g.start_ms == start:
            if g.turn is None:
                raise NotFound(f"gap {ident!r} belongs to no turn (the session has no numbered turn)")
            return wire.Resolved(fresh, wire.RowRef("gap", ident), g.turn, 0)
    raise NotFound(f"no gap {ident!r}")
